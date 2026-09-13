# YuE2 music generation — integration plan

Status: implemented (phase 1). This document is the design record for the first
music generation backend in OpenMagia.

## What OpenMagia looked like before

OpenMagia had exactly one generation backend and one media output.

- `MODEL_BACKENDS` contained only `h3-metal`. Settings, `/api/state.engine`,
  the model registry, the installer, and the composer all assumed "model"
  meant "the MiniMax H3 video engine".
- Generation meant a *scene* with `generation_type` of `video` or `image`.
  One worker (`run_job`) built a prompt with `h3_prompts.py`, launched `h3.c`,
  registered the result as media, and dropped a clip on the base video track.
- "Music" only existed *inside* a video prompt: `non_diegetic_music:` in the H3
  field structure, the `audio_mode` selector, and audio *references*. The
  composer's music model entry and the Skills → Music filter were deliberate
  placeholders that said "coming soon".

That placeholder is the seam this plan fills.

## What YuE2 is (verified against the upstream repository)

`multimodal-art-projection/YuE` (main branch) is **YuE2-3B**: an AR–NAR
mixture-of-transformers that turns *style tags + lyrics* into an editable
symbolic plan (melody/chords in ABC) and then into a full stereo song, decoded
by YuE2-Vae at 48 kHz.

The request surface is deliberately small, and OpenMagia must not invent
controls that are not there:

| YuE2 input | Meaning |
|---|---|
| `style` (alias `tags`) | genre, instruments, vocal character, language, intended tempo |
| `lyrics` | section-tagged words (`[Verse]`, `[Chorus]`) |
| `cot` | `full` (melody + chords), `melody` (melody, free accompaniment), `off` (no symbolic plan) |
| `abc` | an explicit score; requires `cot` of `full` or `melody` |
| `seed`, `cfg_scale`, `id` | reproducibility and text guidance |

There is **no** duration, BPM, reference-audio, phoneme, negative-prompt,
instrumental, or inpainting argument. Tempo and meter go in the style text or
the ABC. YuE2 runs on a BF16-capable NVIDIA GPU with 24 GB VRAM, one request at
a time, and reports progress on stderr as
`[YuE2] Running <stage label>: 512/4096 tokens (12%) | … elapsed 61.0s`.
The worker writes `audio.flac`, `score.abc`, `plan.json`, `result.json` (with
`truncated` flags), or `failure.json` in its job directory. OpenMagia imports
the finished audio and validated `score.abc` into the project. The remaining
runtime artifacts are not currently exposed as an in-app editing history.

## Design decisions

**1. Music is a second media kind, not a second app.**
A song is a scene with `generation_type: "music"`. That reuses the queue, the
single-active-worker guarantee (a song never contends with a video for the
GPU), progress polling, cancellation, media registration, the Asset Center, and
undo — instead of inventing a parallel job system. The differences are
localized: music scenes have no cast, no references, no canvas, and their clip
lands on an **audio** track.

**2. The backend registry grows a `media` axis.**
Every `MODEL_BACKENDS` entry now declares `media: "video" | "music"` and a
`role`. `model_management_state()`, discovery, and selection become role-aware,
so "installed / in use" means *in use for this role*.

**3. Honest about hardware.** YuE 2 runs on this Mac. An early reading of the upstream
README ("Linux / NVIDIA / 24 GiB") took that for a requirement; it is the *validated*
configuration, and the code resolves `cuda -> mps -> cpu`. OpenMagia advertises only what
it can execute end to end - so that rule decided nothing here, the runtime did, by
generating audio on MPS. Both runtimes are offered, and OpenMagia executes both:

- **Managed local runtime** - `install.sh --with-yue` creates `addons/yue/runtime` and
  installs the pinned upstream package (plus, on request, `m-a-p/YuE2-3B` and
  `m-a-p/YuE2-Vae`). Selection requires readiness reported by `yue2 doctor`, so the card
  appears when torch, MPS and the weights are actually present.
- **Remote YuE 2 worker** - OpenMagia ships `yue_worker.py`, a stdlib-only HTTP server
  that runs inside a YuE 2 environment on any machine, CUDA or Apple Silicon, and speaks a
  small job protocol. It mirrors the existing "connect a local model server" pattern used
  for prompt refinement, and stays the path for long-form work on a CUDA box.

Every surface states the device the runtime reported - `mps (Apple Silicon)`,
`cuda · NVIDIA GeForce RTX 3090 · 24 GiB` - never a guessed one.

**4. OpenMagia owns the request structure, exactly like it owns H3's.**
`yue_prompts.py` is to music what `h3_prompts.py` is to video: it compiles the
user's idea + the selected skill contract into a valid YuE2 request, preserves
lyrics verbatim, normalizes section tags, refuses `abc` with `cot: off`, and
returns an honest duration *estimate* because the model has no duration
control. The skill contract is recompiled at execution time so a stale draft
cannot bypass it.

**5. Skills stay one catalog.** Music skills are catalog entries with
`"type": "music"` and a machine contract, so the existing Skills → Music filter,
detail sheet, and contract compiler all work. A music skill never pretends to
control duration or a reference recording.

## Pieces

| File | Role |
|---|---|
| `yue_prompts.py` | Request compiler, lyrics normalization, validation, progress parser, duration estimate |
| `yue_worker.py` | Standalone YuE 2 worker for any device, local or remote (health, job create/status/audio/cancel, token ceiling) |
| `server.py` | Music backend registry, runtime resolution, music scenes, `run_music_job`, `/api/music/*` |
| `install.sh` | Opt-in `--with-yue` runtime + weights install |
| `skills/openmagia/*` | `song-director`, `score-underscore`, `album-identity` music skills |
| `index.html` / `app.js` / `style.css` | Music composer, model picker, Settings → Music, Skills |
| `tests/test_music_generation.py` | Compiler, registry, scene validation, command and payload builders |

## Music job path

1. Composer (`Generate → Music`) posts a music scene: style description, lyrics,
   plan mode, optional ABC, seed, and the no-lead-vocal direction.
3. `POST /api/scenes/<id>/generate` enqueues it on the shared queue.
4. `run_job` routes `generation_type == "music"` to `run_music_job`, which
   resolves the runtime (`local` | `endpoint`), compiles the request, persists
   `execution_prompt` + `music_request` for auditability, and runs it.
5. Progress comes from the same `[YuE2]` lines / worker status and appears in
   the existing generation progress card.
6. The finished audio is stored as the FLAC the runtime produced (48 kHz stereo,
   no re-encode, no quality loss), registered as `kind: "audio"`, and clipped
   onto the first audio track (created when needed) after existing audio. The
   planned `score.abc` is first checked with YuE's native-dialect inspector, then
   saved beside the media as `media/gen-<id>.abc`, and the
   compile audit - what left the lyric, what was moved to style, the length band -
   is stored on the media record under `generation.music`.
7. Failure keeps the scene in `error` with the upstream reason; the worker's
   `failure.json` message is surfaced rather than a generic exit code.

## What shipped (2026-09-11)

- **HTTP surface**: `GET /api/music/state` (fresh runtime probe), `POST
  /api/music/preview` (compile only - what YuE 2 will receive, what was removed,
  what cannot be honoured), `POST /api/music/stop` (stop the managed local
  worker). `/api/state` carries `engine.music`, so the composer and Settings read
  the same object.
- **Composer**: `Generate → Music` renders only the knobs the model has, plus a
  live compile sheet. The type selector's music option is no longer a "coming
  soon" notice; when no runtime is ready the notice says what is missing and links
  to Models.
- **Settings → Music**: install the managed runtime, point at a remote
  `yue_worker.py`, or fall back to this machine; it also lists what the model
  cannot do, so the limit is visible where the button is.
- **Length is a band, never a number.** Measured: a four-line lyric rendered 54.8 s
  while an eight-line lyric rendered 43.5 s. Any single "estimated duration" would
  have been theatre, so `estimated_band` (low, high) drives the wording "YuE 2 sets
  the length · expect roughly X–Y s".
- **Runaway guard**: the worker takes `--max-tokens` (default 6000, about four
  minutes of audio). A piece that never terminates fails with a stated reason
  instead of being killed by the OS after eight minutes with nothing to show.
- **Skill type guard**: attaching a shot-writing skill to a music scene raises
  instead of letting a story formula rewrite the lyric.

## Deliberate limits (phase 1)

- One song per job; no best-of-N, no batch queue per song.
- No transcription/cover chain (SheetSage2/MERT2) — the ABC field is the manual
  door into `cot: melody` covers.
- No lyric writing by the refinement model unless the user asks for lyrics;
  existing lyrics are never rewritten.
- "No lead vocal" is a *style direction*, not a hard control, and is labelled
  that way because YuE2 exposes no instrumental parameter.

## Measured on Apple Silicon (2026-09-10)

Managed runtime installed by `install.sh --with-yue` into `addons/yue/runtime`
(Python 3.12 + the pinned upstream package), weights from the public snapshots, served by
`yue_worker.py`, driven through its real HTTP contract:

| Cue | Plan | Tokens | Wall | Output |
| --- | --- | --- | --- | --- |
| 8-line lyric, `song-director` skill | full | 1,011 | ~140 s | 43.5 s FLAC, 48 kHz stereo, `truncated: false` |
| Instrumental cello cue, `score-underscore` | off | 3,226 | ~190 s | 135.6 s FLAC |
| Instrumental, same prompt, shorter run | off | 901 | 49.7 s | 36.0 s FLAC |
| Instrumental with an ABC-less `melody` plan | melody | 7,385+ | 285 s | killed by the OS, no output |

The same seed re-renders the identical song: two separate server runs of one
four-line lyric both produced 54.758666… s of audio. Seed, description, lyrics and
plan mode are the whole reproducibility contract, which is why the composer keeps
them visible and offers no hidden variation.

What those measurements changed in the code, not just in the prose:

- Semantic rate is 23-26 tokens/s on MPS at roughly 23 tokens per second of audio, so
  duration estimates are calibrated against a measurement.
- Full and Melody can plan a new score without supplied ABC. Supplying a complete
  native YuE score bypasses that symbolic planning and requires Full or Melody.
  Wordless generation remains seed-sensitive and has no duration control; section
  labels describe form but do not guarantee length or a valid ending.
- `yue_worker.py --max-tokens` (default 6,000, about four minutes) stops a runaway cue and
  fails the job with the reason. Without it the process dies in an OOM kill and the
  artist sees only a spinner.
- YuE 2 decodes audio only at the end, so cancelling never keeps partial audio. The UI
  states that instead of implying a keep-what-you-got safety net.

## Editing boundary

YuE's documented editing workflow preserves the original request and score, edits a
copy of `score.abc`, validates and compares it with `abc_tools.py`, then renders a new
complete recording with `cot: full`. Even unchanged notation can produce a different
performance. OpenMagia currently supports supplying a complete ABC score as generation
input and saves the generated score beside the audio; it does not yet provide score
editing, source-versus-edit comparison, or versioned full artifact directories in the
UI. A saved score alone should not be described as a complete editing implementation.
