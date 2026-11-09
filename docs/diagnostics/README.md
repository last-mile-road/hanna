# Block coordinate diagnostics — 2026-11-09

Blank-template overlays of the block boxes the extractor uses. Generated from
`countries/mozambique/form.draft.yaml` and `form.back.yaml`; no patient data.

- `front-blocks-2026-11-09.jpg` — 8 blocks; broadly correct. The front extraction
  and review are working.
- `back-blocks-2026-11-09.jpg` — 29 blocks; visibly misaligned:
  - `visitN_header` starts below the printed `Data:` line, so the evaluation
    date is outside the crop.
  - `visitN_sensory` ends at the top of the feet; `visitN_eye` starts there and
    includes the feet.
  - `visitN_muscle` / `visitN_disability` boundaries are shifted.
  - The reactions/outcome panels are roughly right, but the outcome date sits
    at the top of a large block and was missed.
- `.json` files hold the same boxes as `[number, block_id, type, bbox]` for
  measurement scripts.
