# Release PR — v0.1.0 “First Spell”

> Prepared as the narrative for OpenMagia's first public pull request /
> release notes. Publish the repo privately first, iterate, then when it's
> time to go public, open a PR from a `release/v0.1.0` branch into `main`
> (or paste this as the GitHub Release description) — it's written to work
> as both.

---

## Summary

OpenMagia is a **local, single-view visual editor for AI video** on Apple
Silicon. It pairs [h3.c](https://github.com/antirez/h3.c) — antirez's native
Metal inference engine for MiniMax-H3 — with a real, dependency-free
non-linear editor in the browser. Generate scenes from prompts, keep the same
character across every shot, then cut, layer, transition, and export a single
MP4 — all offline, on your own machine.

Think of a well-made hand tool: **one stage, one timeline, one inspector** —
and nothing else asking for your attention.

## Motivation

Cloud AI-video tools keep your projects on their servers, meter your
creativity in credits, and bolt a chat box onto a player. OpenMagia takes the
opposite path:

- **Local first.** Generation, editing, and export run entirely on your Mac.
  Projects live in a folder you own.
- **Editing is the product.** A multi-track timeline with transitions, split,
  freeze, trim, zoom, audio detach, and a WYSIWYG canvas compositor — not a
  prompt box with a preview.
- **Calm by craft.** One stage, one timeline, one inspector. Controls appear
  exactly where you look for them; nothing else competes for attention.
  The app is finished down to the details so it just works intuitively,
  without frills.
- **Prompting with guardrails.** The local 1.5B formatter may expand your
  prose, but deterministic code owns MiniMax's official field names, ordering,
  and limits. Malformed model output can never reach the engine.

## What's in this release

### Generation
- Prompt-to-video scenes (8–360 frames @ 24 fps, up to 15 s) with eight style
  presets: Motion Graphics, Fast Trailer, Social Reel, Motion Typography,
  Cinematic Story, Anime Action, Product Spot, Dreamlike.
- **Characters that survive cuts** — upload a portrait once; with Ref2VA it
  becomes an ordered `--ref-image` addressed as *Picture 1, Picture 2…*;
  with FL2VA only, it anchors the scene's first frame.
- **Scene chaining** via last-frame extraction (`--first-frame`) for
  multi-shot continuity.
- Optional **Qwen2.5-1.5B local formatter** (llama.cpp) for prose expansion —
  schema-safe even when it's absent.

### Editing
- Multi-track timeline: base video, overlay (centered picture-in-picture),
  and audio; drag-and-drop media bin; split at playhead; freeze frames; edge
  trim; per-clip zoom; audio detach.
- Transitions — cut, dissolve, fade, wipe, slide, circle — previewed on canvas
  and composited with ffmpeg `xfade` on export.
- Full transport: play/pause, seek, frame-step, loop, global + per-clip +
  per-track mute. Keyboard: `Space` `←` `→` `Home` `End` `S` `+` `-` `Delete`.

### Workflow
- Three-zone workspace: global sidebar (Editor, Project Library, Asset
  Center, Skills), media bin on the left, contextual inspector on the right.
- **12 built-in production Skills** (motion graphics, brand promo, product ad,
  papercraft stop-motion, …) that load editable Style + Prompt starting
  points into the same guided composer.
- One-click **MP4 export** (yuv420p, faststart): normalized intermediates,
  `xfade` transitions, PiP overlays, `adelay`+`amix`+`alimiter` audio mix.

## How it works

```
 prompt sheet ──► h3_prompts.py ──► h3 (Metal) ──► clips
      ▲                │  deterministic fields,      │
 Qwen 1.5B (local,     │  labels, timing, limits     ▼
 prose expansion only) ┘                        media bin
                                                     │ drag
                                                     ▼
        canvas preview ◄──── timeline ────► ffmpeg export ──► MP4
```

## Design principles

1. **One folder, no dependencies.** Stdlib-only Python server; vanilla JS/CSS
   frontend with zero npm packages.
2. **Editing is the center of gravity.** Generation serves the timeline.
3. **Correctness never depends on model output.** Deterministic code owns the
   schema; the LLM only improves prose.
4. **What you see is what you export.** Canvas math mirrors ffmpeg math.
5. **Calm is a feature.** Every screen element must earn its place — if it
   doesn't help you cut, layer, or generate, it isn't on the surface.

## Out of scope (deliberately)

Cloud sync, team collaboration, non-macOS inference, and chat-style
generation. OpenMagia is a local studio, not a service.

## Testing

- `python3 -m unittest discover -s tests -t .` — H3 prompt structure, field
  ordering, reference limits, duration clamps (CI runs this on every PR).
- Manual matrix: generate → place → transition → freeze → split → detach
  audio → export; verified export matches canvas preview.

## Screenshots

<!-- Attach: editor overview · generate side-sheet · timeline with transitions · export dialog -->

## Checklist

- [x] AGPL-3.0-only license for OpenMagia (`LICENSE`)
- [x] Third-party credits & licenses (`NOTICE.md`) — h3.c, llama.cpp, Qwen,
      MiniMax-H3, ffmpeg
- [x] README with install, usage, architecture, keyboard reference
- [x] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, PR/issue templates, CI
- [x] Heavy artifacts gitignored (engine, checkpoints, addons, user projects)
- [ ] Screenshots attached before going public

## Credits

Standing on the shoulders of giants — with gratitude to:

- **Salvatore Sanfilippo ([antirez](https://github.com/antirez))** — h3.c,
  the native MiniMax-H3 engine that makes local generation possible.
- **[PoopMan333](https://huggingface.co/PoopMan333)** — the
  [H3 Character Sheet Generator](https://huggingface.co/PoopMan333/H3_Character_Sheet_Generator)
  workflow behind the "Compose character" technique: one frozen orbit, sliced
  into references that can't disagree with each other.
- **MiniMax AI** — the MiniMax-H3 model family.
- **The ggml authors** — llama.cpp.
- **The Qwen team (Alibaba Cloud)** — Qwen2.5-1.5B-Instruct.
- **FFmpeg contributors** — the invisible backbone of every export.

OpenMagia is independent and not affiliated with any of the above. See
[NOTICE.md](../NOTICE.md) for licenses and trademark notes.
