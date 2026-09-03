# Contributing to OpenMagia

Thanks for wanting to make OpenMagia better! This document gets you from
clone to merged PR with minimum friction.

## Project philosophy (read this first)

1. **One folder, no dependencies.** The server is Python stdlib only; the UI
   is one HTML page + vanilla JS/CSS with zero npm packages. If your change
   adds a dependency, you need a very good reason.
2. **Editing is the center of gravity.** Generation serves the timeline, not
   the other way around. Features that turn the app into a chat client will
   be declined.
3. **Correctness never depends on model output.** Deterministic code owns
   field names, ordering, limits, and timing (`h3_prompts.py`). Local LLMs may
   only expand prose.
4. **What you see is what you export.** Canvas preview math and ffmpeg export
   must stay in lockstep — if you touch one, verify the other.

## Development setup

Prereqs: macOS on Apple Silicon, Python 3.10+, `make`, `cmake`, `ffmpeg`,
and the Hugging Face CLI.

```sh
git clone <your-fork> OpenMagia && cd OpenMagia
git clone https://github.com/antirez/h3.c          # the engine
./install.sh                                       # models, formatter, config.json
./start.sh                                         # http://localhost:8730
```

Working without checkpoints? The UI still runs; generation and export simply
stay disabled until `config.json` points at a working `h3` binary.

## Where things live

| Path | What it is |
|---|---|
| `server.py` | HTTP server: projects, media library, generation queue, export endpoints |
| `nle.py` | ffprobe/thumbnails, clip pre-rendering, ffmpeg timeline composition |
| `h3_prompts.py` | Style presets + deterministic H3 prompt formatting/validation |
| `index.html` / `app.js` / `style.css` | The entire frontend (no build step) |
| `skills/openmagia/` | Production skills surfaced in the Skills view |
| `tests/` | Python unit tests (prompt formatting) |

## Making changes

```sh
# run the test suite before every commit
python3 -m unittest discover -s tests -t .

# keep style consistent
#   Python: stdlib only, PEP 8-ish, module docstrings like nle.py
#   JS/CSS: no frameworks, no transpiler, small pure helpers, kebab-case ids
```

If you change preview compositing or transitions, also sanity-check an export
of a two-clip timeline with a dissolve — WYSIWYG regressions are the ones we
fear most.

## Submitting

1. Branch from `main`: `git checkout -b feat/my-feature`.
2. Make commits small and descriptive (imperative mood, e.g.
   `Add per-track mute to export mix`).
3. Open a Pull Request against `main` using the provided template. Include
   screenshots or a short clip for anything visual.
4. Reference issues with `Fixes #123` where applicable.

For bugs and feature requests, use the issue templates — reproduction steps
and console/server-log excerpts make everything faster.

## Code of conduct

Be kind and constructive. By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Licensing

By contributing you agree your work is released under the repository's
[GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`).
Third-party components keep their own licenses — see
[NOTICE.md](NOTICE.md).
