# Ground truth — Mozambique card

One file per card. Accuracy is measured against these files and nothing else.

| Form | Status | Verified | Note |
|---|---|---|---|
| form16 | partial | 51/62 | |
| form19 | partial | 47/62 | |
| form21 | partial | 46/62 | |
| form22 | partial | 49/62 | |
| form23 | partial | 49/62 | |
| form25 | partial | 47/62 | |
| form30 | partial | 48/62 | |

**form20 is not in the set.** It is a duplicate of form16 (owner, 2026-10-23), so
keeping both would double-count one card in every benchmark run.

Remaining gaps on each sheet are fields that are genuinely blank on that card,
plus the identifying fields, which live in the gitignored sidecar. The new
`reference_person` field is pending on every sheet; the card's "Pessoa de
Referência" is a community contact, not a patient ID.

## Schema

```json
{
  "form_id": "form16",
  "image": "form16.jpg",
  "template": "template.jpg",
  "status": "partial",
  "fields_verified": 13,
  "fields_total": 61,
  "last_updated": "2026-10-23",
  "reviewed_by": null,
  "fields": {
    "skin_patch_count": {
      "value": "4",
      "status": "verified",
      "source": "hanna_archive/notes/AI_self_make.txt (line 23)"
    },
    "patient_name": {"value": null, "status": "pending", "source": null}
  }
}
```

Rules the tooling enforces (`tests/test_ground_truth.py`):

- field keys are concept ids from `docs/concepts/concepts.yaml`, nothing else;
- a `verified` value must name its source and must be an allowed option for that concept;
- a `pending` field has no value;
- the sheet's `fields_verified` count matches reality.

## How to fill a sheet

1. Open the card image next to the JSON.
2. For each field you can read, replace `null` with the value and set
   `"status": "verified"`, `"source": "owner reading, 2026-10-23"`.
3. Leave fields you cannot read as `pending`. A wrong truth is worse than a null:
   it turns a correct extraction into a failure.
4. Set `reviewed_by` and re-run:
   `~/.venv/bin/python tools/new_ground_truth_form.py --form form16`
   to refresh counts. Values you marked verified are never overwritten.

Do not let a model fill these sheets. The archive contains extraction output for
many of these cards, and importing it would make the benchmark measure agreement
with v5 rather than with the card.

## Literal text, English enumerated values

Enumerated fields are stored in English even when the card prints an
abbreviation: `N`/`F`/`A` become `Normal`/`Weak`/`Paralysed`, and the mapping is
declared on the concept as `card_marks`. Free text stays exactly as written,
including Portuguese, because translating it would be an interpretation.

Gender is the exception still in card form: `M`/`F`, not `Male`/`Female`. Fixing
that needs a decision on whether the sheet or the normaliser does the mapping.

Scoring therefore compares in the sheet's vocabulary. If validation later
canonicalises to WHO terms, both sides must move together.

## Field notes worth knowing before reading a card

- `eye_blinking_*` is the blink reflex, observed passively. Absence suggests loss
  of corneal sensation. It is not eye closure.
- `eye_lagophthalmos_*` is the eyelid gap in mm after asking the patient to close
  the eyes. 0 means full closure. It is not a 0/1/2 grade.
- `disability_grade_*` is the clinician's own score in the table at the foot of
  the exam. Record it as written, never compute it from the rows above.

## Identifying fields

Seven fields never enter the committed sheet. They are stored in
`_identifying/<form>.json` (gitignored) and merged back in when the benchmark
loads the sheet, so they are still scored.

## What this card does not carry

`countries/mozambique/card_scope.yaml` names the concepts this card does not
print: `mode_of_detection` and `retreatment_type`. They are left out of the
sheets rather than left pending, so a fully read card can reach "verified".
A field that exists but is blank (`transferred_from`, `treatment_completion_date`)
is not in that file; it stays pending until someone reads it.

## Evidence ledger

`_evidence.yaml` holds the raw statements each verified value came from, including
the nerve column order (cubital right, cubital left, radial right, radial left,
popliteal right, popliteal left) taken from
`hanna_archive/notes/nerve_coordinates.txt`. If a value's source is not in the
ledger, it does not belong in a sheet.
