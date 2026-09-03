# Nima and Mote — MiniMax H3 Open-World Test

## Settings

- Video, 16:9, Preview 896×512, Balanced, 12 steps, seed 24017.
- Four scenes, each 120 frames / 5 seconds / 24fps.
- Retry a failed identity pass at 20 steps.
- Keep project style, seed, cast selection, and reference order unchanged.

The scene blocks below show the complete MiniMax H3 Ref2VA wire format for review. In OpenMagia, you may paste the complete block: the app recognizes official H3 fields. When a continuation frame is selected, OpenMagia safely rebuilds the reference header so the actual previous frame becomes `<Picture 1>` while retaining the timed `detailed_description`, soundscape, and music.

## Cast references

Scene 1 uses all nine slots:

- `<Picture 1–5>` Nima: front body, left side, front face, three-quarter face, back.
- `<Picture 6–9>` Mote: front, left side, back, three-quarter.

Scenes 2–4 must choose one conditioning strategy; the current h3.c runner does
not combine a frame anchor with Ref2VA Cast images:

- **Exact frame continuation:** `<Picture 1>` is the previous completed scene's
  last frame. Cast descriptions remain text locks, but Cast images are not sent.
  Use only when the last frame clearly exposes identity-critical anatomy.
- **Identity re-establishment:** use the selected Nima and Mote Cast views through
  Ref2VA without a continuation frame. Use this after drift or when the previous
  last frame hides Mote's mask, hands, or saffron connections.

The earlier version of this plan incorrectly documented the continuation frame
as `<Picture 1>` and Cast as `<Picture 2–9>` in one generation. That combination
is rejected by the installed runner. See [Continuity prompting for MiniMax H3 in
OpenMagia](CONTINUITY_PROMPTING.md) for the mode constraint and safe handoff
workflow.

## Project style

```text
Premium stylized third-person open-world adventure-game cinematic in a monumental wind-carved salt civilization. Grounded tactile realism: porous ivory salt architecture, eroded arches, carved archive niches, suspended parchment maps, indigo route markings, crimson accents, turquoise ceramic, black basalt, brass mechanisms, saffron energy threads, airborne salt dust, and warm directional desert light. Maintain a readable third-person player-follow camera behind and slightly above the heroes, recognizable landmarks, traversable geography, and clearly staged gameplay goals. Friendly NPCs are simple salt-and-parchment constructs, visually secondary to the heroes. Collectibles are always small hovering indigo cartography shards edged in brass and emitting restrained turquoise route-light. No HUD, menus, subtitles, title cards, logos, floating text, or visible button prompts. Preserve continuous geography, screen direction, daylight, palette, material finish, camera height, and scale. Nima always has exactly one biological leg and one long lacquered crimson prosthetic stilt, a shaved head, cinnamon skin, brass compass monocle, contour-printed parchment cape, and collapsible ink-spear. Mote always has exactly four independent floating black-basalt hands walking around one hollow turquoise ceramic mask, connected only by glowing saffron threads, with no torso, neck, conventional arms, or conventional legs.
```

## Scene 1 — Enter the open world

```text
subject_definitions: <Subject 1> is Nima from <Picture 1>, <Picture 2>, <Picture 3>, <Picture 4>, and <Picture 5>; preserve her exact face, one-biological-leg and one-crimson-stilt anatomy, shaved head, compass monocle, contour-map cape, ink-spear, proportions, materials, and colors. <Subject 2> is Mote from <Picture 6>, <Picture 7>, <Picture 8>, and <Picture 9>; preserve exactly four independent basalt hands, one hollow turquoise mask, saffron threads, no torso, proportions, materials, and colors.

summary: [reference generation] Create a 5.00-second audiovisual target video at 24fps. Nima and Mote enter a readable open-world gameplay area and collect the first cartography shard.

retention_analysis: <Subject 1>: fully_preserved - exact identity, anatomy, wardrobe, equipment, materials, and colors remain unchanged; reference pose and background are not copied. <Subject 2>: fully_preserved - exact four-hand construction, mask, threads, scale, materials, and colors remain unchanged.

detailed_description: [Shot 1] At 0.00s, a third-person gameplay camera behind and slightly above <Subject 1> and <Subject 2> follows them through a salt-library arch into a sunlit basin of ivory terraces, towers, parchment bridges, and distant secondary NPC travelers. From 0.00–1.50s, both advance along one readable route. From 1.50–3.50s, one brass-edged indigo cartography shard hovers above a low marker; <Subject 2> accelerates and catches it with one of exactly four hands. From 3.50–5.00s, turquoise light travels through its saffron threads and draws one glowing contour line across <Subject 1>'s cape. Settle behind both heroes facing the deeper open world. One continuous shot; no cuts, UI, logos, or visible text.

overall_soundscape: Dry wind, parchment flutter, precise crimson-stilt knocks, four distinct basalt handfalls, ceramic resonance, and one synchronized crystalline collection chime.

non_diegetic_music: Restrained reed-flute and hand-drum exploration motif resolving on the collection.
```

## Scene 2 — Cross the ravine

```text
subject_definitions: <Reference 1> is the opening continuity frame from <Picture 1>; it is the highest authority for opening composition, anatomy, environment, lighting, camera axis, pose, direction, and spatial relationships. <Subject 1> is Nima from <Picture 2>, <Picture 3>, <Picture 4>, and <Picture 5>; preserve her exact identity, one biological leg, crimson stilt, monocle, cape, and spear. <Subject 2> is Mote from <Picture 6>, <Picture 7>, <Picture 8>, and <Picture 9>; preserve exactly four basalt hands, one turquoise mask, saffron threads, and no torso.

summary: [reference generation] Create a 5.00-second audiovisual target video at 24fps continuing exactly from <Picture 1>. The heroes form and cross a bridge while collecting two shards.

retention_analysis: <Reference 1>: fully_preserved at 0.00s - continue without a reset. <Subject 1>: fully_preserved - exact identity, anatomy, wardrobe, and equipment. <Subject 2>: fully_preserved - exact construction, materials, and scale.

detailed_description: [Shot 1] At 0.00s, continue the exact state of <Picture 1>. From 0.00–1.25s, the heroes reach a ravine with an incomplete folded parchment bridge. From 1.25–2.75s, <Subject 1> plants her crimson stilt and draws an indigo path across the gap with the ink-spear while <Subject 2> uses exactly four hands to unfold it into a physical bridge. From 2.75–4.25s, they cross and <Subject 2> catches two identical shards sequentially. From 4.25–5.00s, two turquoise pulses enter Nima’s cape as they reach the far terrace; settle with an NPC outpost ahead. One continuous shot; no cuts, UI, logos, or visible text.

overall_soundscape: Ravine wind, stilt knock, ink scratching salt, parchment unfolding, four-hand impacts, and two synchronized collection chimes.

non_diegetic_music: Continue the reed-flute and hand-drum motif with a stronger traversal rhythm.
```

## Scene 3 — Help the Salt Archivist

```text
subject_definitions: <Reference 1> is the opening continuity frame from <Picture 1>; preserve its opening state exactly. <Subject 1> is Nima from <Picture 2>, <Picture 3>, <Picture 4>, and <Picture 5>; preserve her exact one-leg/stilt identity and equipment. <Subject 2> is Mote from <Picture 6>, <Picture 7>, <Picture 8>, and <Picture 9>; preserve exactly four hands, one mask, saffron threads, and no torso. The Salt Archivist is a secondary NPC defined only by the description below.

summary: [reference generation] Create a 5.00-second audiovisual target video at 24fps continuing exactly from <Picture 1>. The heroes help a Salt Archivist activate a route mechanism.

retention_analysis: <Reference 1>: fully_preserved at 0.00s. <Subject 1>: fully_preserved. <Subject 2>: fully_preserved. Salt Archivist: newly_generated - never resemble or replace either hero.

detailed_description: [Shot 1] At 0.00s, continue from <Picture 1> into a compact outpost. From 0.00–1.25s, one waist-high Salt Archivist—an ivory salt-stone figure wrapped in blank parchment, with two short arms, two short legs, and a circular brass compass-dial face—gestures toward a mechanism with four empty hand-shaped sockets. From 1.25–3.50s, <Subject 1> points with the spear while <Subject 2> places exactly four hands into the sockets, its mask suspended centrally. From 3.50–4.50s, the mechanism opens the gate and raises one shard; Mote catches it and transfers turquoise light to Nima’s cape. From 4.50–5.00s, the Archivist bows; settle behind the heroes with the opened path ahead. No dialogue, cuts, UI, logos, or visible text.

overall_soundscape: Ceramic NPC clicks, parchment rustle, brass ratchet, four synchronized stone contacts, gate rumble, and one collection chime.

non_diegetic_music: Warm restrained variation of the exploration motif.
```

## Scene 4 — Unlock the map tower

```text
subject_definitions: <Reference 1> is the opening continuity frame from <Picture 1>; preserve its exact opening state. <Subject 1> is Nima from <Picture 2>, <Picture 3>, <Picture 4>, and <Picture 5>; preserve exactly one biological leg, one crimson stilt, monocle, cape, and spear. <Subject 2> is Mote from <Picture 6>, <Picture 7>, <Picture 8>, and <Picture 9>; preserve exactly four hands, one mask, saffron threads, and no torso.

summary: [reference generation] Create a 5.00-second audiovisual target video at 24fps continuing exactly from <Picture 1>. The heroes unlock a new open-world region at a map tower.

retention_analysis: <Reference 1>: fully_preserved at 0.00s. <Subject 1>: fully_preserved - never add a second biological leg. <Subject 2>: fully_preserved - never add a torso, conventional limbs, extra hands, or another mask.

detailed_description: [Shot 1] At 0.00s, continue from <Picture 1> toward a wind-carved map tower. From 0.00–1.50s, <Subject 1> vaults one broken stair with the spear and lands on her single crimson stilt while <Subject 2> climbs alongside using exactly four hands. From 1.50–3.50s, at a brass compass pedestal, <Subject 2> catches three orbiting indigo shards sequentially while <Subject 1> inserts the spear. From 3.50–5.00s, turquoise route-light spreads physically across the landscape without becoming a HUD. Rise slightly into a wide third-person vista with both heroes foregrounded and the unlocked region ahead. One continuous shot; no cuts, UI, logos, or visible text.

overall_soundscape: Climbing handfalls, one stilt landing, spear lock, three collection chimes, brass mechanism, broad wind, and a low route pulse.

non_diegetic_music: The established motif expands into a brief adventurous resolution.
```

## Salt Archivist — create the additional NPC

Create the still first, then use that image as the source for Compose Character.

### Image-model prompt

```text
integrated_multimodal_description: Premium stylized game-cinematic character-design still of one original friendly NPC named the Salt Archivist, isolated full body on a clean neutral warm-gray studio background. A waist-high nonhuman ivory salt-stone construct wrapped in layered blank parchment, with one compact rounded body, exactly two short articulated salt-stone arms, exactly two short articulated salt-stone legs, and one small circular brass compass-dial face containing one indigo needle and a tiny turquoise route-light. No human face, eyes, mouth, hair, cape, weapon, readable text, symbols, or logo. Porous salt texture, weathered parchment edges, restrained indigo seam marks. Front three-quarter standing pose, entire silhouette visible, even soft studio lighting. One character only; no duplicate parts, environment, props, movement, or temporal progression. Hold the same composition across all five decoded frames.
```

### Compose Character prompt

```text
SALT ARCHIVIST is a waist-high friendly nonhuman NPC. Preserve one compact rounded ivory salt-stone body wrapped in layered blank parchment; exactly two short articulated salt-stone arms; exactly two short articulated salt-stone legs; and one small circular brass compass-dial face containing one indigo needle and a tiny turquoise route-light. Preserve porous salt texture, weathered blank parchment edges, restrained indigo seams, compact proportions, materials, and colors from every angle. No human face, eyes, mouth, hair, skin, cape, weapon, backpack, readable text, logo, extra limbs, or duplicate character. Generate a neutral stationary identity turn with the full silhouette unobstructed.
```

## Optional Route Tender NPC

### Image-model prompt

```text
integrated_multimodal_description: Premium stylized game-cinematic character-design still of one original friendly NPC named the Route Tender, isolated full body on a neutral warm-gray studio background. A knee-high nonhuman tripod made from exactly three tapered ivory salt-stone legs supporting one folded blank parchment lantern body, with one turquoise glass compass light suspended inside, thin indigo binding cord, and aged brass hinges. No arms, hands, human face, eyes, mouth, hair, text, logo, or weapon. Front three-quarter view, entire silhouette visible, even soft light. One character only; no duplicate legs, environment, motion, or temporal progression. Hold the same composition across all five decoded frames.
```

### Compose Character prompt

```text
ROUTE TENDER is a knee-high friendly nonhuman NPC. Preserve exactly three tapered ivory salt-stone legs supporting one folded blank parchment lantern body, one turquoise glass compass light suspended inside, thin indigo binding cord, and aged brass hinges. Preserve its tripod silhouette, scale, porous salt, weathered parchment folds, materials, and colors from every angle. It has no arms, hands, torso, human face, eyes, mouth, hair, clothing, weapon, logo, or readable text. Never add or remove a leg and never turn the lantern into a head. Generate a neutral stationary identity turn with the full silhouette unobstructed.
```

## Validation

- Nima: exactly one biological leg plus one crimson stilt; retain monocle, cape, and spear.
- Mote: exactly four independent hands plus one mask; no torso or conventional limbs.
- NPCs never borrow the heroes’ anatomy or equipment.
- Collectibles remain identical.
- The last frame must show Mote's upright mask, four separated persistent hands,
  saffron connections, and Nima's one-leg/crimson-stilt silhouette clearly
  enough to become the next scene's sole visual authority.
- Camera height, screen direction, geography, daylight, and palette progress naturally.
- No HUD, captions, logos, or unintended text.

Do not continue from a failed scene. Regenerate it first and select the corrected last frame.
