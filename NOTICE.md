# Notices & Third-Party Licenses

OpenMagia — a local, single-view visual editor for AI video.
Copyright © 2026 David Vera (@davidaircloud)

OpenMagia itself is licensed under AGPL-3.0-only. The notices below describe
separately licensed third-party components and model dependencies.

This repository contains only OpenMagia's own code. The heavy components it
orchestrates (an inference engine, model checkpoints, a prompt-formatting
runtime) are cloned or downloaded at install time by `install.sh` and are
*not* distributed with this repository. They are credited below because
OpenMagia would not exist without them.

## Components

### h3.c — native MiniMax-H3 inference for Apple Silicon
- Upstream: <https://github.com/antirez/h3.c>
- License: MIT License — Copyright (c) 2026 Salvatore Sanfilippo
- Role: the generation engine. Prompt-to-video/audio, first/last-frame
  conditioning, and ordered reference (Ref2VA) inference on Metal.

### llama.cpp — local prompt-formatting runtime
- Upstream: <https://github.com/ggml-org/llama.cpp>
- License: MIT License — Copyright (c) 2023–2026 The ggml authors
- Role: cloned and built by `install.sh` into `addons/llama.cpp/`; runs the
  optional local prose-expansion model.

### Qwen2.5-1.5B-Instruct (GGUF quantization)
- Upstream: <https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF>
- License: Apache License 2.0 — Copyright the Qwen team (Alibaba Cloud)
- Role: optional ~1 GB local model that expands the user's prose before
  deterministic H3 formatting wraps it.

### MiniMax-H3 checkpoints
- Source: <https://huggingface.co/MiniMaxAI/MiniMax-H3>
- Copyright © MiniMax AI. The checkpoints are downloaded at install time and
  are governed by the license published on the model card (minimax-h3-community
  license). Review the exact terms there before any commercial use of
  generated output.
- Role: prompt-to-video (FL2VA) and ordered-reference (Ref2VA) checkpoints.

### H3 Character Sheet Generator (design inspiration)
- Source: <https://huggingface.co/PoopMan333/H3_Character_Sheet_Generator>
- Credit: PoopMan333, whose ComfyUI workflow established the technique behind
  OpenMagia's "Compose character" feature — a single frozen-subject orbit
  generation sliced into consistent multi-view references — as well as the
  keep/ignore note discipline for rough references. OpenMagia implements the
  idea natively (no ComfyUI) with its own prompt assembly; no code is
  incorporated from the upstream workflow. The upstream workflow is made
  available under the minimax-h3-community license linked from its page.
- Role: design inspiration for `format_sheet_prompt()` in `h3_prompts.py`.

### FFmpeg / FFprobe
- Upstream: <https://ffmpeg.org>
- Invoked as an external binary (probing, thumbnails, timeline export). Not
  bundled with this repository; governed by its own LGPL/GPL terms depending
  on the build installed on your machine.

## Full license texts

### MIT License (h3.c, llama.cpp)

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### Apache License 2.0 (Qwen2.5-1.5B-Instruct)

The full text is published at <https://www.apache.org/licenses/LICENSE-2.0>
and in the model repository linked above.

## Trademarks & affiliation

OpenMagia is an independent open-source project. It is not affiliated with,
endorsed by, or sponsored by MiniMax, Alibaba Cloud, Apple, or GitHub. All
product names, logos, and brands are property of their respective owners.

## Demo assets

The preview clips in `assets/skill-previews/` were generated with OpenMagia
driving MiniMax-H3. They are included for demonstration and are provided
under AGPL-3.0-only with the rest of the OpenMagia repository.
