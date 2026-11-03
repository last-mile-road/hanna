# 04 — Review: the screen, the corrections and what they teach

**In plain language:** after HANNA reads a card, a person sees one screen with
the card photo, each value the model read, and the map with the patient's
location. They confirm what is right, fix what is wrong, and place the pin on
the patient's house (or at least their village). Every one of those actions is
recorded — which is how the system learns: corrections feed the ground truth
that measures accuracy, and confirmed place names teach the location resolver
the local spellings it did not know.

---

## The review screen (Bench)

- `static/review.html/js/css`, served per run at `/review?run=<id>`.
- Each declared value pair: what was read, the expected/ground-truth value
  where it exists, and the issue raised by validation, if any. Three actions
  per value: **accept**, **correct** (type the right value), **skip**.
- The location card: a static map composed server-side from OSM tiles (the
  patient's coordinates never go to the tile client), a ring sized to the
  precision of the match level, one question in Portuguese
  ("Onde fica a casa do doente?"), one confirmation, and a draggable pin that
  reopens after confirming. No "não sei" option: the instruction asks for the
  house, or at least the village.
- Developer detail (score, service use, cascade reason) is hidden behind a
  dev-mode switch; reviewers see the question, not the machinery.

## What review writes

| Action | Stored in | Feeds |
|---|---|---|
| accept / correct / skip per value | `observations.review_mode`, `observations.reviewed_value` | ground truth, accuracy |
| location confirm (`accepted`) | `runs.location.review` (+ moved_m) | moved_m distribution; **alias rows** |
| location candidate pick (`chosen`) | `runs.location.review` (+ moved_m) | **alias rows** |
| free pin drag (`chosen`, "ajustado no mapa") | `runs.location.review` (+ moved_m) | moved_m only — no alias |

Review decisions are stored *with* the original machine reading, never over
it: the pair (what the system said, what the human said) is the evidence that
later improvements are measured against.

## The measurement loop

- `tools/moved_m_report.py` — the moved_m distribution (see `03-location.md`
  §6). Run after each review round.
- Per-concept accuracy: `v_concept_accuracy`; per-run: `v_run_scores`.
- The bench rule: one consolidated verdict per pass, no early exit, and no
  review on a run that is going back for a re-photograph (2026-10-28 round).
