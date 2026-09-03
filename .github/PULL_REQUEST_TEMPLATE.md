<!--
  Thanks for contributing to OpenMagia!
  Keep PRs focused; open an issue first for large or architectural changes.
-->

## What does this PR do?

<!-- One or two sentences: the user-visible outcome, not the implementation. -->

## Why is it needed?

<!-- Link issues with "Fixes #123", or describe the problem. -->

## How was it tested?

- [ ] `python3 -m unittest discover -s tests -t .` passes locally
- [ ] Manual pass in the browser (which flows? generate / edit / export)
- [ ] If preview compositing changed: verified against an ffmpeg export (WYSIWYG)
- [ ] No new runtime dependencies added (server stays stdlib-only)

## Screenshots / demo clip

<!-- Drag images or short clips here — required for anything visual. -->

## Checklist

- [ ] Deterministic prompt formatting still owns field order/limits (`h3_prompts.py`)
- [ ] Docs updated (README / inline docstrings) if behavior changed
- [ ] Heavy artifacts are still gitignored (models, h3.c, addons, projects, media)
