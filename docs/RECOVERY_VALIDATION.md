# H3MLX and Qwen Image recovery validation

Restored on October 23, 2026 from the October 17 handoff.

## Comparison and preservation

The freshly cloned public `main` was still `761688a9` (`Improve generation composers and YuE adapter support`), the same base recorded by the handoff. All 14 tracked-file changes in `openmagia-uncommitted.patch` applied without conflicts. All five recovered new files were copied and compared byte-for-byte by SHA-256 before further changes:

- `qwen_image.py`
- `scripts/validate_h3mlx_registry.py`
- `docs/QWEN_IMAGE_INTEGRATION.md`
- `tests/test_qwen_image.py`
- `tests/qwen-image-browser.cjs`

The original handoff remains unchanged outside this checkout. It excludes old generated media and model weights; restoring this source does not restore those assets.

Two test corrections were added after restoration: the shared installer catalog expectation now includes `qwen-image`, and the mocked frame-seek test supplies mocked FFprobe discovery so it does not depend on the host's tools. No recovered application behavior was removed.

## Current-machine verification

- Python suite: 269 tests passed, zero skipped, with managed FFmpeg available on PATH.
- Python compilation, JavaScript syntax, shell syntax, and Git whitespace checks passed.
- Installed only the repository's managed FFmpeg runtime using `install.sh --no-h3 --no-models --no-formatter`.
- Launched the real editor at `http://127.0.0.1:8730`.
- Created a separate Recovery Review project, imported the existing official product sample, assembled a three-second timeline, and verified browser playback.
- Exported the timeline through the application's API and decoded the complete exported MP4 with FFmpeg without errors.
- Browser verified Qwen Image selection and its 512/1024 resolution controls, hidden H3-only controls, and model Settings accurately reporting no installed weights.
- The in-app browser does not support the application's native `prompt()` project-name dialog. The review project was created through the local application API; this browser limitation was not treated as an application recovery defect.

H3/H3MLX and Qwen weight downloads, native inference, actual model installation/removal, and image reference editing have NOT been rerun on this machine. Historical inference results in the Qwen integration document remain historical.

## Pinokio comparison

The companion public launcher is at `d01f0a3`. Its documentation references the same OpenMagia base `761688a`; it does not contain the uncommitted recovery work. It maintains lifecycle scripts and clones OpenMagia into `app/`, rather than duplicating application source. Its static Node validation passes. Full Pinokio installation/start/update tests have not been rerun here (Pinokio is not installed).

The launcher follows application `main`. A recovery-branch commit alone will not update Pinokio users: review and publish/merge the application change first, then validate the launcher against that revision. Large weights and private projects must remain untracked.

## Local model and creative workflow validation — 2026-10-23

- Qwen Image now uses the shared Models catalog, installed list, license sheet,
  and managed removal flow. Generated a studio image, performed an ordered
  two-image reference edit, and inspected the actual outputs. One visually
  incorrect edit was retained in Studies and excluded from the trailer.
- Installed and ran MiniMax H3 through both native h3.c and H3MLX engines.
  Both completed an 8-frame, 2-step first-frame smoke test. These square test
  clips are diagnostic takes, not final-quality or aspect-ratio examples.
- Built H3MLX after fixing the registry patch to target only the pipeline-name
  array. It no longer corrupts dispatch calls; the patch also repairs the known
  older corruption and is idempotent.
- Installed YuE 2 and generated a complete 44.56-second cue with an ABC score.
  Full audio decode passed; a separate listening mix was normalized to about
  -16 LUFS. The source output and score remain available in the local project.
- Prompt refinement produced an AI-assisted three-scene storyboard. Shared
  style, character references, and continuity controls were reviewed in browser.
- Cache/runtime defaults stay under this checkout's ignored addons directory.
  Readiness now checks all indexed checkpoint shards and VAE/text weights so an
  interrupted download cannot appear complete merely because metadata exists.
- Aspect-ratio review identified native H3's stretch behavior for opening frames.
  OpenMagia now prepares these anchors with a proportional center crop before
  passing them to H3. A rendered pixel-geometry test verifies that a square stays
  square when a landscape source is fitted to square output.
- Music progress now retains start time and updates the live scene status.
- Validation: 274 tests passed, zero skipped, using managed FFmpeg. No source,
  model, or generated output was pushed or published.

Generated Qwen, H3, and YuE material is a private evaluation draft, subject to
its respective model terms. Installer and inference logs, model revisions,
license snapshots, and media metadata are in ignored `data/launch-demo/`.
