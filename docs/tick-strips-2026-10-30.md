# Tick strips vs the block prompt — measured 2026-10-30

Seven cards, the same 9B model. Arm A is the current pipeline: one call for the whole
clinical-history block and one for the demographics block, nine fields competing. Arm B is
one call per question group, on the rendered strip: the crop is the boxes plus 20 px, then
56 px of white is added on the left and the band numbers are drawn into that margin at 1.2.

| concept | block prompt | tick strip |
|---|---|---|
| itching | 5/7 | **7/7** |
| household contact | 5/7 | **7/7** |
| previous treatment | 7/7 | 7/7 |
| referred by | 7/7 | 7/7 |
| gender (cut-and-stack) | 6/7 | **7/7** |
| case category | not scored in arm A | **7/7** |
| leprosy classification | not scored in arm A | **7/7** |
| **total** | **30/35 (86%)** | **49/49 (100%)** |

Every field the block prompt missed was a tick.

## Notes

- Each call is self-contained: the prompt carries that group's question and its band→option
  mapping, so four calls with four "band 1"s cannot be confused with one another.
- Case category and leprosy classification are stored in the sheets under their **concept**
  keys (`case_category`, `classification`), not their pair ids. Looking them up by pair id
  made them appear unverified; they are verified on all seven cards.
- Sample size is seven cards and 49 judgements. Treat 100% as "no failure observed", not as
  a guarantee: repeat once, and add the cards still missing truth for other fields.
- Geometry lives in `countries/mozambique/tick_columns.yaml`; the rendering is
  `tools/render_tick_strip.py`, so the benchmark image and the production image are the
  same code path.
