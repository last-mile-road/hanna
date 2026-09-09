# Requirements — Generic & User-Definable Form Setup (HANNA v2)

**Source:** owner objective 2 — "more generic, more user-definable so that it is not so specific per
country, or that we allow countries to define, to scan in their templates, their template forms,
identify certain areas and information and data groups."

**Baseline issue:** current `hanna_scalable` is hardcoded for the Mozambique card (static config).
This replaces that with a **dynamic template-definition workflow**.

---

## Goals
- A country can onboard a **new clinic-card template themselves** with minimal engineering help.
- Configuration is **generated from the template**, not hand-coded per country.
- Extraction quality remains high and is validated against that country's forms.

## Core workflow (design intent)
1. **Upload / scan the blank template form** (image or PDF).
2. **Define regions & data groups** in a UI: draw/place boxes over areas (e.g., "General
   demographic", "Clinical history", "Skin patch", "Nerve assessment", "Def exam") and mark each
   field's type.
3. **Field-type system** — each field is one of a small set, which drives the extraction method:
   - `text` (handwritten/printed text → OCR-tuned VLM)
   - `date` (birth year, date diagnosed; value normalisation rules)
   - `choice_single` / `choice_multi` (tickboxes with allowed values, e.g. gender M/F, classification)
   - `annotated_table` (nerve rows — grid of cells, +/-)
   - `visual_annotation` (body diagrams — skin patches, hand/feet impairment points → VLM +
     template comparison)
   - `code_number` (unique case number, phone number with normalisation)
4. **Automatic field suggestion** — leverage the ~60-70% structural overlap between country cards:
   pre-label common fields (patient name, birth year, gender, district, etc.) and auto-extend
   bounding boxes to similar positions, which the country confirms.
5. **Validation rules** — mark which fields are **required** (block submission if missing) and the
   **allowed values** for choice fields.
6. **Generate config** → export a template config (JSON/YAML) usable by the extraction + validation
   + geocoding + feedback pipeline. Template configs are stored per country.
7. **Test & iterate** — immediately run a small validation set against the new template and show
   extraction accuracy before going live.

## Non-functional requirements
- **Config-driven + provider-agnostic inference** (preserve `hanna_scalable` lessons).
- **Runs locally on modest hardware** (this machine at first) then VPS.
- **No secrets in repo**; per-country config is data, not code secrets.
- **Human-in-the-loop verification** stays central (clinical trust).
- Works with **low bandwidth**; hybrid/offline geocoding continues.
- Each template config is versioned; template ↔ form pairing each day.

## Out of scope for v2 baseline (later)
- LoRA / active-learning visual training (future theme, not Phase 2-3 of v2).
- Full autonomous CREW agent; v2 focuses on the extraction/onboarding core first.

## Acceptance criteria (v2 milestone checkpoints)
- A non-Mozambique sample card (e.g. Bangladesh/India/Chad) can be onboarded through the UI to a
  generated config and reach a baseline extraction accuracy (target defined in benchmark plan).
- Onboarding requires **no code change** and works with a modest-quality phone photo.
