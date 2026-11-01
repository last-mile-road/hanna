# MinerU 4.x — table handling, and what we were not using

Research notes, 2026-11-01, against MinerU 4.1.3 (the version in the box image) and the
4.0/4.1 docs. Written because the treatment grid comes back as a picture and the question
was whether that is a configuration we missed.

## The tiers are efforts, and `--tier` is a ceiling

From `mineru/parser/tier.py`:

| tier | effort | what runs |
|---|---|---|
| `flash` | flash | native text, Flash OCR for scans |
| `basic` | medium | ONNX/Torch small models, no VLM |
| `standard` | high | small models + VLM |
| `advanced` | xhigh | standard plus more inference computation |

The docs are explicit: **"The API startup `--tier` is a ceiling including itself and lower
tiers; `--tier standard` does not enable Advanced."** The box was started with
`--tier standard`, so advanced requests could never be served, whatever the client asked
for. Advanced shares the standard weights — enabling it is a flag, not a download.

The one behavioural difference that matters for tables: **image analysis runs only at
xhigh**. In `backend/analysis/pdf/window.py` the inference options are
`{"image_analysis": image_analysis if effort == "xhigh" else False}`, and the 3.3
changelog says medium "does not support image analysis; for maximum parsing accuracy or
image analysis support, switch to effort=high" (high = standard). So `--disable-image-analysis`
is a flag that does nothing unless advanced is enabled.

## Why the treatment grid becomes a picture

The layout model (PP-DocLayout v2) carries both `table` and `chart` labels
(`model/layout/pp_doclayout_v2_base.py`). The grid is labelled **chart**, so:

- the table structure model (SLANet Plus, ONNX, CPU) never sees it — it only runs on
  regions labelled `table`;
- markdown renders the region as an image, which is what we saw:
  `![](images/page_0_chart_0.jpg)`, 30 characters at every tier.

`output_formats` (markdown / middle_json / structured_content / zip) decides how the same
parse is packaged. It cannot turn a region labelled chart back into cells, which is why
asking for structured content would not have helped either.

## Configuration we were not using

| setting | where | what it does | relevance here |
|---|---|---|---|
| `--tier advanced` at startup | `mineru-api` | enables xhigh effort and image analysis | **the one we missed**; untested until now |
| `--disable-image-analysis` | `mineru-api` | turns the xhigh image pass off | only meaningful at advanced |
| `--ocr-mode auto\|txt\|ocr` | parse request | force OCR rather than native text | crops are images, so `auto` already OCRs |
| `MINERU_TABLE_MERGE_ENABLE=0` | env | disables cross-page table merging (on by default since 2.6.2) | no cross-page tables in a crop; harmless |
| `llm_aided.features.cross_page_table_cell_merge` | config | LLM post-processing to merge cells across pages | could point at our own vLLM; not a single-crop problem |
| `llm_aided.features.title_leveling` | config | LLM post-processing of headings | not a table problem |
| `--vlm-server-url/-model/-api-key` | `mineru-api` | remote VLM instead of in-process | already used (the box serves its VLM) |
| `model.small_backend: onnx\|torch` | config | which runtime for layout/table/OCR models | onnx on CPU is what we run |

MinerU 4.0 also advertises nine render targets (Markdown, HTML, LaTeX, DOCX, EPUB, PDF,
Structured Content, Content List V1/V2), but each CLI/API exposes a subset; the parse API
here gives markdown, middle_json, structured_content and zip.

## What the model is supposed to be good at

MinerU2.5's own claims, relevant to the cards: "drastically improves parsing for
challenging cases, including rotated tables, borderless/semi-structured tables, and
long/complex tables", plus cross-page table merging and "image recognition inside tables".
The disability and nerve tables we measured (98/98 and 84/84 cells) are exactly this
strength. The treatment grid is a bordered 30-day calendar: a table to a human, a chart to
the layout model.

## Measured: advanced reads the treatment grid (2026-11-01)

The same four crops through the box, `?tier=standard` against `?tier=advanced`:

| crop | standard | advanced |
|---|---|---|
| treatment | 30 chars, 0 tables, 1 image, 3.4 s | **1,340 chars, 1 table, 0 images, 8.0 s** — `Janeiro … Dezembro` headers, one row per year |
| def_exam | 871 chars, 1 table, 3.9 s | 871 chars, 1 table, 4.3 s (noughts come back as `O`; the mapper already handles that glyph) |
| nerve | 385 chars, 1 table, 2.9 s | 385 chars, 1 table, 2.8 s |
| whole card | — | 2,053 chars, 3 tables, 0 images, 15.7 s, but none of the printed labels (`Pestanejo`, `Grosso`) survive |

So advanced is the missing configuration for the grid, and it costs nothing on the two
tables that already worked. The whole-card parse is not a substitute for the crops: it
returns tables without the row labels the mapping needs.

Consequence: `core/readers.py` now requests `tier=advanced` for every reader block, and
`treatment_follow` is routed to it. The start date, dose count, month count and missed
count are still derived by `core/treatment.py` from the grid, not read separately.

## A second thing this boot exposed

The redeploy landed on an **RTX PRO 6000** (the ordered GPU fallback working as intended,
96 GB, $3.03/h instead of the L40S at $1.95/h), and there the 9B refused to start:

```
ValueError: max_num_seqs (1024) exceeds available Mamba cache blocks (321)...
```

Qwen3.5 has Gated DeltaNet layers, so every decode sequence needs a Mamba cache block;
with 6 GiB of KV the default 1024 sequences do not fit. Fixed with `--max-num-seqs 64`,
which is more concurrency than the pipeline uses anyway. It had not bitten on the L40S
because that card's KV arithmetic was different.

