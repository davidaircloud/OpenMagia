# OpenMagia: local AI operating and monitoring guide

Prepared September 15, 2026 for this installation. Recheck code and live state after updates.

## Read this first: current repair status

Music generation is NOT yet proven reliable. Do not report it fixed merely because unit tests pass or a request is queued.

Confirmed problems and repairs:

- Music completion did not clear `active_job`, which could strand subsequent songs. The queue-release repair is running.
- Invalid ABC plans were decoded before validation. `yue_generate.py` now checks the native plan before semantic/acoustic work and checks major early semantic endings before acoustic synthesis. `yue_worker.py` launches this wrapper. It delegates health checks to the native CLI.
- Worker failure receipts use `reason`; the wrapper now reads that field instead of only `message`.
- Full score planning still produces malformed scores on some requests. Three retries can all fail. This remains an unresolved model/output-quality problem.
- A stall-timeout correction is ON DISK but was written after the current server started. Load it at the next safe restart. Previously every successful poll reset the timer, even without progress.
- The server was started in a persistent terminal session, not confirmed as an independently durable launchd service. Verify survival if its host application closes.
- Midnight Protocol exhausted three preflight score attempts during the current regeneration. Velvet Bandwidth started next, demonstrating queue handoff. Refresh live state for subsequent results.
- Low Orbit and its queued successors were accidentally removed by the app's cancellation endpoint, then restored from the full pre-cancellation snapshot. All nine affected songs were requeued. Completed media was preserved.
- Earlier claims that the worker had disappeared were not established: sandboxed curl failed while host-level process inspection showed a listener. Never infer a crash from a sandbox network error.
- SIGKILL means externally terminated; it does not prove an out-of-memory kill. Unsloth's model server was running and using substantial memory. No causal attribution to Unsloth has been proven.

## Installation and state

```text
Repository: /Users/davidprom5/Documents/2026/09/OpenMagia
Editor/API: http://127.0.0.1:8730
YuE worker: http://127.0.0.1:8931
Active project: projects/hackathon-background-music/project.json
Saved media: projects/hackathon-background-music/media/
Worker runs: data/music/YYYYMMDD/<request-folder>/<song-id>/
Worker log: data/music/worker.log
Launcher log: data/runtime/server.log
Configuration: config.json
Active project pointer: data/active.json
YuE Python: addons/yue/runtime/bin/python
```

The foreground server may log to its terminal rather than `server.log`. Scene IDs and worker job IDs differ. The worker directory suffix also differs from the public job suffix; match requests, timestamps and scene `music.job`, not suffix guesses.

## Copy this instruction to your local AI

> Monitor OpenMagia's active project and YuE worker every 30–60 seconds. Keep a compact table of scene ID, name, status, worker job, phase, completed/total, last progress change and error. Report only completion, terminal failure, a verified stall, or required intervention. Do not regenerate, cancel, restart, edit project JSON, alter prompts, lower quality or stop other applications merely because you are monitoring. If I authorize repair/regeneration, preserve completed media and original lyrics, use the procedures below, and verify one real output and the next queue handoff before scaling up. Do not call the scene cancellation endpoint to pause a queue: it deletes scenes and can delete successors. Keep a short persistent status file so another AI can resume without rereading logs. A successful HTTP response, `status=complete` receipt, or passing unit test is not proof of a usable generation.

## What to watch

Run these from the repository in the host terminal. `jq` is used for compact output.

```bash
cd /Users/davidprom5/Documents/2026/09/OpenMagia
curl -fsS --max-time 10 http://127.0.0.1:8730/api/state |
  jq '{slug,scenes:[.scenes[]|{id,name,status,error,progress,music}]}'
curl -fsS --max-time 10 http://127.0.0.1:8931/health | jq .
tail -n 20 data/music/worker.log
lsof -nP -iTCP:8730 -iTCP:8931 -sTCP:LISTEN
```

`GET /api/state` also invokes queue recovery; it is not strictly side-effect-free. For a passive disk-only check, read `project.json`, but it does not contain all transient token/step counts.

For a known worker ID, replace JOB_ID with the actual returned value:

```bash
curl -fsS --max-time 10 http://127.0.0.1:8931/v1/music/JOB_ID | jq .
```

### Healthy progression

YuE: queued → starting → planning score → generating song (semantic tokens) → synthesizing audio (usually 32 steps) → decoding → validation → ready/imported.

- Compare `(job ID, phase, completed, total)` across samples. A phase transition is progress even if the new count resets to zero.
- A spinner or unknown total alone is not a stall. Long acoustic steps can be slow on MPS.
- Use your own monotonic timestamp for the last changed tuple. UI elapsed/ETA fields are not reliable for every music phase.
- After a terminal result, the next queued scene should start within ordinary polling/boot latency. If the worker is idle but a finished scene still occupies the app queue, investigate slot release.
- `ready=false` during worker startup can mean imports/model checks are in progress. A missing worker while idle is normal; the server starts it on demand.

### Escalation criteria

| Observation | Interpretation and action |
|---|---|
| Same job/phase/count for 15 minutes | Suspected stall; inspect process, memory and logs. Preserve evidence before stopping anything. |
| API unreachable but listener exists | Check host versus sandbox network access before claiming a crash. |
| Worker busy while app has no matching live job | Possible orphan. Inspect the worker job and artifacts; do not launch a second worker on another port. |
| HTTP 409 worker busy | Wait for the slot. It is not a model-quality failure. |
| Invalid ABC / truncated plan | Candidate failed; inspect retry count and whether validation happened before synthesis. |
| SIGKILL / exit -9 | External kill; inspect OS evidence and memory pressure. Do not assert Unsloth caused it. |
| All three attempts fail | Stop repeating identical batches. Save seeds, requests, scores, errors and runtime identity for diagnosis. |
| Disk audio exists but scene still running | Check receipt, worker terminal state and import/coordinator path. Never overwrite the project from an old snapshot. |

Useful host diagnostics: `vm_stat`, `memory_pressure`, Activity Monitor, and `ps -axo pid,ppid,etime,rss,command`. RSS alone does not describe all Metal/unified-memory allocations. Never use `killall python` or terminate Unsloth without authorization.

## Generating songs

Use YuE's actual inputs: style, lyrics, planning mode, seed, optional native ABC, and optional guidance. There is no exact-duration slider in this integration. BPM and key in the style are musical requests, not guaranteed controls.

### Dictation template: vocal song

> In project [name], create one song titled [title]. Style: [genre], [instruments], [voice], [mood], approximately [BPM]. Use full planning and default guidance. Preserve these exact lyrics and section labels: [lyrics]. Generate one candidate batch, verify the whole audio including the ending, and report the output path, duration and validation. Do not change the lyrics or switch to Direct automatically.

### Dictation template: instrumental

> Create [title], an instrumental [genre/mood] with [instruments], around [BPM], no lead vocal and no sung words. Set instrumental=true explicitly. Use full planning with section-only lyrics: Intro, Instrumental, Interlude, Breakdown, Instrumental, Outro. Treat this as a requested form, not a guaranteed cure for malformed plans. Keep default sampling and decoder quality. Report failure if all bounded attempts fail.

Section-only lyric text:

```text
[Intro]
[Instrumental]
[Interlude]
[Breakdown]
[Instrumental]
[Outro]
```

`full` plans melody/chords; `melody` plans melody; `off` bypasses symbolic planning and changes the generation method. Never switch modes silently to manufacture a success. Preserve user-supplied structured lyrics. Avoid production notes inside sung lyrics.

### Compile before spending GPU time

POST `/api/music/preview` compiles the actual request without generation. Example body:

```json
{
  "name": "Evening Circuit",
  "prompt": "Warm downtempo lounge, Rhodes, rounded bass, soft percussion, around 96 BPM, instrumental, no lead vocal, no sung words",
  "params": {
    "instrumental": true,
    "plan_mode": "full",
    "seed": 12345,
    "guidance": null,
    "lyrics": "[Intro]\n[Instrumental]\n[Interlude]\n[Breakdown]\n[Instrumental]\n[Outro]"
  }
}
```

Inspect returned `request`, `sections`, `instrumental` and warnings. Do not confuse the app's `params.plan_mode` with native YuE's `cot` field.

To create a scene, POST `/api/scenes` with the same name/prompt/params plus `generation_type:"music"`, `character_ids:[]`, `reference_media_ids:[]`, `source_media_id:null`. Creating a scene does not start it. Use its returned ID with POST `/api/scenes/ID/generate`. Use this endpoint to retry an existing failed scene instead of creating duplicate tiles. Check current status first and submit once.

Do not attach Cast, frame continuation or audio reference inputs to a YuE song: this integration does not support them. Do not depend on a specific old scene ID without checking the current project.

### Completion evidence

Require scene and media `ready`, a nonempty imported audio file, sensible probed duration, and a receipt. Inspect both `truncated.abc` and `truncated.semantic`; upstream `status:"complete"` can coexist with truncation.

For planned music, inspect the native ABC using the shipped tool:

```bash
addons/yue/runtime/bin/python addons/yue/YuE/skills/yue2-music/scripts/abc_tools.py inspect /absolute/path/to/score.abc
```

The app currently rejects audio below 80% of nominal planned duration. This is an OpenMagia heuristic, not an upstream guarantee. A syntactically valid score and sufficient duration still do not establish musical quality. Listen to the beginning, middle and final 15–30 seconds; ideally review the whole piece. Check for abrupt endings, silence, distortion, repeated fragments, omitted lyrics and vocals in an instrumental. Do not fabricate a listening assessment when your local AI has no audio capability.

Official generation contract: https://github.com/multimodal-art-projection/YuE/blob/main/docs/generation.md
Local copy: `addons/yue/YuE/docs/generation.md`.

## Generating videos

OpenMagia uses H3 for video. Use the app's prompt formatter or the installed H3 schema, rather than inventing prompt fields.

### Dictation template

> In [project], create a [seconds]-second [aspect ratio] video. Subject: [identity and appearance]. Setting: [place/time]. Action: [one clear sequence]. Camera: [framing and movement]. Lighting/style: [description]. Sound: [dialogue, effects, music or silence]. Preserve [specific continuity details]. Use [actual selected reference media/Cast]. Generate one scene, inspect the first, middle and last frames and playback, then continue only if identity and motion are correct.

- POST `/api/prompt/format` can format an `idea`, `answers`, `frames`, and selected reference IDs; use returned prompt/frames and inspect them before creation.
- POST `/api/scenes` with `generation_type:"video"`, prompt, name and validated `params`; then POST `/api/scenes/ID/generate`.
- Current app frame rate is 24 fps, with 8–360 video frames (up to 15 seconds). Use returned normalized settings.
- Current allowed sizes: 512×512, 512×896, 896×512, 768×768, 768×1344, 1344×768, 1024×768, 768×1024.
- Current clamps: steps 2–60, layers 35–50, reuse 1–3. Valid quality modes: balanced/high/reference. Preserve the user's quality choice; these ranges are not quality recommendations.
- Audio modes: effects/full/dialogue/silent.
- Explicitly pass `character_ids:[]` if no Cast is intended; scene creation otherwise defaults to project Cast.
- Inspect the actual last frame before using it as a continuation anchor. Do not propagate deformed faces, missing limbs, hidden defining features or broken product geometry.
- Current reference limits: up to 9 images, 3 audio references, 12 mixed files. Audio references need at least one visual reference; each is 2–15 seconds and total audio is at most 15 seconds. Check actual backend validation after updates.
- A frame-continuation anchor cannot be combined with audio references in this integration.

Local detail: `docs/CONTINUITY_PROMPTING.md`, `h3_prompts.py`, `clamp_generation_params` and reference validation in `server.py`.

## Generating images

### Dictation template

> Create one image of [subject], [composition], [lighting], [materials/style], [aspect ratio], using [selected references]. Keep [identity/product details] exact. Use OpenMagia's Image mode. Verify the saved image at full size, including hands, faces, text and crop. Do not call it complete until the actual file is present and visually checked.

Use POST `/api/scenes` with `generation_type:"image"`, then its generate endpoint. The current implementation renders five H3 frames and extracts the middle frame as the still; it is not a separate text-to-image model. The server forces five frames. Use supported dimensions and the user's quality setting. Images cannot use audio references. Verify the extracted file is an image and that the library card is typed correctly.

## Repair and restart procedures

### Before changing or stopping anything

1. Read repository instructions and `git status`; preserve unrelated user changes.
2. Save a timestamped copy of the current project JSON and relevant requests/receipts outside the active file. Use a fresh backup filename.
3. Record current API state, worker job ID, progress, process IDs and command lines.
4. Prefer letting an advancing generation finish. A server restart can orphan a separate YuE worker and lose the importer waiting for its result.
5. Do NOT use POST `/api/scenes/ID/cancel` as a pause operation. In this checkout it removes the scene, its media records, and potentially queued successors. Do not use broad kill commands or replace project JSON while the server is writing it.

### Reloading repaired code

- Editing Python source does not update an already running process.
- Launcher checks compare the static VERSION value. Same version does not prove the running source matches disk.
- Server and YuE worker are separate processes. Restarting only one may reuse a stale other process because the protocol version remains compatible.
- For an idle worker, POST `http://127.0.0.1:8931/shutdown` shuts down the worker service. Inspect health first; shutdown alone is not a safe way to handle active work.
- To intentionally abandon an authorized active worker attempt, DELETE `/v1/music/JOB_ID` directly at port 8931, then confirm termination and preserve artifacts. Coordinate the app's scene state. This is distinct from the destructive app scene-cancel endpoint.
- Run `./start.sh --restart` from the repository when safe. Verify port 8730 and API state afterward; do not trust its success message alone.
- If background/launchd startup does not survive, use a normal Terminal window: `cd /Users/davidprom5/Documents/2026/09/OpenMagia` then `./start.sh`. Keep that Terminal open. Do not start it while another process owns 8730.
- Worker starts on demand. Verify its command line contains `yue_generate.py generate` for generated attempts, and ensure only one active music subprocess exists.
- If local HTTP fails inside an agent sandbox, request/use permitted host access. Never bypass a denied approval. A failed sandbox probe is not evidence to kill a listener.

### Verification after repairs

Run focused checks:

```bash
addons/yue/runtime/bin/python -m unittest tests.test_music_generation tests.test_generation_queue
addons/yue/runtime/bin/python -m py_compile server.py yue_worker.py yue_generate.py
git diff --check
```

Then verify a real generation and queue transition. Tests do not prove musical quality, memory stability or server longevity. Compare exact submitted payloads with intended settings. For each repair, record whether it is only on disk, loaded in the server, loaded in the worker, tested with mocks, or verified by real media.

## Compact reports that save tokens

Keep one short local status note and update only meaningful changes:

```text
Time:
Project:
Server PID / worker PID:
Active scene / worker ID:
Phase and count:
Last progress change:
Ready / running / queued / failed totals:
Latest failure and attempt count:
Next action:
Files changed but not loaded:
Output paths actually verified:
```

Send the user a sentence when a song is ready, fails all attempts, or stalls. Avoid streaming token counts, whole JSON payloads or repeated unchanged logs. If every candidate fails, report that plainly; do not say the regeneration was completed just because it was started.
