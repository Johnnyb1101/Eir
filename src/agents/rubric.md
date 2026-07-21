# Critic Rubric — grading one slide

The grader is given: one slide (title, bullets, speaker notes), its outline
entry (title, learning objective), and the full text of every chunk the
slide cites.

Pass rule: all answers "yes" = pass. Any "no" = fail. Every "no" must come
with a note naming which question failed and why, specific enough for the
writer to fix it.

Taken together, do the slide's bullets and speaker notes address the learning objective? (Bullets are brief cues; the speaker notes carry the explanation. Do not fail the bullets for lacking detail that belongs in the notes.)
Does the cited chunk text actually state each claim the slide makes?
Are numbers, doses, and thresholds on the slide or speaker notes identical to the source?
Do the speaker notes agree with the slide's bullets (no contradiction between them)?
Do the speaker notes have cited chunk text that actually state each claim in the speaker notes?

## Checked by code, not by the critic

- Slide times sum to the requested duration (arithmetic)
- Every cited chunk ID exists in this run's retrieval results (string lookup)
- Source/section/page metadata matches the chunk (copied by code, tested once in pytest)