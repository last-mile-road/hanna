# Countries

Every country is a folder. Mozambique is the first participating country, not
the default one: the core pipeline takes the country from the form pack and
must not name Mozambique anywhere. Where it still does, that is a known leak,
not a design.

```
countries/
├── _template/            the starter a new country copies
└── mozambique/           a participating country
    ├── form.draft.yaml   front: blocks, value pairs, concepts, sensitivity
    ├── form.back.yaml    back
    ├── labels.yaml       printed labels and box corrections
    ├── validation.yaml   required fields, rules, Portuguese advice
    ├── location.yaml     hierarchy declaration and tier backends
    ├── gazetteer/        COD-AB units and facility list
    ├── report/           feedback report template and field selection
    ├── docs/             Mozambique-only diagnostics and notes
    └── ground_truth/     README only; the sheets are country data (see below)
```

## Two kinds of file

| Layer | What varies | Where | Changed by |
|---|---|---|---|
| **Core** | nothing | `core/`, `server/` | the project |
| **Country pack** | card layout, fields, language, validation, location, report | `countries/<name>/` | the country team, with help |

A country pack is data. If a variation needs code, it goes into core behind an
interface, and that is the exception.

## Country data is not source

Ground truth, identifying sidecars and the evidence ledger are country data, not
code. They live outside the repository:

```
country_data/<country>/ground_truth/
```

`HANNA_COUNTRY_DATA` points at the directory. The portal will hold the same
shape per country, under that country's account. Never commit a country's test
cards or confirmed values to git.

## Test material

All test cards are dummy cards with invented patients. A real card enters only
with the programme's written permission and with the identifying fields removed.
The public repository ships no cards at all: `template.jpg` is the blank form.

## Adding a country

Read `countries/_template/README.md`. The entry ticket is a blank template plus
five to ten dummy cards with known values; without known values there is no
accuracy to measure.
