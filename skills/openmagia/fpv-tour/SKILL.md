---
name: FPV Tour Video
description: Create continuous first-person H3 environment tours with route geometry matched to duration. Use for fly-throughs, walkthroughs, ride-throughs, and destination reveals.
---

# FPV Tour

Read `../references/h3-production-contract.md` before authoring.

## Required intake

Collect the environment or references, camera platform (walking, drone, vehicle, or abstract glide), starting viewpoint, desired destination, must-see landmarks, forbidden zones, duration, aspect ratio, speed character, and ending purpose. Determine whether the supplied image is an exact first frame or a broader environment reference.

If route, destination, or camera platform is missing, the local refinement model must ask. When a reference permits several routes, it should present two or three concise options with clear tradeoffs—for example a low intimate route, a fast landmark route, or a slower architectural reveal—before formatting the prompt.

## Spatial survey

List stable landmarks, openings, obstacles, vertical changes, lighting zones, scale cues, and uncertain or unseen areas. Preserve their order and relative placement. Do not invent unseen rooms as factual extensions of a supplied environment.

Estimate a physically traversable route for the duration. Prefer fewer landmarks and readable parallax over impossible velocity. Define the arrival frame before writing intermediate motion.

## Route plan

Divide the tour into continuous phases: establish and accelerate, approach, pass or enter, turn or change elevation, transition through a lighting zone, decelerate, and arrive. Each phase must state the visible landmark, motion vector, camera height, speed change, and transition into the next phase.

Maintain horizon behavior, screen direction, obstacle clearance, scale, and parallax. Walking has head and step rhythm; a drone banks and carries momentum; a wheeled vehicle follows a turn radius. Avoid teleportation, instantaneous stops, collision-like near misses unless requested, or a route too dense for the runtime.

## H3 scene construction

Write one chronological scene rather than disconnected beauty shots. Map route phases to seconds or frame ranges at 24 fps. Include lens feel, stabilization level, acceleration and braking, motivated atmospheric changes, and a deliberate final hold.

Use an exact first-frame reference when departure composition is locked. Use Ref2VA when the reference defines the environment more broadly. Name which geometry and design traits must remain stable, and avoid claiming details that are not visible.

## Local refinement

The local model may improve route phrasing, velocity curves, atmospheric cues, sound transitions, and negative constraints. It cannot change landmark geometry, destination, platform, required waypoints, or reference roles. It must ask rather than infer any missing route-critical fact and must expose the route plan for approval before generation.

## Audio and quality check

Build continuous spatial sound: propulsion or footsteps, wind proportional to speed, reflections in enclosed zones, passing ambience, and deceleration at arrival. Verify the route is traversable, landmark order is stable, motion suits the platform, turns retain orientation, and the destination receives enough screen time.

If motion feels too fast, remove a waypoint. If geometry changes, shorten the route and repeat stable landmarks. If the arrival lacks impact, begin deceleration earlier and reserve a clean final hold.
