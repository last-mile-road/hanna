# Mozambique template — which file is the template

| | |
|---|---|
| File | `template.jpg` |
| MD5 | `92e2c6befb2c63e8403a83edeb70075f` |
| Dimensions | 3332 x 2502 |
| Source | `hanna_archive/versions/v4_hanna3_2025-2026/hanna_scalable/Mozambique_Leprosy_clinic_card_PT_front.jpg` |
| Identity | byte-identical to `hanna_v2/code/Mozambique_Leprosy_clinic_card_PT_front.jpg` (same MD5) |
| Card | FICHA CLÍNICA LEPRA, Ano 2010, Portuguese, blank |
| Tracked | yes — a blank government form, allowed in git |

## Do not substitute Ficha_clinica.jpg

`hanna_archive/versions/v4_hanna3_2025-2026/test_forms/Ficha_clinica.jpg` is the
**same form, a different scan**: 3332 x 2502 but MD5 `99e9c0344affa06138b9234244fd4bf4`,
851,813 bytes against 504,603.

Every coordinate in `form.draft.yaml` was measured against the file above. Swapping
in the other scan shifts the alignment template and silently moves every crop.
`tests/test_ground_truth.py` asserts the MD5, so a substitution fails the test
instead of quietly degrading extraction.

## Why the coordinate space matters

The v5 group boxes run to `x=3325, y=2492`, inside 3332 x 2502. A template of any
other size would make those numbers meaningless.
