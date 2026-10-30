# Tick strips vs the block prompt — measured 2026-10-30

Seven cards, the same 9B model. Arm A is the current pipeline: one call for the whole
clinical-history block and one for the demographics block, nine fields competing. Arm B is
one call per question group, on the rendered strip (boxes only, 56 px white margin, band
numbers at 1.2, question and options in the prompt).

| concept | block prompt | tick strip |
|---|---|---|
| itching | 5/7 | **7/7** |
| household contact | 5/7 | **7/7** |
| previous treatment | 7/7 | 7/7 |
| referred by | 7/7 | 7/7 |
| gender (cut-and-stack) | 6/7 | **7/7** |
| **total** | **30/35 (86%)** | **35/35 (100%)** |

The five fields the block prompt missed were all ticks; the strips fixed every one,
including the case where two ticked boxes were resolved silently.

Sample size is seven cards and 35 judgements, so treat 100% as "no failure observed", not
as a guarantee. The next run should include the cards with no truth yet (case category and
leprosy classification are still pending in the sheets) and a second pass for repeatability.

Numbers here are local to each call: the prompt carries the question and the band→option
mapping, so four calls with four "band 1"s cannot be confused with each other.
