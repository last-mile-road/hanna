# 06 — Studio: how a country defines its card

**In plain language:** we usually cannot read a country's clinic card — it may
be in a language we do not know, printed in a layout we have never seen. Studio
is the window where the country team shows us their card instead. They upload a
clear photo of the blank card (the tool straightens and cleans it), draw a box
around each value the card asks for — a name line, a date, a tick column, a
table — give each a display name and a one-line description, and pick what it
means from a list of standard WHO definitions. When that is saved, the
moderator groups those boxes into reading blocks, and the country can send
filled test cards to see what HANNA reads and give feedback. Nobody writes
code in this process, and nothing the country does can break an existing
working setup.

---

## The three steps on the screen

1. **The blank card.** Upload a scan/photo of the empty card. On upload it is
   automatically **perspective-corrected** (the card's outer border is found
   and warped to a true rectangle — fixes keystone, where vertical lines lean
   even though horizontal rules are level), **deskewed** (rotation capped at
   ±10° — beyond that the tool refuses and asks for a re-scan) and
   **contrast-normalised** (CLAHE). The untouched original is kept as
   `template_original.jpg`. This matters because a skew of 2° at the top of
   the card is a skew of 2° in every box drawn on top of it. A previous
   upload is kept as `template.jpg.bak`.
2. **Mark what the card asks (country team).** Draw a box around each value
   the card asks for. For each one: a **display name** (as the team calls it),
   a one-line **description** (for whoever reads the definition later), a link
   to a **WHO concept** (searchable), a **required** flag with the hint to
   show when it was not filled, and **sensitivity** (prefilled from the
   concept, editable: identifying / clinical / administrative). Ids
   (`vp_001`…) are generated, so the country team never has to invent
   database names. Blocks are *not* part of this step.
3. **Test cards.** Upload ~10 filled test cards and verify what HANNA reads.
   This runs on the Bench test screen today; the planned truth flow is:
   upload a filled card, overlay it on the template with the pair boxes,
   click a box, fill in the verified value (see "The truth step" below).

## Blocks are the moderator's step (admin mode)

Block grouping and typing is an extraction-strategy decision — how the reading
model is prompted and cropped — not something a country team can be expected
to know. Behind the **Admin mode** toggle, the moderator draws blocks, chooses
their type (`tick_grid`, `annotated_table`, …), and absorbs the pairs whose
centre falls inside. Two rules from the reading experience:

- a block must contain the printed header of its table or column (the nerve
  block went 8/12 → 12/12 when the header joined the crop);
- a box drawn tightly around one tick cell loses its row/column context —
  the moderator grows such boxes to the full grid region.

Pairs not yet absorbed into a block are wrapped on save into provisional
`auto_*` blocks (marked `studio_auto`) so the draft always loads; the admin
pass merges or grows them.

## Where the drawings go

- Studio saves to `countries/<country>/form.studio.yaml` — **never** to the
  live `form.draft.yaml` the Bench runs on. Publishing a draft as the live
  pack is a deliberate moderator step (not built yet; see
  `hanna_next/STUDIO.md` milestones).
- Every save is validated against the real form rules (`core/forms.py`:
  block types, boxes inside blocks, concepts in the index, one pair per
  concept unless `cardinality: many`). Problems are listed on the screen —
  a mid-edit draft is *supposed* to be incomplete, and a pair with no WHO
  concept yet is a listed problem, not a crash.
- A save keeps a `.bak` of the previous draft.
- Fields the editor does not show (allowed values, derived flags, geometry
  sources) are preserved on save — a Studio round-trip strips nothing.

## Tables and groups: draw the region, not the cells

The nerve table, the disability table, the hand/feet diagrams and the dose
grid are handled as **groups**. The country team draws **one box around the
whole table** and names it ("Tabela de nervos"). Nobody draws 40 small cells.
How the working Mozambique pack declares it:

| Group | Block box drawn | Individual items declared |
|---|---|---|
| nerve_assessment | 1 | 12 pairs, **all boxless** |
| def_exam (disability) | 1 | 14 pairs, only 1 boxed |
| hand_feet_assessment | 1 | 12 pairs, all boxless |
| treatment_follow | 1 | 5 pairs; the grid is read as one, totals derived |

A pair without its own box is read from the block's crop (the block type —
`annotated_table`, `tick_grid`, `dose_grid`, `visual_annotation` — selects how
the model is prompted). So the grains are deliberately different:

- **draw** at the grain the model reads (the table region);
- **store** at the grain the report needs (each nerve, each grade);
- **validate** at the grain the programme decides (rules, per country).

For country #2 onward, the item lists of known groups (nerve table, disability
table, hand/feet, dose grid) come from the concept index as **group
templates** — the country confirms the items instead of drawing them. That is
the structural-overlap suggestion layer (`hanna_next/STUDIO.md` M4).

**Blank vs missing.** A blank cell among filled siblings is a *value* ("not
filled / not assessed here"), not a failure — the hand/feet block needed an
explicit `is_filled` signal before it scored 26/28, because "the model did not
see it" and "the clinician left it blank" must not look the same. Blocking is
never automatic: it belongs to the validation rules (`validation.yaml`), per
country. Sensible default: block on what makes the record unusable (identity,
classification), warn on an incomplete group (table anchor filled but three
items unreadable).

## More than one form per country

A country has several cards: the registration card (front), the follow-up
evaluation card (back), later others. Each is its own **form variant** in
Studio — chosen in the header selector — with its own draft
(`form.<v>.studio.yaml`), its own live pack (`form.<v>.yaml`) and its own
template (`template.<v>.jpg`). The original front card keeps the legacy
names (`form.draft.yaml`, `template.jpg`). A follow-up card reuses the same
group templates (nerve table, disability table, hand/feet) across its visit
columns and adds its own sections — on the Mozambique back card, reaction
management: clinical-history and exam questions (sim/não), the prednisolone
dose table (12 weeks), and the outcome checkboxes (WHO §9.2 maps to these).

**Blank vs missing — one rule per group, not one rule for all.** The
closed-world assumption is a property of the group template, declared per
group. Nerve palpation is closed-world: any mark in the table means the table
was worked through, so a cell without a mark is a "−" (negative finding) and
the table counts as complete. The disability table and the voluntary muscle
test are open-world: a blank block there can mean "not examined", and one side
can be graded while the other is not — a blank side is "unknown", never "−".
A value that is present but not in the vocabulary (not N, F or A) is a third
case: unreadable, not blank — it goes to review flagged, and it blocks the
*notification*, not the record: a human resolves it before the case goes out.
Blocking lives in the validation rules per country; the default is to block
on what makes the notification unsafe, warn on what is merely incomplete.

## The truth step (planned)

Once the pairs (and blocks) are confirmed: the country uploads a filled card,
it is overlaid on the template with the pair boxes drawn, and they click each
box to fill in the verified value. That produces the ground truth that
measures extraction quality. The interim tool is `tools/gt_capture/`
(localhost:8765); the overlay-click flow will move into Studio.

## What the moderator still does with the country

- Set the `reference_year` (the year printed on the card; no silent default —
  CONSTRAINTS §1.6).
- Concepts that no country shares yet get `tier: local` in the concept index
  before they can be linked.
- Repeating structures (e.g. a contact list with an "examined" tick per line):
  model as repeated pairs under one concept with `cardinality: many`
  (`contact_01`, `contact_02`…), with the companion tick concept sharing the
  ordinal (`contact_examined_01`…). The list is then a presentation of the
  pairs, and the WHO §8 aggregates (contacts examined, detected, prophylaxis)
  are derived at report level.

## The WHO concept list and its limits

The index held 74 concepts — WHO §4.2 (registration) plus the 62 fields the
v5 pipeline actually produced. When the back card was onboarded (2026-11-03)
the gap became concrete: nothing for the REACÇÕES section, the outcome block
or the voluntary muscle test. The index now carries **98 concepts** after
adding 24 sourced from WHO DHIS2 §6.2/§7.2/§9.2/§10.2: reaction history and
exam items (sim/não), lepra reaction present/type, the prednisolone taper
grid, treatment outcome (alta, abandono, falecido, outro, transferido,
má classificação), self-care group, little-finger abduction, recorded by.
The full WHO DHIS2 list is roughly **110+ elements**; the remaining gap
(contact tracing §8.2 mostly) is a curation pass, not a mechanical dump.
Concepts a card does not print are declared in `card_scope.yaml` so ground
truth stays honest.
