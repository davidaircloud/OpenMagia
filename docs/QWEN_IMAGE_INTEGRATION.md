# Qwen image integration — October 16, 2026

Qwen-Image-2.1-Turbo is available in Generate → Image → Image model. Settings → Image generation installs its isolated Python runtime and weights. H3 still-frame generation remains selectable. Restart the OpenMagia server after updating its source.

The installer uses `uv`, Python 3.12, PyTorch, Transformers, Accelerate, Pillow and Diffusers. Weights occupy approximately 30 GiB, plus runtime dependencies. Model revision: `fc13ba1986fcb905854dc8de2f52eee395f49b5b`; tested Diffusers revision: `d42ebf9c6e0b46103b74283f5324357a67acaa04`. The model's saved eight-step sigma schedule is used without replacing it. The process exits after each image, releasing model memory. Installation errors are retryable from Settings.

Text-to-image and image-conditioned editing share the pipeline. Existing Cast and image reference selections are passed as ordered image inputs. Video continuation and audio references are rejected. Project style is prepended when enabled. Model identity, prompt, dimensions and seed are retained on generated media. Failed jobs update the scene and pending media and release the generation queue.

## Verification

Host: Apple M5 Max, 128 GB unified memory. Isolated browser server on port 8732; test projects under `/tmp/openmagia-qwen-tests`, separate from the user's active project.

- Browser model selection, hidden H3 quality controls, Settings installer and Installed state: verified.
- 512×512 red teapot from text: approximately 20 seconds including process startup and model loading.
- Reference edit changing the teapot from red to blue: approximately 20 seconds; shape/composition retained.
- 1024×1024 yellow mug from text: approximately 21 seconds with untiled decoding.
- Final untiled browser rerun: 512×512 text generation and reference edit both completed in approximately 15 seconds; outputs visually checked.
- Eight denoising steps confirmed in runtime logs.
- 28 relevant Python tests pass, covering existing queue/installer/composer behavior and new Qwen dimension normalization, readiness and failure recovery.

Optional VAE tiling produced visible vertical seams at 512 and 1024 pixels. Full-frame decoding removed those seams in a same-prompt, same-seed 1024 test. Tiling is therefore disabled. These timings are individual checks, not a controlled comparison against H3 or a guarantee for larger resolutions/multiple references. The UI offers 512 and 1024 short-side presets; 2K was not benchmarked.

`tests/qwen-image-browser.cjs` runs real generation and a reference edit against a dedicated test server. Requires Playwright (or `PLAYWRIGHT_MODULE` pointing to an existing installation). It must not target a user's active project.

## Sources and terms

- https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo
- https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE
- https://github.com/QwenLM/Qwen-Image-2.1

Weights use the Qwen Research License, for noncommercial evaluation; commercial use needs a separate license. The Settings install action displays this condition and links the full license.

FastH3-Omni was researched separately, not installed or advertised as working locally. Reactor documents text, reference images/audio and first/last frames. Local weights are announced for October 20. Existing FastH3 MLX support does not establish local Omni support, and the documented FastH3 MLX server adapter does not expose reference inputs:

- https://docs.reactor.inc/model-api-reference/fast-h3-omni/overview
- https://nuvalab.ai/blog/fasth3-omni-minimax-h3-reference-audio-video/
- https://haoailab.com/FastVideo/cookbook/openai-api/
