# OpenMagia video benchmark — 26 October 2026

Apple M5 Max, 128 GB unified memory. 512×512, approximately five seconds, seed 20261026.

| Backend | Input | Wall seconds | Output seconds | Passes |
|---|---|---:|---:|---:|
| h3.c | text | 310.605 | 5.18 | 20 |
| H3MLX | text | 262.144 | 5.20 | 20 |
| FastVideo | text | 132.413 | 5.17 | 8 |
| h3.c | image | 326.590 | 5.18 | 20 |
| H3MLX | image | 283.883 | 5.20 | 20 |
| FastVideo | image | 175.620 | 5.17 | 8 |

Single-run measurements of different app presets and distilled models, not equal computational work. The reference image comes from the original h3.c text output, which may favor that model’s appearance. The first exploratory native timings were discarded and rerun without downloads. OS file and shader caches may be warm. Run order: FastVideo text/image, h3.c text/image, then H3MLX text/image. Timings include process startup, conditioning, denoising, decoding and media registration. FastVideo uses Trim INT6 for text and Omni INT6 for references, with M5 NAX attention and reference pixels capped to the output area. No temporal interpolation or spatial upscaling was enabled. FastVideo memory figures are peak MLX allocations, not total RAM; native peak memory was not measured.

Prompt:

> A small red tin toy robot stands on a pale wooden table against a plain teal backdrop. The robot slowly raises its right arm, waves twice, then lowers it. One continuous locked-off medium shot, soft studio light, clear natural motion, no text, no cuts. Quiet mechanical clicks, no speech or music.

Open [comparison.html](comparison.html) locally to play the six videos. [CSV](results.csv) · [JSON](results.json).
