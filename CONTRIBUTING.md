# Contributing

HANNA reads a photographed leprosy clinic card into a validated, structured
case record. It is a test bench for co-developing that with endemic countries,
not a production clinical system.

Two ways to contribute. Read the data rule first.

## The data rule

**Never commit a real patient card, a real patient identifier, or anything
derived from one.** Test material is dummy cards, synthetic forms, or cards
supplied with explicit permission for the test. This applies to issues, pull
requests, screenshots and test fixtures. A pull request that contains patient
data is closed without discussion, and the history is rewritten.

Never commit credentials. `.env` is ignored; keep it that way.

## Track 1 — a country pack

A country pack is data, not code: `countries/<name>/` with a form definition, a
blank template, a location declaration and ground truth. Start from
`countries/_template/README.md`; it lists the five things a pack must contain
and the four things a country team is asked for.

A pack is ready for review when:

- every block has a type and every value pair maps to a concept in
  `docs/concepts/concepts.yaml`, or declares `concept: local.<name>` with a
  label and `tier: local`;
- the template is a clean, deskewed scan, and the geometry in the form file
  matches it;
- there are at least five dummy cards with known-correct values, so accuracy
  can be measured;
- the identifying fields are flagged (`sensitivity: identifying`);
- `NOTICE.md` records who supplied the pack and under what permission.

Add a country through the "Country pack" issue template before opening a pull
request. Concept requests go through the same route.

## Track 2 — code

- One change per pull request, with the reason in the description.
- Run the test suite (`python -m pytest tests -q`); a change without a passing
  run is not reviewed.
- New behaviour gets a test. Parsers, validation rules, money and security
  paths always do.
- Do not add a dependency without saying why in the description.
- Keep `countries/` data-only. If a country needs a new code path, it belongs
  in `core/` behind an interface.

## Sign-off

Every commit carries a Developer Certificate of Origin 1.1 sign-off:

```
git commit -s -m "your message"
```

which adds:

```
Signed-off-by: Your Name <you@example.com>
```

That is your statement that you wrote the change, or have the right to submit
it, under the licence of the layer it touches. No separate contributor licence
agreement is required.

## Review

One maintainer reviews. Expect about a week; there is no service-level
agreement. A review may ask for a smaller change, a test, or a data check.
Disagreement is fine; argue it in the pull request, not in email.

## Licence of contributions

Code is Apache-2.0, documents and the concept index are CC BY-SA 4.0, and a
country pack stays under the terms recorded in its `NOTICE.md`. See
`LICENSING.md`.
