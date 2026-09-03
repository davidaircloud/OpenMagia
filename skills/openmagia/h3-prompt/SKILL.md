---
name: H3 Prompt Writer
description: Convert a scene idea and optional media into a validated MiniMax H3 generation prompt for OpenMagia.
---

# H3 Prompt Writer

Read `../references/h3-production-contract.md` before authoring.

Translate a user’s scene request into OpenMagia’s validated MiniMax H3 prompt structure. This skill directs one generation scene; use the timeline to assemble longer work.

## Resolve the brief

Use the refinement sheet to obtain only missing information that materially changes generation: subject and action, outcome/final frame, duration, aspect ratio, setting, camera, pacing, cuts, exact visible text/dialogue, audio priority, and reference roles. The local formatter can propose safe defaults when the user skips fields, but must expose the resulting plan for review rather than silently changing facts.

Reject or resolve conflicts: more than nine images; duration beyond 15 seconds; unreadably dense copy; contradictory identity references; a requested opening that conflicts with a selected first frame; or multiple unrelated events that cannot fit the duration.

## Select the mode

- T2VA when no visual anchor is supplied.
- I2VA/FL2VA when a still or selected media edge frame must be frame zero.
- Ref2VA when ordered images define identity, product, environment, material, palette, label, or design.

Give every reference one explicit role. Distinguish cast identity from general visual references. Pose, crop, lighting, and background in a reference are non-binding unless the user names them. A first frame is binding at time zero.

## Direct the scene

Calculate frames at 24 fps and clamp to 8–360. Choose a continuous shot unless distinct cuts improve comprehension. For every cut provide exact non-overlapping ranges, a new composition, concrete visible action, camera framing/motion, continuity from the prior state, and a settled outgoing state. A one-shot prompt must describe setup, intermediate movement, physical response, and final composition rather than a start/end wish.

Preserve names, counts, quoted dialogue, labels, and visible copy exactly. Make body mechanics, object contact, geography, lighting, and environmental reaction physically coherent. Avoid contradictory camera instructions, repeated action, teleportation, impossible travel, and vague phrases such as “cinematic sequence” without observable content.

Separate synchronized sound effects/ambience from non-diegetic music. Explicitly choose silence, effects, dialogue priority, or full mix. Keep speech short enough for the selected duration.

## Refinement and validation

Supply the complete installed skill and user answers to the local formatter. It may enrich staging, camera, lighting, atmosphere, transitions, and sound, but cannot alter facts, timing, copy, identity, or reference roles. Deterministic OpenMagia code then owns field names/order, reference labels, mode, duration, frame count, cut timing, and audio sections.

Before queuing, review mode, references, continuity, exact text, camera, readable action, sound/music, and final frame. If a result fails, correct the smallest cause: simplify action, strengthen reference roles, reduce cuts/copy, or choose a better opening frame.
