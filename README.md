# HANNA

HANNA (Hansen's Notification Agent) reads a photograph of a handwritten leprosy
clinic card and turns it into a structured, validated case record: the fields
extracted, checked against the WHO standard, the home placed on a map, and a
clinician confirming each value before the record is finalised.

It is a test bench, not a national system. A country joins with its own card, in
its own language, and measures HANNA on its own test cards before anyone commits
to a deployment.

## Short video overview

https://github.com/user-attachments/assets/fb388927-3f10-4658-a4fa-07ba7d6e6289



## What it does

- **Reads the card** — front and back, photographed with a phone. No scanner, no
  transcription, no double entry.
- **Checks the record** — required fields, allowed values and clinical
  cross-checks against the WHO standard, with the country's own rules.
- **Shows its work** — every value is reviewed beside the crop it came from, so
  a clinician can confirm or correct it in seconds.
- **Places the home** — district, locality and village written on the card,
  resolved against a gazetteer and confirmed on a map at the level the data
  supports.
- **Produces the record** — a case with its visits, a feedback report for the
  reviewer, and an export mapping for the country's own system.

## What it is not

- Not a replacement for a register or for DHIS2. The country's system remains the
  system of record.
- Not a deployment. Nothing goes live in a programme during a test.
- Not a place for identifiable patient data. Test cards are dummy cards, or real
  cards anonymised with permission.

## What a country gets

- Accuracy measured per field on its own card, against values its own team
  confirms — a number it can check, not a vendor claim.
- The review screen, the map and the feedback report working on its card.
- A written report it can share inside the programme: what worked, where it
  failed, what it would take to fix.
- An export mapping from the shared concept list to its own system.
- No cost during the test.

## What a country supplies

1. A clean scan of the blank card, front and back.
2. Five to ten filled cards with known values — dummy cards, or real cards
   anonymised with permission. **This is the entry ticket:** without known values
   there is no accuracy to measure.
3. The location hierarchy: which administrative levels are used, their official
   names and codes, and whether a boundary set and facility list exist.
4. A focal point and one clinician who can confirm values.
5. A DHIS2 contact, to build the case-based data dictionary against WHO DHIS2.

## How it works

The pipeline is shared; a country is a folder of data.

```
core/                    one pipeline for every country
countries/mozambique/    one participating country: card, fields, rules, location
countries/_template/     the starter a new country copies
```

A country pack declares the blocks on the card, the value pairs inside them, the
concept each one maps to, the language, the validation rules and the location
hierarchy. The same code then runs for every country. When a country needs
something the core cannot express, it is added to the core behind an interface —
not forked.

## Where it stands (November 2026)

A working test build: phone capture of both sides, extraction, WHO validation,
value-by-value review, the case decision and the map. Measured on the Mozambique
card, per-block accuracy runs from about 80% to 98% depending on block and model
route; a front page takes about 10 seconds and a back page about 30. 206 tests
pass.

Not built yet: authentication and invitations, the hosted test server, the DHIS2
export adapter, and the form-definition assistant. The details and the dated
measurements are in [`docs/evidence.md`](docs/evidence.md).

## Getting involved

The test platform is opening to a small number of national programmes. The first
step is a conversation about your card and what you would want from the test;
the second is the country brief, which sets out what we ask for and what you get.

Write to **ariedk@lastmileroad.org**.

## For developers

- [`docs/DEVELOPING.md`](docs/DEVELOPING.md) — running it, the web layer, the API.
- [`docs/evidence.md`](docs/evidence.md) — what has been measured, and its limits.
- [`countries/_template/README.md`](countries/_template/README.md) — adding a country.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — data rule, DCO sign-off, pack checklist.

## Licence

Code is Apache-2.0. Documents and the concept index are CC BY-SA 4.0. A country
pack stays under the terms in its own `NOTICE.md`. See [`LICENSING.md`](LICENSING.md).

## Contact

Arie de Kruijff — ariedk@lastmileroad.org
