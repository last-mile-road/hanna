# MinerU assessment — what it adds, and what it does not

**Date:** 2026-10-29. Inputs: the five archives in `~/Downloads/MinerU_tests`
(MinerU 4.0.12), covering three cards — form16 (aligned card, plus a demographics crop and
a nerve crop), form19 (aligned card) and form21 (full card). The pipeline comparison uses
three fresh routed runs on the same cards (`purpose = mineru-compare`).

## What MinerU returns

One HTML table per card, with `colspan`/`rowspan` intact, printed labels inline with
handwritten values (`Ano de nascimento/Idade: 1972`, `Distrito: Balama`), checkboxes as
`☑`/`☐`, and the two diagram regions replaced by cropped images. No keys, no confidence,
no canonical values, no per-field bbox in the markdown (those live in `middle.json`, which
was not in the archives).

That adjacency — label and value in the same cell, in reading order — is the most valuable
property, because it is exactly the mapping the VLM prompt has to re-derive from a picture.

## Where it is strong

**Numbers, IDs and dates.** All 11 identifier/number/date values across the three cards are
present in MinerU's text: NIL `KDR-55-7210`, `82543260`, `82123321`, `1972`, `1968`, `1984`,
and the three diagnosis dates. The routed pipeline scored 9/11 on the same fields, and three
of its two misses turned out to be suspect ground truth (below).

**Clean tables.** On form19 MinerU read the nerve grid 12/12 (enlargement
`- - + - - -`, pain all `-`), the eye fields (`+`/`+`, `0`/`0`), the muscle table
(`N`/`N`, `N`/`F`) and all six disability grades, plus max and total. On form21 the same
tables are correct: grades `0/0`, `0/1`, `0/0`, max 1, total 1.

**Table structure survives.** `colspan`/`rowspan` plus the printed header row is what makes
a cell assignable to a nerve and a side at all. This is the part the VLM has to infer, and
it is where the VLM fails on form21: the routed pipeline transposed left and right on the
cubital nerve and read the radial right as `~` (7/12). MinerU kept the column order and lost
cells instead (8/12, with the last two columns dropped).

## Where it is weak

**Tick marks are inconsistent.** `☑`/`☐` appear on form16 and form19, and are absent
entirely on form21, where every option is printed with no mark. A field whose value *is* a
tick cannot be sourced from MinerU reliably.

**Cell loss and column merging.** form21's nerve header merges `D E` into one cell and
the row yields four values instead of six. form16's disability table loses the right-hand
column (three grades instead of six), though the max and total it prints are right.

**No canonicalisation.** MinerU returns what is on the card (`Caso novo`, `MB adulto`,
`veio sozinho`, `A partir do ano passado`). The pipeline returns canonical values. Turning
MinerU's text into scored fields needs a label-to-concept mapper — another model or a rule
set — which is the work the VLM currently does inside its call.

**Spelling drift on handwriting.** `Sledgehamme` for Sledgehammer, `Namgogetra`, `Na Cava`,
`velo sozinho`, `Gexo`. Readable, not exact.

## The finding that matters most: it audits the ground truth

Across the three cards the pipeline was marked wrong ten times. Five of those are not
extraction failures:

| card | field | pipeline | sheet | second reader |
|---|---|---|---|---|
| form16 | contact_number | 82543260 | 82543210 | 82543260 |
| form19 | contact_number | 82123321 | 821233210 (10 digits) | 82123321 |
| form19 | patient_name | Maria Jose de Souza | Marie Jose de Souza | Maria José de Souza |
| form16 | reference_person | Chefe da Aldeia | Chefe da Aldera | Chefe da Aldera |
| form21 | locality | Nangogotia | Namogelia | Namgogetra |

For the two phone numbers, the pipeline and MinerU read the same digits and the sheet
differs; a 10-digit Mozambican mobile and a `...210` ending are both implausible as card
values. `Aldera` is almost certainly `Aldeia` mistyped in the sheet. So the sheet, not the
reader, is the weak link on at least four fields, and form21's locality is unclear to all
three readers.

This is the strongest argument for keeping MinerU: two independent readers that fail in
different ways (the VLM swaps columns, the OCR drops cells) turn a silent wrong answer into
a flagged one.

## Cost and speed

No API cost: it runs locally. I did not measure wall time — this install reports no local
`basic`/`standard`/`advanced` engine (`No basic, standard, or advanced engine available`),
so I started the MinerU server, confirmed the error, and stopped it again rather than
change the configuration. Tell me which tier or extra your runs used and I will time it.

Whatever the number, it is a **desktop-GPU number**. `lastmile_werker` is a 4-OCPU ARM
instance with no GPU, so MinerU would run ONNX on CPU there, or not at all in the request
path. Treat "fast" as applying to this machine, not to the test server.

## What to do with it — ranked

1. **Second reader, moderator-side first.** Run MinerU on the aligned card, diff its text
   against the pipeline's observations, and flag disagreements — per cell, for tables.
   Offline script first (`benchmarks/`), wired into the review screen later. No API cost,
   no change to the extraction path, and it targets the failure modes we actually have.
2. **Ground-truth audit, now.** Run the two-reader diff across all seven sheets and fix the
   fields where both readers agree against the sheet. Cheap, and it moves the baseline:
   the 94.2% figure and today's 94.3% both rest on those sheets.
3. **Studio auto-draft.** Use MinerU's layout table (and `middle.json` bboxes) to propose
   blocks for a new country's card: printed labels become `label_local`, adjacency becomes
   candidate pairs, the header row becomes grid structure. The team then reviews instead of
   drawing 30+ boxes. This attacks the onboarding bottleneck in `PURPOSE.md` O1/O2, and it
   is where the largest programme value sits.
4. **Stage 2 privacy path.** Local OCR plus a local mapper would remove third-party egress.
   Not now: the mapper does not exist and the server has no GPU.

## What it is not

A replacement for the VLM. On these three cards 39–41 of the scoreable fields are choice or
tick fields, which text alone cannot decide, and the visual blocks are diagrams. MinerU
reads the paper; the pipeline reads the record.
