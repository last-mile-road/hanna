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

## Proposed back boxes and strips — 2026-11-10

Measured from the template's printed rules (row/column projections), **not yet
applied** to `form.back.yaml`. Awaiting owner confirmation.

- `back-blocks-proposed-2026-11-10.jpg` / `.json` — 4 visits × 6 sections plus
  the 5 reaction/outcome panels. The visit header now includes the title, `Data:`
  and `Preenchido por:`; the nerve block includes the printed `Palpação` header
  and `+/-` legend; the sensory block includes hands **and** feet; eye, muscle and
  disability sit at the bottom of each column, not inside the feet.
- `back-reaction-strips-2026-11-10.jpg` / `.json` — the 10 printed sim/não
  questions (3 history + 7 exam), one strip per question with `sim` and `não`
  bands. The question text is carried by the prompt; the strip is the two boxes.
- `back-outcome-ticks-2026-11-10.jpg` — the outcome `Data:` line and the four
  outcome checkboxes (Alta, Abandono, Falecido, Má classificação); `Outro` and
  `Transferido para` are free-text lines.

### v2 after owner review

- Eye exam ends at 1754, not 1839; the muscle-test header starts at 1754.
- Muscle test ends at 1949; the `Grau de deformidade` header starts at 1949.
- Outcome ends at 2195, leaving writing/descender space below the
  `Transferido para:` line and stopping above the `Integrado...` header.
- Self-care starts at 2195 and ends at 2296, below the `de:` response line.
- `back-blocks-proposed-2026-11-10-v2.jpg` / `.json`;
  `back-outcome-ticks-2026-11-10-v2.jpg`.

### v3 — front parity, final proposal

- One wide `visit_index` block across the four headers (6–159).
- Per visit: `nerve` 159–517, `sensory` 517–1614, **`def_exam` 1614–2302**.
  `def_exam` is one block per visit, exactly like the front: eye exam + muscle
  test + disability scores in one `annotated_table`, routed with the front
  `def_exam` profile (MinerU on the local route). The internal review tables
  still split eye / muscle / disability.
- Outcome: one `outcome_choice` strip 1760–2020 with the four checkboxes;
  `outcome_date` 1663–1718; `outcome_free` 2020–2195 (`Outro`,
  `Transferido para`).
- Reaction strips unchanged.
- `back-blocks-proposed-2026-11-10-v3.jpg` / `.json`.
