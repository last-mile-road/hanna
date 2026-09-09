# ground_truth/ — gold-standard extraction data

Ground truths come from `hanna/AI_self_make.txt` (owner-provided expected values for the
Mozambique template, `Mozambique_Leprosy_clinic_card_PT_front.jpg`).

Each file is one form's expected key/value pairs, e.g. `form16.json`:

```json
{
  "form_id": "form16",
  "image_path": "/absolute/path/to/aligned/form16.jpg",
  "expected": {
    "nerve_enlargement": "-,+,-,-,-,+",
    "nerve_pain": "-,-,-,+,-,-",
    "skin_patches_total": "4"
  }
}
```

Notes:
- `image_path` should point at the **aligned** crop when available (benchmark should compare
  aligned vs aligned+background-subtracted).
- `numeric_fields` (e.g. `skin_patches_total`) are scored with ±tolerance (see harness).
- Add more forms and more countries as their ground truths are validated (Phase 3).

Seeded from `hanna/AI_self_make.txt` (form16/19/21). Verify exact expected values against the
source before running a real benchmark.
