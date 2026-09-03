# Prompt skills

OpenMagia prompt skills are reusable production workflows. They are not model
checkpoints, persistent project styles, or installable packages.

## One source of truth

Bundled skills live in `skills/openmagia/`:

- `catalog.json` is the machine-readable catalog used by the UI and server.
- `<skill-id>/SKILL.md` is the complete human-facing workflow and reference.
- `references/h3-production-contract.md` documents the shared MiniMax H3 rules.

The server refuses to publish a catalog entry when its matching `SKILL.md`,
concise instruction, or machine contract is missing. The browser fetches this
catalog from `/api/skills`; there is no second hard-coded JavaScript catalog.

## How a skill and Refine work together

The selected skill owns the production method. Refine improves the user's
scene idea *inside* that method:

1. The server compiles the selected skill's instruction, invariants, required
   elements, and forbidden elements into a concise skill contract.
2. The local refinement model receives that contract as immutable direction.
   It may add useful scene detail, staging, camera, lighting, timing, and sound,
   but it may not summarize, replace, weaken, or contradict the contract.
3. If a prompt already uses complete H3 fields, the AI does not rewrite it.
   OpenMagia validates its authored timing and inserts the skill contract
   deterministically into the detailed description.
4. Immediately before generation, the server compiles the selected skill
   again. This execution-time check prevents stale UI state, saved drafts, or
   structured prompts from bypassing the skill.

The full `SKILL.md` is shown in the skill detail sheet for people to read. It is
never pasted wholesale into the small local model's context.

## Other reusable guidance

- **Custom prompt skills** are user-created workflows stored locally. Their
  concise instruction travels with the scene and is applied by the same prompt
  formatter.
- **Project styles** are continuity profiles attached to a project. They define
  stable visual facts. A project style and a prompt skill can be active at the
  same time: the style owns continuity; the skill owns how the new scene is
  directed.

## Adding a bundled skill

1. Create `skills/openmagia/<skill-id>/SKILL.md`.
2. Add exactly one matching entry to `skills/openmagia/catalog.json`.
3. Keep `instruction` short and operational.
4. Put non-negotiable rules in `contract.invariants`, necessary output in
   `contract.required`, and explicit failure cases in `contract.forbidden`.
5. Add or update catalog tests. Do not add a JavaScript catalog entry.
