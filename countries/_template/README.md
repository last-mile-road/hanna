# _template — the starter pack for a new country

Copy this folder to `countries/<name>/` and rename the files. Four things, no code.

```
countries/<name>/
├── form.yaml          blocks, value pairs, coordinates, sensitivity
├── template.jpg       the blank card, unused by any code
├── location.yaml      hierarchy declaration and which tier backend
└── ground_truth/      dummy cards plus their known-correct values
```

## form.yaml, minimum shape

```yaml
form:
  id: <country>_<form>
  country: <country>
  template: template.jpg
  status: draft
  reference_year: 2026      # required, never defaulted (CONSTRAINTS §1.6)

blocks:
  - id: patient_identity
    block_type: value_pairs          # value_pairs | tick_grid | annotated_table | visual_annotation
    bbox: [x, y, w, h]               # on the template
    value_pairs:
      - id: patient_name
        concept: patient_name        # must exist in docs/concepts/concepts.yaml
        value_type: person_name
        bbox: [x, y, w, h]           # optional; omit to use the whole block crop
        sensitivity: identifying
```

A `concept` outside the index needs `concept: local.<name>`, a `label`, and
`tier: local` (PURPOSE.md §6.1.1).

## What the onboarding asks the country team for

1. the blank template;
2. blocks drawn on it, each with a block type;
3. value pairs inside each block, each mapped to a concept;
4. which fields are identifying;
5. dummy cards with known values in `ground_truth/`.

A country that cannot supply item 5 cannot measure accuracy, so it cannot build
confidence. That is the entry ticket, not an afterthought.
