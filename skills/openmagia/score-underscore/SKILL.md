---
name: Score and Underscore
description: Builds instrumental cues and score beds with YuE 2, using ABC notation when exact pitches must hold.
---

# Score and Underscore

Read `../references/music-production-contract.md` before authoring.

Make music that serves a picture or a scene: an instrumental bed, a title cue,
a sting. Where a Song Director chooses a voice, this skill chooses an
ensemble, a dynamic shape, and a place to breathe under dialogue.

## Decide the authority first

Two authorities exist and they must not be mixed in one generation.

1. **Style prose** — ensemble, register, texture, dynamics, mood, and the
   tempo term. Always present.
2. **ABC notation** — the pitch authority. Supply it when the melody, meter,
   or key must be honoured exactly, for example a recurring theme reused across
   episodes. An ABC score requires `full` or `melody` planning; `melody` is
   normally correct because it keeps the tune and frees the accompaniment.

If there is no score, the plan carries the pitches and the ABC comes back out
of the generation as the editable record of what was written.

## Instrumental intent

There is no instrumental switch. State it twice: in the style tags
(`instrumental, no lead vocal, no sung words`) and in the lyric body, which is
an instrumental section map such as `[Intro]`, `[Instrumental]`, `[Outro]`.
Say so plainly in the notes — this is a strong direction, not a guarantee, and
some seeds still add a vocalise.

## Cue length

The request cannot set a duration. Build the section map from the cue's real
shape (a four-bar sting is not an eight-section map), expect the runtime to
realize the structure at its own tempo, and cut to length on the timeline.
Leave a tail: an abrupt trim at the picture cut is cheaper than a song that
ends early.

## Under dialogue

Choose registers that leave room. Name the frequency neighbourhood in words
(`low sustained strings, no busy midrange percussion`), keep the dynamic arc
flat under the speech region, and put any swell where nobody is talking. If a
cue fights a voice, remove an instrument rather than turning the guidance
knob.

## Review

Tap the tempo against the picture. Confirm no lead vocal entered. Confirm the
ABC, if supplied, survived into the returned score. Check whether the ending is
a cadence or a truncation before reusing the cue as an outro.
