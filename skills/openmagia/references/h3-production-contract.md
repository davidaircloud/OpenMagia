# OpenMagia MiniMax H3 production contract

Use this contract for every OpenMagia video skill.

## Generation modes

- T2VA: text-only generation.
- I2VA/FL2VA: a supplied first frame anchors frame zero. Continue its geometry, identity, lighting, camera axis, and motion; do not redescribe it as a loose reference.
- Ref2VA: ordered references define characters, products, environments, materials, palettes, labels, or designs. State what each reference governs. Never let pose, crop, or background override the user unless requested.
- OpenMagia currently emits one H3 scene per generation. Break longer productions into independently reviewable scenes and assemble them on the timeline.

## Hard limits and fields

- 24 fps; 8–360 frames; maximum 15 seconds.
- Up to nine ordered reference images across cast and visual references.
- Preserve names, dialogue, visible copy, counts, brand spelling, product geometry, and user-supplied facts exactly.
- Resolve duration before writing beats. Use chronological `CUT NN | start-end` ranges with no gaps, overlaps, or times beyond the selected duration.
- The final H3 prompt must contain the mode-appropriate official field structure produced by OpenMagia: visual description/timeline, soundscape, and non-diegetic music, plus reference definitions when applicable.
- Treat silence, dialogue priority, effects-only, and full mix as explicit audio decisions. Never request garbled placeholder speech.

## Refinement boundary

The local formatter may expand concrete staging, camera, lighting, atmosphere, body mechanics, environmental response, transitions, and a final composition. It must not change user facts, quoted text, identity, product claims, reference roles, duration, frame count, or safety constraints. Deterministic OpenMagia code owns H3 schema, timing, reference count, and mode validation.

## Delivery check

Before generation confirm: correct mode; exact duration and frame count; reference roles; identity/product invariants; readable requested text; continuous geography; physically observable action; camera motivation; clean audio intent; and a deliberate final frame. Ask only for missing information that materially changes the result.
