# Local readers on the hard blocks — corrected comparison

**Date:** 2026-10-30 (supersedes the first version of this file, same day).
**Readers:** MinerU 4.0.12, tier `standard`, local API · PaddleOCR-VL 1.6 0.9B (official
GGUF, llama.cpp Vulkan) through `paddleocr doc_parser`.
**Cards:** form16, form19, form21, form22, form23, form25, form30 — all seven sheets.

## Correction: what the first pass got wrong

The first version reported MinerU at 79/98 on the disability table and 0/84 on the nerve
grid, and concluded that both tables stay with the VLM. That was wrong, for three reasons.

1. **Wrong MinerU path.** The `mineru` command on PATH is the docvortex CLI. Without a
   local parse server it falls back to `--tier flash`, a text-only preview that returns
   image placeholders for tables. The web UI the owner uses runs `mineru-api` at tier
   `standard`: layout + slanet-plus table structure + the MinerU2.5 1.2B VLM, all local.
   Nothing was sent to an external service. The 2026-10-29 archives were local output too;
   the claim that they came from mineru.net was my inference and it was incorrect.
2. **Raw text instead of tables.** Both readers export tables as HTML. Scoring the model's
   text stream loses the column, so a correct read of `0 | Olhos | 2` was scored as a
   missing value. The disability table and the nerve grid are now parsed as tables.
3. **No column alignment on the treatment grid.** Paddle's grid has the day cells but the
   first pass never aligned them to the month header. With alignment, the grid derives
   start date, doses, months and missed correctly.

The corrected numbers follow. Where the readers win, they win by enough to change routing.

## How each reader returns a table

- **MinerU** (`mineru-api`, `tier=standard`) returns Markdown with a real HTML `<table>`
  per block, `colspan`/`rowspan` intact, and `middle_json` with block and cell geometry.
  It also returns HTML/`content_list`/`docx` if requested. Tables are detected by layout
  and structured by slanet-plus.
- **PaddleOCR-VL** (`paddleocr doc_parser`) detects layout with PP-DocLayoutV3, sends the
  matching trained element prompt, and returns HTML `<table>` plus `.md` and `.docx`.
  Called directly with `Table Recognition:`, the model emits `fcel/lcel/ucel/ecel` cell
  tags that the client converts to HTML; either form can be parsed into a grid.
- On a table crop, both are deterministic. There is no need to interpret a text stream.

## Results by block

Pair counts are the verified pairs that are scoreable on that block; treatment is scored on
four derived fields per card (start, doses, months, missed).

| block | pairs | routed pipeline | MinerU standard | PaddleOCR-VL |
|---|---|---|---|---|
| disability table (`def_exam`) | 98 | 89 (90.8%) | **98 (100%)** | **98 (100%)** |
| nerve grid | 84 | 79 (94.0%) | **84 (100%)** | 48/72 measured (3 empty tables) |
| treatment grid | 23 checks | 21 (91.3%) | no grid (image block) | **22 (95.7%)** |
| patient demographics | 42 | **37 (88.1%)** | 36 pragmatic (34 strict) | 34 pragmatic (32 strict) |
| hand/feet counts | 28 | 26 (92.9%) | 0 | 0 |

### Disability table

Both readers are exact on all seven cards. The route's 11 misses are the 9B on form22
(1/6 grades) and form30 (4/6), plus one tick. MinerU and Paddle read both columns, the
`+`/`−` ticks, the lagophthalmos values and the muscle letters. Example, form16:
`Olhos 0|2, Mãos 0|1, Pés 1|2`, max 2, total 6 — the truth, from a block where the earlier
flash pass returned four of six grades.

Handwritten glyph variants are handled by the mapper, not the reader: open zero `○`/`C`,
slashed one `/`, `I` for 1, `Z` for 2. The card's value is right; only the glyph differs.

### Nerve grid

MinerU returns the full 12-cell grid on every card:

```
              cubital   radial cut.  popliteal ext.
Grosso          - +        - -          - +
Dor             - -        - +          - -
```

That is form16, and it matches the card. Paddle returns the same grid on four cards but an
empty table on form19 and form23 and no table on form22, so it is not a nerve reader as
configured. MinerU is.

The "count the pluses, assume everything else is minus" rule is implemented: a blank cell
scores as `-`, and a fully blank grid is treated as a reader failure, not as twelve minuses,
when the card has pluses.

### Treatment grid

Paddle returns the grid rows with the day cells. Aligning each `Dia` row to the month header
and padding a dropped empty cell gives, for form22:

| derived | value | truth |
|---|---|---|
| start | 2022-02-18 | 2022-02-18 |
| doses | 5 | 5 |
| months | 7 | 7 |
| missed | 2 | 2 |

form23 (start 2024-07-24, 6 doses, 8 months, 2 missed), form25, form30 and form21 also
match. Only form16's day is wrong: the reader returned `3/5` for `31/5`. Total 22/23 against
the route's 21/23. MinerU classifies the grid as a picture and returns no cells.

### Patient demographics

| reader | strict | pragmatic | misses after pragmatic matching |
|---|---|---|---|
| routed pipeline 9B | — | 37/42 | form16 phone, form19 phone |
| MinerU standard | 34/42 | 36/42 | 2 phones, form21 name, 2 localities, form23 NIL |
| Paddle, `OCR:` on the block | 32/42 | 34/42 | form16 NIL/name, form21 year, form22 village, form23 NIL, 3 localities |

Pragmatic means one-character slips and lost word spaces are forgiven
(`SledgehammeAno`, `Naman humbire`), as a clinician or a Portuguese name list would. The
remaining locality misses are the gazetteer case (`Namogelia` written `Namgogetra`), which
no reader can infer. The remaining NIL miss is a code: exact is the right standard there.

The pipeline is still one value ahead of MinerU on this block. It is not 20 points ahead,
and the gap is handwriting slips and one code, not field assignment.

## Speed

| reader | all 42 crops | per block crop | notes |
|---|---|---|---|
| MinerU standard | 116 s | ~2.8 s | local API, layout + table + 1.2B VLM |
| Paddle doc_parser | 168 s | ~4.0 s | PP-DocLayoutV3 + 0.9B VLM |
| Qwen 9B | — | 4–5 s | current route |
| Qwen 27B low | — | 100–559 s | latency reference |

## What this changes

1. **MinerU standard is the table reader to route to.** 98/98 on the disability table and
   84/84 on the nerve grid, better than the routed VLM (89/98 and 79/84), local, free and
   about 3 s per block. This replaces the 27B on `def_exam` and the 8B on the nerve grid;
   it does not supplement them.
2. **Paddle's grid parser beats the 8B on treatment** (22/23 against 21/23) and matches it
   on the disability table. The 8B remains the cheapest read and the fallback.
3. **Ticks are read by MinerU standard.** `Pestanejo +/-` came back as `+`/`-` on every
   card, and the nerve marks too. "Ticks stay with the VLM" is true for the hand/feet
   diagram, not for the tables.
4. **Demographics stays VLM-first with a reader cross-check.** The 9B leads by one value;
   Paddle's OCR prompt and MinerU both catch the numbers the 9B truncates (form19's phone
   in Paddle's case). A disagreement flag is cheap.
5. **Hand/feet stays with the VLM**, as the owner said: the block is a diagram with more
   than ticks on it, and both readers return only the printed legend.

## Caveats

- MinerU standard is a local VLM pipeline (MinerU2.5 1.2B), not classical OCR. Its table
  quality is a property of that model. The flash tier behind the plain `mineru` CLI is a
  different, much weaker path and must not be used for measurement.
- Ground truth is shared with the pipeline and not re-audited here. Known defects remain:
  form16 phone, form21 locality, form19 name unconfirmed.
- Paddle's nerve failures are model/layout behaviour on that crop, not a parsing artefact;
  reported as measured.
- Treatment scoring uses my derived-field parser; the four fields are checked against the
  sheet, not against the grid cell by cell.

## Reproduce

```bash
# MinerU standard API (local, the tier the web UI uses)
mineru-api --host 127.0.0.1 --port 8001 --tier standard --allow-local-source --preload-models

# PaddleOCR-VL (GPU)
cd ~/Public/PaddleOCR-VL
tools/llama/vulkan/llama-server -m models/PaddleOCR-VL-1.6-GGUF.gguf \
  --mmproj models/PaddleOCR-VL-1.6-GGUF-mmproj.gguf -ngl 99 \
  --host 127.0.0.1 --port 8118 --ctx-size 16384

cd ~/Documents/hanna_project/hanna_server
python benchmarks/readers_standard.py            # all blocks, both readers, HTML
python benchmarks/table_compare.py --rescore     # tables, cell by cell
python benchmarks/treatment_compare.py           # grid -> derived fields
```

Outputs: `output/readers_standard.json`, `output/table_compare.json`,
`output/treatment_compare.json`; HTML and crops in `/home/sam/tmp/opencode/{standard,tables,readers}/`.
