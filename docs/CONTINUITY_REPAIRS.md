# Local continuity repairs

Before queuing a storyboard, OpenMagia compares adjacent scene prompts and checks duration, skill, reference and physical constraints. This is text and metadata review; it cannot certify rendered pixels.

The configured local prompt-refinement model now proposes scene-specific alternatives. If the model is unavailable or its response is invalid, the UI offers manual editing and explains the failure instead of presenting invented AI choices. The previous generic “already visible” / “this scene introduces it” confirmations have been removed.

The user enables individual issues and selects an alternative, then requests a local AI rewrite. Complete before/after prompts are shown. Each proposed scene change has an independent toggle. Applying enabled changes updates the draft, never queues generation; the next Generate request runs continuity review again. Disabled issues and unselected scenes are not rewritten. Reference IDs, cast IDs, style and generation settings remain unchanged; authored frame count and a required skill can be restored explicitly when repairing their corresponding technical issue.

| Risk | Grounded repair directions the local model can propose |
| --- | --- |
| Prop provenance | Show acquisition in the affected scene, remove the unestablished prop, or restore the established prop |
| Cast and identity | Explain an entrance/exit or preserve established identity and count |
| Space and motion | Preserve seating, screen direction and geography; take a traversable route |
| Text and world state | Preserve exact copy, lighting and weather, or motivate a change |
| Sound | Preserve the requested sound direction and describe motivated transitions |
| Timing and skill | Restore the authored duration or required skill |
| Resource budget | Simplify the affected prompt; shared style and unavailable references still require manual review |

These are capability categories supplied to the model, not hard-coded dropdown choices. Every displayed repair label and instruction comes from the local model. The preview remains essential: model suggestions can be wrong or aesthetically unsuitable.

## Audio verification

Magia previously forced newly created storyboards to silent mode. It now preserves the selected audio direction and notes. Regression coverage renders two adjacent 7.5-second audio clips through the real export pipeline and checks the 15-second output for boundary dropouts. An additional local H3MLX 360-frame, 12-step rain scene completed with a decodable audio stream, no clipped/nonfinite samples, and no silent 100ms windows. This one test does not establish that every model output has acceptable sound or explain a deleted project's intermittent artifacts.

Local inference uses a dedicated system instruction and one constrained correction attempt for unsupported provenance edits. Reversed evidence, invented elements, off-screen retroactive acquisitions, replayed entrances and failed prop removals are rejected. Scene names are labels rather than evidence. Text review remains advisory and cannot establish visual continuity from pixels.
