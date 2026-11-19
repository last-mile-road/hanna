# Baseline — what v5 actually scores, measured from corrected records

**Date:** 2026-10-23 (measurement of runs from 2025-11-30 to 2026-02-19)
**Source:** the v5 VPS backup (`cabo_delgado_admin.db`)
**Data:** `data/baseline/field_accuracy.csv` (aggregates only; no patient values leave the archive)

## The number

| | |
|---|---|
| Jobs in the database | 153 |
| Jobs with human-corrected values | 53 |
| Field judgements | 3,045 |
| Correct | 2,869 |
| **Field accuracy** | **94.2%** |

This is the "before" figure the iteration has to beat. It did not exist before:
`ground_truth/form16.json` held 3 fields and the other sheets held 10 and 3.

## Method, and what makes it trustworthy

`extraction_accuracy.corrected_value` is the value after the clinician corrected
the extraction in the feedback screen. `is_correct` compares extraction against
that corrected value with the metric chosen per field type: exact match, numeric
tolerance, or Levenshtein distance for text longer than three characters
(`metrics_service.py`).

Three caveats, the first of which is serious:

1. **Fifteen patients, not fifty-three cards.** The 53 reviewed jobs contain 15
   distinct patient names and 9 distinct government case numbers. One patient
   accounts for 1,117 of the 3,045 judgements (37%); the top three for 1,867
   (61%). The same physical cards were uploaded repeatedly during testing, so
   94.2% is much closer to "how well does v5 read one card, eighteen times" than
   to a per-card average. Any model comparison built on this number inherits the
   weighting.
2. **Selection bias.** Only 53 of 153 jobs were reviewed. Unreviewed jobs are
   excluded, so the figure describes cards someone chose to check, not all cards.
3. **Job-to-form attribution is lost.** `aligned_image_path` points at deleted
   VPS paths, so no row can be tied to `form16.jpg` or `form30.jpg` yet. The
   seven ground-truth sheets are what fixes that.

Caveat 1 is also the reason `hanna_next/CARD_IDENTITY.md` exists: without a case
key, the database cannot tell that it measured one person eighteen times.


## Where the errors are

The pattern is not random. Every one of the ten weakest fields is a field whose
answer sits inside a drawing or a grid, not in a text line:

| Field | Accuracy | n |
|---|---|---|
| `right_foot_dorsiflex` | 58% | 53 |
| `insensitive_points_left_hand` | 62% | 53 |
| `nr_skinpatches` | 70% | 53 |
| `insensitive_points_left_foot` | 74% | 53 |
| `insensitive_points_right_foot` | 74% | 53 |
| `ehf_right_foot` | 78% | 23 |
| `left_foot_dorsiflex` | 83% | 53 |
| `who_referred` | 83% | 53 |
| `gender` | 87% | 53 |

`who_referred` is the exception: a tickbox list where the card's five options
barely overlap WHO's list (see `concepts.yaml`). That is a vocabulary defect, not
a vision defect, and it is cheap to fix.

At the other end, `district` scores 100% over 38 judgements and the six `EHF_*`
fields 100% over 30. `body_area_percentage` also scores 100% over 53, which
almost certainly means the field is blank on every card rather than correctly
read. A 100% on a blank field measures nothing.

## Condition of this baseline

- One model dominates: `qwen/qwen3-vl-8b-instruct` and its `:novita` variant
  account for 146 of 153 jobs. Five other models appear with 1 to 3 jobs each.
  Those five are not a comparison; they are noise.
- `qwen/qwen3.5-35b-a3b`, the model named in `config.yaml`, appears in **zero**
  jobs. The configured model has never been measured.
- The deployed VPS backup predates the `reference_year` fix (`CONSTRAINTS.md`
  §2.3), so any birth-year figure here is suspect.

## What this means for the next step

The weakest fields are the ones `PURPOSE.md` §6 predicted: the three blocks v5
handled with bespoke functions and no declared value pairs. That is now a
measured claim rather than a design argument.

**Not verified:** whether the five alternative models were run on the same cards,
and whether `body_area_percentage` is genuinely blank on all 53 reviewed cards.
Both need a query against the archive, not the new repo.
