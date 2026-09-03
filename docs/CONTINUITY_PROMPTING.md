# Continuity prompting for MiniMax H3 in OpenMagia

This guide explains what continuity can and cannot guarantee, how to prepare a
project continuity file with AI, and how to keep unusual characters stable from
one generated scene to the next.

## Using Storyboard mode

Select **Storyboard** from the Generate type menu when planning two or more
connected scenes. The full-screen composer deliberately separates three scopes:

1. **Shared project style** contains only facts that must remain stable across
   the sequence.
2. **Scene cards** contain chronological action, camera, sound, and scene-local
   facts. They can carry different Cast/reference allocations.
3. **Shared output** fixes aspect ratio, resolution, quality, length, steps,
   seed, and audio direction for the whole batch.

Previous-frame continuity is enabled on Scene 2 and later by default. Disable it
on a card only when the scene should begin an intentional new sequence. The
visible reference rail is authoritative: it shows the image allocation saved
for that card, not a project-wide implicit set. **Exact opening frame** uses the
native frame-conditioning path, so Cast becomes text identity locks. **Previous
frame + Cast** instead sends the predecessor frame as Ref2VA Picture 1 and the
selected Cast views as Pictures 2–9. It reinforces identity, but it is not a
pixel-exact opening-frame guarantee.

Submission creates the complete dependency chain before generation starts. The
queue runs in order, and each continued scene points to the pending media record
of its direct predecessor. Closing the workspace does not discard the draft.

## The two conditioning modes are different

OpenMagia uses the native [h3.c](https://github.com/antirez/h3.c) runner. H3 has
two relevant checkpoints:

1. **First/last-frame conditioning (FL2VA/I2VA)** anchors the opening and/or
   ending composition with one or two frames.
2. **Omni-reference conditioning (Ref2VA)** accepts ordered character, object,
   image, video, and audio references. Images are exposed to the model as
   `<Picture 1>`, `<Picture 2>`, and so on; filenames have no semantic meaning.

The native modes cannot be combined in one H3 command. h3.c rejects a request
that contains both a frame anchor and full references. See the
[h3.c tutorial](https://github.com/antirez/h3.c/blob/main/README.md) and its
[conditioning validation](https://github.com/antirez/h3.c/blob/main/h3.c).
Therefore, **Exact opening frame** gives the model the exact opening frame but
does not also send Cast reference images. OpenMagia's hybrid **Previous frame +
Cast** option is implemented entirely as Ref2VA: the decoded predecessor frame
is Picture 1 and Cast follows it. OpenMagia keeps Cast identity descriptions as
text locks in exact-frame mode, but text is weaker than multi-view visual
evidence for topology that is ambiguous in the chosen frame.

This tradeoff is fundamental:

- Use **Ref2VA + Cast** when exact character identity or unusual anatomy is more
  important than a pixel-matched transition.
- Use **Continue frame** when opening composition, camera direction, geography,
  pose, and motion state are more important and the selected frame clearly shows
  every identity-critical feature.

## Case study: why Mote changed in Scene 2

The Scene 1 last-frame file and the frame selected for Scene 2 are byte-for-byte
identical. Scene 2 also begins with the same composition. The failure was not a
wrong-frame selection.

Mote is a difficult topology: exactly four independent floating basalt hands,
one hollow turquoise mask, saffron connections, and no torso. In the selected
rear gameplay frame, the turquoise face is hidden, the mask reads as a dark
oval, hands overlap in perspective, and the upper/lower roles are ambiguous.
Because frame anchoring excluded Mote's four selected Cast views, the model had
only that ambiguous image and prose.

The Scene 2 action then imposed too many simultaneous topology-changing cues in
five seconds: plant all four hands in sequence, keep three as supports, catch
two objects with named front hands, run across a deforming bridge, jump, and
react to a large attacker. The model preserved the broad palette and creature
concept but reassigned visible parts to satisfy motion. The mask turned into a
front-facing head while two hands were absorbed or omitted. This is a known
generative failure class: semantic resemblance survived while object
persistence and topology did not.

Seed, high steps, exact layers, and repeated negative wording cannot guarantee
topology. They can improve repeatability or fidelity, but they do not restore
visual evidence unavailable to the active checkpoint.

## Rules for safe chained scenes

Before generating:

1. Inspect the previous scene's actual last frame at full size.
2. Reject it as an anchor if a critical face, mask, limb count, connection,
   signature prop, or silhouette is hidden, merged, motion-blurred, cropped, or
   visually inverted.
3. End the previous prompt with a short **continuity hold**: both characters
   fully visible, separated silhouettes, critical anatomy readable, minimal
   occlusion, stable camera, and no transformation in progress.
4. Start the next scene with 0.5–1.0 seconds of the existing gait and pose before
   introducing a new action. Do not reset or immediately articulate every limb.
5. Give a nonhuman character one topology-sensitive task per beat. Say “Mote
   crosses while maintaining the established four-hand gait,” not “three hands
   stabilize while the front-right catches, then the front-left catches.”
6. Describe persistent objects, not only counts: each of the four hands remains
   the same continuous basalt hand even while occluded; the mask remains upright
   and central; connections never become limbs or a torso.
7. Prefer the simpler action whenever action and anatomy conflict.
8. Stop and regenerate the first scene that drifts. Never use a malformed last
   frame as the next scene's authority.

For identity-critical shots, deliberately break the pixel-perfect chain and use
Cast/Ref2VA again. Re-establish the characters from their multi-view references,
then end on a clean anchor suitable for subsequent frame continuation.

## MiniMax-compatible prompt structure

For Ref2VA video prompts, keep these six fields in this order, following the
[official MiniMax-H3 examples](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/README.md):

```text
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

For a frame-anchored continuation, OpenMagia supplies `<Picture 1>` as the
opening frame and formats the prompt for the corresponding mode. Do not claim
that `<Picture 2–9>` contain Cast unless the UI is actually in reference mode.
Reference order is authoritative; filenames and media labels are not sent as
model semantics.

### Recommended topology language

Use a compact invariant block once, then make the action simple:

```text
Mote topology lock: one persistent upright turquoise mask; exactly four
persistent independent basalt hands; four continuous saffron connections; no
torso, conventional arms, or conventional legs. Each hand remains the same
object throughout occlusion and motion. Never merge, split, remove, duplicate,
swap, invert, or reinterpret these parts. Rear views are not alternate anatomy.
Prefer simpler motion over changing topology.
```

Avoid contradictory micro-direction such as requiring one limb to collect an
object while all limbs are simultaneously load-bearing. Avoid stacking multiple
collections, environmental destruction, complex traversal, HUD changes, and
precise limb choreography into the same short beat.

## Creating a project continuity file with AI

Create one Markdown or plain-text file per project, for example
`PROJECT_CONTINUITY.md`. The file is a stable-fact authority, not a plot recap
and not a replacement for visual references.

Give the AI:

- exact character descriptions and the names of the approved Cast views;
- environment, geography, palette, materials, time, weather, and lighting;
- camera height, lens language, screen direction, and gameplay presentation;
- persistent props, HUD geometry, counters, typography, and allowed state
  changes;
- sound and music conventions;
- the latest approved ending state;
- a list of facts that are unknown or visually ambiguous.

Use this instruction:

```text
Create a MiniMax H3 continuity authority for OpenMagia from the supplied
evidence. Record only stable facts supported by the evidence; never invent an
unseen trait. Separate stable invariants from the latest scene state and from
future action. For every nonhuman or mechanically unusual subject, enumerate
topology as persistent named objects and connections, including exact counts,
orientation, forbidden additions, and behavior through occlusion. Record which
facts require visual references and cannot be guaranteed by text alone.

Resolve conflicts in this order: selected opening frame for opening composition
and motion state; attached visual references for identity and design; selected
media generation metadata; latest approved scene; earlier approved history;
text-only guidance. Never convert a historical action into a future command.

Return these headings only: PROJECT; EVIDENCE AND REFERENCE ORDER; CHARACTER
IDENTITY AND TOPOLOGY; WORLD AND GEOGRAPHY; CAMERA AND MOTION; HUD AND VISIBLE
TEXT; SOUND AND MUSIC; LATEST APPROVED END STATE; NEXT-SCENE HANDOFF; FORBIDDEN
DRIFT; UNKNOWN OR AMBIGUOUS FACTS; VALIDATION CHECKLIST. Use concise production
language. Do not output story ideas, generic advice, or unsupported details.
```

Review the result manually. In particular, verify reference numbering against
the current OpenMagia UI, remove invented facts, and mark topology that is not
visible in the intended continuation frame. The continuity file should be
updated only after an approved generation, never after a failed or malformed
one.

## Track state with a continuity ledger

Continuity is evidence, not repetition. A long style paragraph cannot make an
object exist in an earlier frame, and the word “same” cannot repair missing
evidence. Keep these scopes separate:

- **Project Style** contains stable design facts: character identity and
  topology, the canonical vehicle design, world materials, palette, camera
  language, and sound conventions.
- **Latest approved state** contains facts that can change: who is present,
  passenger count, seating, wardrobe currently worn, props currently carried,
  vehicle damage, HUD values, screen direction, time, weather, and location.
- **Scene action** contains only what changes during the new scene, including
  the visible entrance, exit, acquisition, loss, or transformation that causes
  each state change.

For every handoff, record this compact ledger:

```text
CAST: Santiago drives; Helena sits rear-left; Miguel sits rear-right; no other passenger.
VEHICLE: one yellow-and-blue three-wheel mototaxi; black canopy; moving screen-left to screen-right.
PROPS: two backpacks are worn; one chicken stands on the rear roof; no arepa is visible or carried.
WORLD: painted bridge; school tower ahead; sunny morning.
TEXT/HUD: exact approved text only; no incidental copy.
NEXT CHANGE: Miguel receives one wrapped arepa from the named vendor on camera before carrying it.
```

The absence statement matters. If an arepa is not present, say so. A later
scene must not describe “the same arepa” or place it in a hand. It must first
show a concrete source and acquisition: the vendor enters, hands over exactly
one wrapped arepa, Miguel receives it, and the updated ledger then records that
he carries it. Do the same for a new passenger, backpack, animal, tool, vehicle
part, injury, costume, sign, or HUD value.

### The mototaxi/arepa failure

The failed sequence made two separate mistakes:

1. The arepa was added to the shared Project Style and Scene 3 prompt even
   though it was absent from the approved Scene 2 ending. That converted a
   future plot action into a supposedly persistent fact, so it appeared without
   an acquisition.
2. Scene 3 changed from the generated Santiago visible in the predecessor frame
   to a hybrid set containing detailed original Cast portraits. Those two
   identity authorities were similar but not identical. Ref2VA reconstructed a
   new Santiago rather than preserving the exact generated face.

The correction is to keep the arepa out of Project Style, introduce it visibly
in the scene where it enters, and update the latest-state ledger only after that
scene is approved. For ordinary adjacent vehicle shots, use Exact opening frame.
Use Previous frame + Cast only at a motivated camera change where the identity
benefit is worth possible resynthesis, and select Cast views that agree with the
generated identity. Never use New sequence as an identity refresh.

## OpenMagia continuity review

Before a Storyboard is queued, OpenMagia now opens **Review continuity**. The
review compares adjacent scene prompts, selected Cast, reference allocation,
continuation mode, and Project Style. When the local refinement model is
available it is prompted to identify:

- cast, passenger-count, or seating changes;
- props, food, animals, wardrobe, or vehicle parts that appear or disappear;
- “same,” “still,” or “continued” elements that were never established;
- changes to vehicle geometry, screen direction, geography, HUD, or visible
  text;
- conflicts between the predecessor frame and Cast identity authorities; and
- scene-local state that was incorrectly promoted into Project Style.

For every finding, choose one of three outcomes:

1. **Already visible in the approved previous frame** — confirm only after
   inspecting that actual frame at full size.
2. **This scene visibly introduces the change** — the scene must show the cause
   before treating the element as persistent.
3. **Return to storyboard and correct it** — use this for an accidental change,
   unclear provenance, or conflicting identity evidence.

Confirmed decisions are saved with the storyboard and added to the scene's
continuity instructions. Any prompt, reference, Cast, style, or continuation
change invalidates the confirmation and triggers a new review.

The refinement model is an advisory text auditor, not a visual oracle. It can
compare authored facts and metadata, but it cannot prove that the rendered
pixels contain the correct face, prop, limb count, vehicle, or seating. Human
approval of the prior scene remains mandatory. In a future vision-enabled
workflow, visual analysis should propose ledger values, but the same confirmation
sheet must remain the authority before downstream scenes are released.

### Optional scene optimization

Enable **Optimize scenes** beside the Storyboard generation button when longer
renders are unstable on the current computer. Before continuity review or
queueing, OpenMagia asks the local refinement model to divide every 10–15 second
scene into chronological clips of no more than five seconds. OpenMagia—not the
model—locks each clip's frame count, reference inheritance, order, and
last-frame dependency.

The optimized clips return to the Storyboard for review instead of entering the
queue immediately. Confirm that actions are neither repeated nor omitted, then
run generation again. This reduces memory pressure and gives continuity fewer
seconds in which to drift; it does not guarantee identity, so the normal
continuity review and final-frame approval still apply.

## Identity and reference rules

1. Give every recurring character one canonical Cast identity. Select views
   that agree on age, face, hairstyle, wardrobe, proportions, and rendering
   style. Remove a contradictory view rather than averaging it with the others.
2. Do not change the selected Cast set between adjacent scenes without a stated
   reason. A changed reference set is a changed conditioning problem.
3. In hybrid mode, Picture 1 is always the predecessor frame. Cast references
   begin at Picture 2. Never hand-author Picture numbers from memory; verify the
   visible reference rail and generated prompt.
4. The seed affects stochastic sampling. It does not identify a character and
   cannot compensate for missing or conflicting visual evidence.
5. Preserve exact counts and roles: “exactly three named people, no additional
   passenger” is stronger than merely listing three names.
6. For vehicles, record immutable geometry and moving state separately. Include
   wheel count, colors, canopy, passenger layout, travel direction, wheel
   rotation, background translation, and camera relationship.
7. If a prior scene contains an extra character, wrong face, duplicated prop,
   changed vehicle, unreadable required text, or malformed anatomy, reject it.
   Never chain from a bad frame simply because generation completed.

## Per-scene validation checklist

- The chosen conditioning mode matches the priority: frame geometry or Cast
  identity.
- Every `<Picture N>` in the prompt corresponds to the actual ordered input.
- The anchor visibly proves all identity-critical anatomy needed by the action.
- The opening second continues rather than restages the characters.
- Each unusual character performs at most one topology-sensitive task per beat.
- Limb, mask, face, prop, and connection counts remain persistent through
  occlusion.
- The final 0.5 seconds produce a clean, readable handoff frame.
- Every new foreground element has a named source and visible entrance or
  acquisition; otherwise it is explicitly absent.
- Cast count, passenger roles, seating, carried props, vehicle geometry, screen
  direction, HUD, and visible text match the latest approved-state ledger.
- Project Style contains no scene-local prop or future action.
- Failed anatomy is rejected before any later scene is generated from it.

## Reliable storyboard limits

OpenMagia now rejects a storyboard before queueing when its CUT numbers repeat,
its timing has gaps or overlaps, its final CUT does not match the selected
duration, or it names a `<Picture N>` that the H3 command will not actually
send. An authored CUT plan is preserved verbatim rather than nested inside a
second generic timeline.

### Never use a new sequence as an identity refresh

Starting a new sequence discards the predecessor's vehicle, seating, screen
direction, geography, and motion state. Cast portraits can rebuild identity,
but they cannot prove those scene-specific facts. This was the cause of the
Colombian mototaxi Scene 3 regression: the independent scene reconstructed the
vehicle and passengers instead of continuing Scene 2.

OpenMagia now supports two explicit continuation modes:

- **Exact opening frame** uses H3's first-frame conditioning. It is the strongest
  choice for an exact handoff, vehicle motion, seating, camera axis, and spatial
  continuity. Cast remains as detailed text identity/topology locks because
  this H3 conditioning path cannot simultaneously accept Ref2VA images.
- **Previous frame + Cast** sends the decoded predecessor frame as ordered
  `<Picture 1>` and up to eight selected Cast images as `<Picture 2–9>`. The
  prompt marks Picture 1 as the highest continuity authority. Use this at a
  motivated camera change when identity needs visual reinforcement. It is a
  reference-conditioned continuation, so it preserves more evidence but does
  not promise pixel-identical first-frame matching.

Use **New sequence** only for an intentional discontinuity. Never alternate to
New sequence merely to refresh faces. For a multi-character moving-vehicle
sequence, use Exact opening frame for normal adjacent shots and Previous frame
+ Cast at carefully chosen camera changes. Reject any frame that introduces an
extra passenger, changes the vehicle, reverses direction, or changes seating;
do not let later scenes inherit it.

High and Reference fidelity always use the schedule selected in Generate or
Storyboard mode, including 10–15 second native renders. Those long renders are
substantially slower and more memory-intensive than 5-second scenes, but
OpenMagia does not pre-emptively lower their fidelity. If the H3 process
actually stalls, OpenMagia performs one recorded stable-memory retry while
preserving dimensions, frame count, steps, and seed; the scene metadata and UI
identify that retry rather than hiding it. Choose Balanced deliberately when
speed and stability matter more, or use 5-second High scenes when you want the
full schedule with a smaller resource footprint.

For audio, choose Effects while validating visuals. Full mix requires concise,
non-competing audio direction. Add music, dialogue, or denser sound only after
the visual sequence is approved; too many simultaneous sound instructions can
produce a loud, harsh native H3 mix even when the encoded file is technically
valid.
