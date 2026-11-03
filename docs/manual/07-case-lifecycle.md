# 07 — The case lifecycle: from notification to the last follow-up

**In plain language:** a patient's story is more than one photo. It starts
when the clinic notifies a case — the front of the card — and continues every
three months on the back, one column per visit. HANNA gives each case a
number that is written on both sides of the card. Every photo HANNA receives
is linked to that number, so a follow-up photo from six months later lands on
the same patient's record instead of creating a duplicate. Each photo is read
against the right form (front or back — the system recognises which), the
filled visit column is extracted, the clinician verifies it, and only then is
it added to the patient's record and shared with the national programme.
Nothing is ever overwritten: the record grows visit by visit, and "what
changed since the last visit" is always answerable.

---

## The story, step by step

1. **Notification (front card).** Read → locate → verify → notify. HANNA
   issues the case number; the clinician writes it on the front (the
   government box, if the programme allocates numbers through HANNA) and on
   the back (a marked spot near the REACÇÕES header — the back has no box of
   its own).
2. **Follow-up (back card).** The clinician fills **one** visit column,
   photographs the back. HANNA: recognises the card as the back form,
   reads the case number from its marked spot, finds the case, reads all
   four columns, sees which column is newly filled, extracts that visit,
   and the clinician verifies it. Empty columns are future visits — never
   flagged, never errors.
3. **Sync.** A visit's values join the patient's record only after
   verification and only when no blocking problem is open. The programme
   receives events per visit, mapped to the country's DHIS2 definitions.

## The rules that keep the data trustworthy

- **One case per patient, one number, written twice.** The number has a
  check digit; a number that fails it or matches nobody goes to a person to
  confirm — HANNA never silently merges two patients or invents a new case.
- **The record is a series of captures.** A re-photo adds a capture; a
  correction adds a corrected value. "What changed" is computed between
  visits, never overwritten.
- **Blank means different things in different places** — a blank cell inside
  a worked-through nerve column is a negative finding; a blank disability
  block is "not examined"; an empty visit column is a future visit. Each
  group declares which rule applies (see `06-studio.md`).
- **Verification is the gate to the programme.** Unverified data never
  syncs. Accuracy is reported per patient, not per photo.

## The technical layer

Design of record: `hanna_next/CASE_LIFECYCLE.md` (this page is its summary);
identity options: `hanna_next/CARD_IDENTITY.md`. Spine:

```
case        (case_id, hanna_number, country, government_id, status)
capture     (capture_id, case_id, form_variant, visit_ordinal, captured_at, image, source)
observation (capture_id, concept_id, value, verified_by, verified_at, ...)
```

- **Form matching:** the photo is aligned against every template variant;
  the alignment score picks the form; below threshold the reviewer picks.
- **The number:** fixed length + check digit; read from an anchored region
  (never a page-wide search); the candidates branch of review decides.
- **Visit columns:** the follow-up template declares the visit dimension
  (column 1–4, month mapping per PB/MB); extraction reports filled columns;
  the capture stores the newest not-yet-recorded ordinal; re-photos dedupe
  by (case, ordinal).
- **Export:** a per-country, versioned map from concepts to DHIS2 data
  elements; a capture syncs as one event per visit. A dummy DHIS2 stub will
  test the hand-off contract before any real MOH server is involved.

**Status of this page:** design. Steps 1–2 of the build order (case tables,
form matching) are the first build; the HANNA number waits on the programme
question — who allocates it, HANNA or the district.
