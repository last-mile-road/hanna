# Ground truth — Mozambique card

One file per card. Accuracy is measured against these files and nothing else.

**The cards are dummy cards.** The patient details on the test cards are invented
or anonymised; no real patient data is held in this repository or in the test
set. If a card ever comes from a real register, it enters only with the
programme's written permission and with the identifying fields removed.

## Where the sheets live

The sheets are **country data, not source**. They are not tracked in git. The
working copies sit outside the repository, in the country data directory:

```
country_data/<country>/ground_truth/*.json
country_data/<country>/ground_truth/_identifying/<form>.json
country_data/<country>/ground_truth/_evidence.yaml
```

`HANNA_COUNTRY_DATA` points at that directory (see `.env.example`).
`tools/load_pack.py` reads it first and falls back to
`countries/<country>/ground_truth` if it does not exist. Tests that need the
sheets skip when the directory is absent, which is what a public clone sees.

The eventual home is the country portal: same shape, one folder per country,
with the country's own account controlling it.

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
      "source": "owner reading, 2026-10-23"
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
   `"status": "verified"`, `"source": "owner reading, YYYY-MM-DD"`.
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

Seven fields never enter the sheet itself. They are stored in
`_identifying/<form>.json`, merged back in when the benchmark loads the sheet, so
they are still scored. Both the sheet and the sidecar sit in the country data
directory, outside git.

## What this card does not carry

`countries/mozambique/card_scope.yaml` names the concepts this card does not
print: `mode_of_detection` and `retreatment_type`. They are left out of the
sheets rather than left pending, so a fully read card can reach "verified".
A field that exists but is blank (`transferred_from`, `treatment_completion_date`)
is not in that file; it stays pending until someone reads it.

## Evidence ledger

`_evidence.yaml` holds the raw statements each verified value came from,
including the nerve column order (cubital right, cubital left, radial right,
radial left, popliteal right, popliteal left) taken from
`hanna_archive/notes/nerve_coordinates.txt`. If a value's source is not in the
ledger, it does not belong in a sheet. It lives with the sheets, outside git.
