# 06 — Studio: how a country defines its card

**In plain language:** we usually cannot read a country's clinic card — it may
be in a language we do not know, printed in a layout we have never seen. Studio
is the window where the country team shows us their card instead. They upload a
clear photo of the blank card, draw boxes around the regions of the card, then
draw smaller boxes around each value the card asks for (name, birth year,
district…) and pick what it means from a list of standard WHO definitions.
When that is saved, we can build the reading pipeline for their card and they
can send test cards to see what HANNA reads and give feedback. Nobody writes
code in this process, and nothing the country does can break an existing
working setup.

---

## The three steps on the screen

1. **The blank card.** Upload a scan/photo of the empty card. It becomes the
   country's `template.jpg` — the map every drawing is measured on. A previous
   upload is kept as `template.jpg.bak`.
2. **Draw what the card asks.** Draw a **block** around a region (demographics,
   treatment grid, skin patch…). Select it, then draw a **value pair** box for
   each value inside it. Each pair is linked to a **WHO concept** from the
   shared index (68 concepts, searchable by name or synonym); the printed label
   in the card's language is stored with the pair. A drawn pair is clamped to
   stay inside its block — the block box is the crop the reading model will
   see, so it must contain both the printed label and the value.
3. **Test cards.** Upload ~10 filled test cards and verify what HANNA reads.
   This runs on the Bench test screen today and moves into Studio as the
   country's pipeline is set up.

## Where the drawings go

- Studio saves to `countries/<country>/form.studio.yaml` — **never** to the
  live `form.draft.yaml` the Bench runs on. Publishing a draft as the live
  pack is a deliberate moderator step (not built yet; see
  `hanna_next/STUDIO.md` milestones).
- Every save is validated against the real form rules (`core/forms.py`:
  block types, boxes inside blocks, concepts in the index, one pair per
  concept unless `cardinality: many`). Problems are listed on the screen —
  a mid-edit draft is *supposed* to be incomplete.
- A save keeps a `.bak` of the previous draft.
- Fields the editor does not show (allowed values, derived flags, geometry
  sources) are preserved on save — a Studio round-trip strips nothing.

## What the moderator still does with the country

- Set the `reference_year` (the year printed on the card; no silent default —
  CONSTRAINTS §1.6).
- Decide block types and prompts hints for the trickier blocks (visual blocks
  need their printed legend in the crop — see `02-extraction.md`).
- Concepts that no country shares yet get `tier: local` in the concept index
  before they can be linked.
