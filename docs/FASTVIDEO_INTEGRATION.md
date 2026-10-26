# FastVideo in OpenMagia

FastVideo is an optional Apple Silicon video backend. Install it in Settings →
Models → Available, then choose **Use for video**. The composer’s Models picker
also switches between installed video engines. Installation does not activate
it automatically. Uninstall removes its managed runtime, source, caches and
weights; existing projects and the other engines remain available.

## What it runs

| Input | Checkpoint | Sampling |
|---|---|---|
| Text | FastVideo/FastVideo-FastH3-Trim-8-Step-MLX-INT6 | Eight forwards, released INT6 DiT |
| Image references | FastVideo/FastVideo-FastH3-Omni-8-Step-V1 | Eight forwards, local INT6 conversion with trained VSA gates |

These are distilled models, not a speed switch on the existing base H3 weights.
FastVideo complements h3.c and H3MLX. Its MLX runtime requires macOS on Apple
Silicon, at least 36 GB unified memory and approximately 170 GB for the complete
installation, including conversion sources. The existing editor remains Python
stdlib only.

The supported duration is approximately 5–15 seconds at 24 fps. FastH3 aligns
its temporal grid to `17n+5` frames: a request for 120 frames becomes 124 frames.
OpenMagia records both the request and the effective count. The fixed trained
schedule overrides the native engine’s quality/step controls, which are hidden
for this backend. Resolution and seed are preserved.

Image references use Omni’s reference conditioning, not exact first-frame
anchoring. When a request needs an opening frame or a shorter clip, the composer
shows one short message and an explicit action to switch to H3. It does not
silently discard the input. Storyboard reference continuity is supported;
exact-frame continuity requires H3. An unsupported storyboard is rejected before
it is queued.

## Isolation and safety

- Managed footprint: `addons/fastvideo/`. Downloads and conversions do not
  modify base H3, H3MLX or Qwen Image weights.
- The module has a separate Python environment. Each generation runs in a new
  process and exits after rendering, releasing model memory. Cancellation uses
  OpenMagia’s existing process-group termination and queue cleanup.
- The upstream source is pinned to
  `a0e6cf1f5086c9af735d73e6c0b80dbc1d5817b3`. Every Hub snapshot is pinned in
  `fast_video.MODELS`. Updates reinstall the reviewed pins; they do not pull
  arbitrary changes from upstream `main`.
- `requirements-fastvideo.lock` pins and hashes all 33 inference dependencies.
  `uv pip sync --require-hashes` verifies packages during installation.
- OpenMagia loads the original MLX submodules through a package namespace,
  bypassing upstream’s eager CUDA/training API imports. No upstream model math
  is patched. The full PyTorch, Gradio, Accelerate and training stack is not
  installed. The adapter does not expose FastVideo’s own server or web UI.
- Weights use safetensors. No Hub Python files are downloaded, no pickle model
  files are accepted, and no `trust_remote_code=True` path is used.
- Generation receives JSON over stdin and passes arguments directly to a
  subprocess, without a shell. Hub/Transformers offline flags and telemetry
  opt-outs are set during generation. References come from local project files.
- Safe removal requires the exact managed directory and receipt. Redirected
  roots and symlink roots are rejected. Removal is blocked during generation
  or installation, and an active backend falls back to h3.c before removal.

On M5, the module uses upstream’s opt-in NAX TensorOps attention with MLX
0.32.4. Private MLX header hashes must match the upstream allowlist. Independent
selected-token numerical checks passed on this M5 Max for FP16, BF16 and FP32;
the kernel and header hashes are recorded in generation metadata. Other Apple
Silicon machines use upstream’s `auto` attention backend. Reference conditioning
is capped to the output pixel area, matching native H3’s reference sizing; a
512×512 input is not silently enlarged to a 2048×2048 conditioning canvas. NAX accumulation can
change the generated clip relative to the SIMD backend even with the same seed.

This is process and dependency isolation, not an operating-system sandbox.
The worker runs with the user’s normal file permissions. A targeted code review
and a clean advisory scan reduce risk; they cannot establish that third-party
code is free of defects.

### Dependency review, 26 October 2026

The initial full upstream dependency set reported 70 advisories across six
packages. That stack was replaced with the smaller inference environment.
`pip-audit --no-deps --disable-pip -r requirements-fastvideo.lock` then reported
**no known vulnerabilities across 33 dependencies**. This result is a dated
snapshot; repeat the scan before a future release.

## Reproducing the benchmark

Run one generation workload at a time:

```sh
python3 scripts/benchmark_video_backends.py --backends h3-metal h3mlx fastvideo
```

The harness creates its own project without changing the active project or
saved backend choice. It submits real jobs through the same worker and media
registration code used by the app. Each backend runs a text prompt and the same
prompt with one image. The common image is the first frame of the h3.c text
render, saved beside the report. This favors that source model’s appearance;
subject-preservation comparisons should take that into account.

All cases request 512×512, 120 frames, seed 20261026 and synchronized sound.
Native h3.c uses Balanced (20 passes, 45 layers, reuse 2); H3MLX uses its
Balanced schedule (20 passes, 35 layers, reuse 2); FastVideo uses its trained
eight-forward checkpoints. This compares usable app presets, not equal models
or equal computational work. The generated clip duration is probed from the
output, rather than inferred from the requested frame count.

Raw logs, prompts, wall times and output probes are written to
`projects/fastvideo-benchmark-2026-10-26/benchmark.json`. The benchmark skips
completed cases when resumed. One run per case is an exploratory benchmark,
not a statistical performance guarantee. Record any concurrent downloads or
other workloads when publishing measurements.

## Licenses

FastVideo code is Apache-2.0. FastH3 and its source H3 components retain the
MiniMax H3 Community License; LightVAE is Apache-2.0. The install dialog links
the code and model terms before downloading. Model weights are not bundled in
the OpenMagia repository. Review the model terms for the intended distribution
and use of outputs.

Sources:

- https://github.com/hao-ai-lab/FastVideo/blob/a0e6cf1f5086c9af735d73e6c0b80dbc1d5817b3/docs/getting_started/installation/mlx.md
- https://github.com/hao-ai-lab/FastVideo/blob/a0e6cf1f5086c9af735d73e6c0b80dbc1d5817b3/docs/inference/fasth3_omniref_mlx.md
- https://huggingface.co/FastVideo/FastVideo-FastH3-Trim-8-Step-MLX-INT6
- https://huggingface.co/FastVideo/FastVideo-FastH3-Omni-8-Step-V1

## Measured results

The [published comparison](../benchmarks/2026-10-26-fastvideo/README.md) includes all six clips, the shared input image, CSV/JSON results and a local HTML player. On this M5 Max, FastVideo completed text in 132.413 seconds and image-reference generation in 175.620 seconds. h3.c took 310.605 / 326.590 seconds; H3MLX took 262.144 / 283.883 seconds. These are single serial runs, with no concurrent downloads.

Shared style is an optional disclosure below the main prompt, or above the scenes in the storyboard. Its enable checkbox preserves authored text when disabled. Unsupported FastVideo settings show a small inline message with an action to switch engines.
