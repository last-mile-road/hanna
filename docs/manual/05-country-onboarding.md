# 05 — What a new country does to come on board

**In plain language:** a country joins with three things: photos or scans of
its own patient card (the blank template, plus a handful of filled dummy
cards), a copy of the official place lists (the gazetteer), and a small team
who will run the review screen. The technical team wires no country-specific
code — a country's card, labels and locations are data files. The pages
above explain each component those files feed.

---

## The checklist

1. **Template.** One clean scan/photo of the blank card →
   `countries/<country>/template.jpg`, checksum-pinned. The capture quality
   gate applies to these photos too.
2. **Form pack.** Declare blocks and value pairs (`form.yaml` + optional
   `labels.yaml` overlay). Concept mapping starts from the shared WHO index;
   concepts the card does not print go in `card_scope.yaml`. (The Studio UI
   for this is the card-definition build — `hanna_next/STUDIO.md`. Until it
   ships, this step is done with the moderator.)
3. **Gazetteer.** COD-AB admin units + Master Facility List for the country →
   `countries/<country>/gazetteer/` via `tools/build_gazetteer.py` (see
   `03-location.md` §2 for the Mozambique sources and their caveats).
4. **Location declaration.** `location.yaml`: how the card's lines map to
   admin levels and facilities, and which fuzzy rules apply.
5. **Ground truth.** Review dummy cards through `tools/gt_capture/` to build
   verified sheets. The first entry ticket (3 or 10 dummy cards) is an open
   decision.
6. **Bench loop.** Upload dummy cards → run → review → per-block accuracy.
   Accuracy targets come from the benchmark plan (`docs/benchmark-plan.md`).

## What is shared, what is national

Shared: the code (`core/`, `server/`), the concept index, the surfaces.
National: the card, its labels, its ground truth, its gazetteer, its aliases
(the `location_aliases` rows a country's reviewers create are that country's
asset — and the reason the same misspelling never costs a click twice).

## Known gaps a new country will hit

- The gazetteer and facility lists are only as current as OCHA/MFL; the build
  scripts record sources and dates — keep them updated per country.
- Card labels are in the country's language; the concept index is WHO English.
  `label_local` carries the printed label; prompts use it.
- The capture gate is calibrated on Mozambique cards (blur/brightness/glare
  work; framing and rotation do not) — recalibrate on the new country's
  photos before judging its readers.
