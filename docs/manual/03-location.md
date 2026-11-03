# 03 — Location: the gazetteer, the cascade, the map and the alias table

**In plain language:** a patient card writes where the person lives in three
lines: district, locality, and village or health facility. Those lines are
often misspelled, and many small places are not in any national list. HANNA
turns those three lines into a point on a map in three steps. First it looks
the names up in its own copy of the official lists (the **gazetteer**). If the
names do not match well enough, it asks an online map service as a backup. If
that also fails, a person picks the place on a map during review. Every time a
person confirms or corrects a place, HANNA remembers the spelling it saw — so
the next card with the same spelling resolves by itself, with no second click.
The system never silently guesses: when it is unsure, it says so and asks a
person.

---

## 1. The three names on the card and what they mean

Mozambique's administration runs province → district → administrative post →
locality → village, and the health system adds facilities at points. The card
prints **Distrito**, **Localidade** and **Unidade Sanitária/Aldeia**. They map
to the national lists like this:

| Card line | What it usually is | What it sometimes is |
|---|---|---|
| Distrito | district (ADM2) | — |
| Localidade | locality (ADM3) | a facility, or a place in no list |
| Unidade Sanitária/Aldeia | health facility | a village (ADM4) |

The province is not printed; it is inferred from the district, and all 161
district names are unique today, so that is safe. Full details of the sources
and their caveats: `hanna_next/LOCATION.md`.

## 2. The gazetteer (tier 1)

The gazetteer is two CSV files per country under
`countries/<country>/gazetteer/`:

- `admin_units.csv` — COD-AB v02 (OCHA, 2025): 11 provinces, 161 districts,
  457 administrative posts, 1,712 localities, each with a point and p-code.
- `facilities.csv` — the Master Facility List (MFL, SIS-MA via HDX): 1,590
  facilities, 1,589 with coordinates.

Both are rebuilt from the source spreadsheets with `tools/build_gazetteer.py`.
The MFL is dated and incomplete (national reporting says ~1,770 facilities) —
a current extract from SIS-MA/MISAU is an open ask, not a solved problem.

## 3. How a card is resolved (the cascade)

`core/location.py` implements the cascade; `countries/<country>/location.yaml`
declares the rules. Per card:

1. **Normalise** the three written names (strip accents, lowercase, strip
   printed prefixes like "Posto Administrativo de").
2. **Check the alias table first** (section 5). A spelling a reviewer has
   already resolved is replaced by its canonical name — exactly, with no
   guessing.
3. **Anchor** on the district: a strong district match (score ≥ 85) narrows
   the search to that district's chains.
4. **Score whole chains**, not names one at a time, against all three written
   names (a province name written in the locality field constrains the
   province instead of failing the locality).
5. **Decide:**
   - `auto` — best chain ≥ 85 and ≥ 10 clear of the runner-up. The point is
     written; the review still shows it on a map for one confirmation.
   - `review` — ≥ 70, or a tie, or a thin margin. Top candidates are shown;
     the reviewer picks.
   - `escalate` — below 70. Try the service (tier 2), then the map picker.

The decision, its score, margin, chosen chain and alternatives are stored in
`runs.location` (JSONB) with the run. An escalate can be lifted to review by
tier 2, but never to auto: a service proves *a place with this name exists
nearby*, not *this is the place on the card*.

## 4. Tier 2 — the online service, kept on a short leash

On an escalate, `core/location.service_search` asks a Nominatim-compatible
service (self-hosted is the production default; the public one is demo-only at
1 request/second). The leash: the query is bounded to an 80 km box around the
district centroid, throttled to 1 req/s, cached to disk, and a service hit is
marked `source="nominatim"` and sent to the map picker, never auto-accepted.
A slow or down service changes nothing about the card's state.

Villages are also the tier-3 answer: several card places (`Nanili`, `Eiope`,
`Muinamba`) are in no public data at all. The map picker is the correct
fallback for them, not a failure.

## 5. The alias table — a reviewer's click teaches the resolver

**In plain language:** when a reviewer confirms a place or picks one from the
candidates, HANNA writes down the spelling from the card next to the place it
turned out to be. The next card that writes the same spelling skips the
guessing. The table is per country and grows with use.

Technically:

- Table `location_aliases` (migration `f3d9a1c4b7e8`), one row per
  (country, normalised spelling, card field). Fields: the raw spelling as
  read, the canonical unit (name, level, p-code, district, province, point),
  the run that taught it (`run_id`), a use counter, timestamps.
- **Written by review only.** `POST /api/runs/{id}/location` records rows when
  the reviewer confirms the gazetteer's chain (`accepted`) or picks a
  candidate. A free pin dragged on the map corrects the house position, not
  the name, and writes no alias. Per field: the district spelling maps to the
  chain's district; the locality and village spellings map to the chosen unit.
- **Read before every resolution.** `core/aliases.lookup` runs before the
  gazetteer in the run flow (`core/run.py`); hits are substituted for the
  written names and the resolution is marked `aliases_applied`. A lookup
  failure degrades to normal resolution — it can never fail a card.
- **Conflicts:** a re-attestation updates the row and increments the counter.
  The evidence of what happened stays append-only in `runs.location`.
- To see what a country's reviewers have taught so far:

```sql
SELECT written_raw, field, canonical, level, times_used
FROM location_aliases WHERE country = 'mozambique' ORDER BY times_used DESC;
```

## 6. What is measured, and the open decision

The review map draws a ring at the claimed precision (facility 250 m,
locality 1.5 km, admin post 4 km, district 12 km) and stores how far the
reviewer moved the pin (`review.moved_m`). The question this answers: **is
the gazetteer good enough, or does tier 2 earn its place?** If reviewers
confirm or move the pin less than the ring, tier 1 carries the load; if they
repeatedly move pins far outside the ring, tier 2 matters.

`tools/moved_m_report.py` reports the distribution (n, mean, median, max,
share inside the ring, confirms vs moves). **Current state: n=1** (form16,
facility Impiri, moved 34.6 m — 14% of the 250 m ring). One number is not a
distribution; the decision waits for a review round across reviewers and
cards. Run the report after each round.

## 7. Failure modes worth knowing

- `pandas` reads empty cells as NaN, and NaN is not JSON — a run that read
  every block and then failed to store. `_candidate` normalises NaN/None/
  `"nan"` to null. (Found 2026-11-02.)
- Without a district anchor, fuzzy matching on short strings invents
  plausible rubbish ("Nowhere" → "Noere" at 83). An unanchored chain must
  clear the review floor as a whole or the cascade escalates.
- Sibling admin posts can tie at identical scores; candidates are ordered by
  score, then facility-before-locality, then number of names matched.
- Location matching is only for places. Person names, phone numbers, IDs and
  gender are never fuzzy-corrected (`hanna_next/LOCATION.md` §3).
