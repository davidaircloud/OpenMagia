# OpenMagia music production contract

Use this contract for every OpenMagia music skill. It records what the music
runtime can actually be told, so a skill never promises a control that the
request cannot carry.

## The runtime

OpenMagia generates music with **YuE 2** (`multimodal-art-projection/YuE`), an
AR–NAR mixture-of-transformers that turns style prose plus lyrics into an
editable symbolic plan and then into a full stereo song at 48 kHz. Upstream
validates it on a BF16-capable NVIDIA GPU; OpenMagia also measured it on Apple
Silicon MPS with `--backend torch-eager`, so the managed runtime runs on this
Mac and `yue_worker.py` is only for a machine you want to render on instead.
The CUDA-only accelerations (`vllm`, flash kernels, fp8) stay switched off
where they are not available.

## The whole request surface

One song per request. The fields below are the entire surface; anything a
skill cannot express inside them is not a direction, it is a wish.

- `style` (aliases `tags`) — one prose string: genre, instruments, vocal
  character, language, and intended tempo or feel. This is the only
  conditioning surface besides lyrics.
- `lyrics` — section-tagged words. Section tags such as `[Verse]`, `[Chorus]`,
  `[Bridge]`, `[Instrumental]` are the only structural control.
- `cot` — `full` (melody and chords planned, editable ABC out), `melody`
  (melody planned, accompaniment free), `off` (no symbolic plan).
- `abc` — an explicit ABC score to realize; requires `cot` of `full` or `melody`.
- `seed` — reproducibility. Same seed, request, and runtime reproduce the song.
- `cfg_scale` — how hard the model follows the text.
- `id` — the job name.

## What cannot be requested

- No duration, bar count, or key signature argument. The model decides how long
  the song is: a four-line lyric measured 54.8 s while an eight-line lyric
  measured 43.5 s, so length is reported as a band, never a number, and you trim
  it on the timeline afterwards.
- No BPM, reference audio, cover-from-a-recording, voice clone, phoneme,
  negative prompt, stem export, inpainting, or instrumental flag.
- No "no lead vocal" switch. An instrumental is stated with style-tag prose and
  instrumental section tags, which is a strong direction and not a guarantee.

Tempo, meter, and key belong in the style prose or in the ABC score, described
that way and never as a slider.

## Authoring discipline

- Preserve supplied lyric wording, spelling, capitalization, and line order
  exactly. Draft lyrics only when the artist supplied a brief and requested a
  draft; always leave that draft editable before generation.
- Section the lyric before generating; a wall of lines gives the model nothing
  to shape.
- Put every controllable choice in words: `88 BPM`, `downtempo`, `common time`,
  `D minor`, `brushed drums, upright bass`.
- One revision variable at a time, and record the seed. Two changes at once
  make the result unreproducible and unattributable.
- Expect an instrumental to sometimes carry a vocalise, and expect a long lyric
  to produce a long song. Report what happened instead of assuming.

## Review

Listen for: does the arrangement match the tags, are the words intelligible and
in the given order, does the chorus return where the section map says, is the
ending finished or cut off (the plan may report truncation), and does the song
sit at the intended tempo. Then change one tag, keep the seed, and compare.
