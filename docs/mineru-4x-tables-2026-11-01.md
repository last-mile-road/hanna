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

## Pending

Whether `advanced` (xhigh, image analysis on) makes the treatment grid parseable, and
whether it changes the disability and nerve output. Measured on the box; results appended
below when they land.
