---
name: Song Director
description: Turns an idea and a lyric into a complete YuE 2 song request with style tags, sectioned lyrics, and a chosen plan mode.
---

# Song Director

Read `../references/music-production-contract.md` before authoring.

Direct one finished song: the words, the style prose that controls the
arrangement, and the planning mode that decides how much of the music is
composed before it is performed.

## Intake

Collect, in this order: the lyric (or the story and point of view when the
writer wants lyrics drafted), the language, the intended singer, the genre and
instruments, the tempo or feel in words, the mood, and what the song is for
(a release, a scene bed, a demo). When the writer supplies lyrics, preserve them.
When they provide only a brief and explicitly ask for a draft, use the refinement
model to draft sectioned lyrics and leave them editable before generation.

Split the input into the two things the runtime accepts. Everything about
*how it should sound* becomes style-tag prose. Everything about *what is sung*
stays in the lyric, word for word, in the writer's order.

## Structure

Tag the lyric into sections before generating — `[Verse]`, `[Chorus]`,
`[Bridge]`, `[Instrumental]`, `[Outro]`. One chorus phrase recurring verbatim
is what makes a chorus read as a chorus. Structure influences the result but
does not predict its length; report only a broad observed band and trim on the
timeline afterwards, because the request has no duration control.

## Style prose

Write the style as a comma-separated list of concrete, audible facts: language,
genre, tempo in BPM or a plain-language term, ensemble, vocal timbre and
count, production character, and emotional arc. Avoid artist names, and avoid
adjectives with no acoustic content. Every revision changes one tag and keeps
the seed, so the difference is attributable.

## Plan mode

Choose `full` when the harmony matters and the writer may want the ABC score
later. Choose `melody` when a supplied or authored melody must survive and the
band should be free. Choose `off` for a quick texture sketch, and never with a
score attached. State the choice in the notes so a later revision knows what
was tested.

## Review

Check the words first: intelligible, in order, no invented lines. Then the
arrangement against the tags, the chorus returning where the map says, and
whether the ending is a real ending. If the singer is buried, name the
instrument that is covering it. If the song drifts from the genre, remove the
style tag most likely to dominate rather than adding another. If the melody
repeats flatly, raise the plan mode one step instead of changing the lyric.
