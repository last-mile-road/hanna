# 02 — Extraction: how the card is read

**In plain language:** the card photo is cut into regions (blocks), and each
block is read separately by a vision language model — the same kind of AI that
describes photos, here asked to return structured values: names, dates, tick
marks, counts. Reading block by block keeps each question small and checkable.
A rules layer then checks the answers against what a card can plausibly say
(required fields, allowed values, ranges). Nothing is trusted because a model
said it: every value can be compared against a human-verified ground truth,
and accuracy is always reported with its sample size.

---

## The pieces

- **Form pack** — `countries/<country>/form.draft.yaml`: the card as data.
  Blocks with pixel boxes on the blank template; inside each block, value
  pairs (id, concept, value type, optional box, printed label). Block types:
  `value_pairs`, `tick_grid`, `annotated_table`, `visual_annotation`,
  `dose_grid`, `free_text`, `sketch`. Contract: `hanna_next/CONTRACTS.md` §1.
- **Concept index** — `docs/concepts/concepts.yaml`: 68 WHO-aligned concepts
  with value types, observed card vocabulary, mark legends and sensitivity
  (identifying / clinical / administrative).
- **Extraction profile** — provider, model, settings, prompt version, crop
  strategy. Model knowledge lives in the profile, not the form.
- **Reader** — `core/inference.py` + `core/readers.py`; one call per block,
  parallel where the profile allows.

## Measured performance (2026-11-01, local stack)

Reader comparison and MinerU assessment: `docs/readers-comparison-2026-10-30.md`,
`docs/mineru-4x-tables-2026-11-01.md`. On the local stack (OpenRouter 9B +
MinerU on this machine) form16 reads 8 blocks in 2.5–4.3 s per block.
Model profiles measured on form16: Qwen3.5-27B 48/49; Qwen3.8-27B (reasoning
tuned) 47/49; 8B lower on the visual blocks.

## Rules that came from measurement, not taste

- A block crop must contain the printed label as well as the value (the nerve
  block went 8/12 → 12/12 when the header joined the crop).
- Treatment totals are derived from the monthly dose grid, not asked for.
- Visual blocks (hand/feet/skin) need their printed legend in the crop; the
  27B reads them, the 8B still swaps sides.
- Text scoring: exact after case/accent normalisation; fuzzy only for free
  text > 12 chars at ratio ≥ 0.9; numbers exact; dates ISO.
- Temperature 0 is not determinism: two identical runs changed 2/52 fields.
  Repeatability is measured, not assumed.

## Where ground truth comes from

Verified sheets per card (`ground_truth/`), captured through the review UI at
`tools/gt_capture/` (localhost:8765), with identifying values in a gitignored
sidecar. A value counts as scoreable only when the form has a pair for its
concept and that pair sits inside the block that was read.
