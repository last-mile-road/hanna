# hanna_server — the build

The running version of HANNA for testing and iteration. Design lives in
`../hanna_next/`; the read-only baseline lives in `../hanna_v2/`.

**Status (2026-11-19).** Working test build. A phone photographs the front and
back of a leprosy clinic card; the pipeline extracts the fields, validates them
against the WHO standard, reviews them value by value against their crops,
decides the case and places the home on a map. PostgreSQL 18 run ledger, form
contract, alignment, generated prompts, model routing, 206 tests pass. There is
no authentication yet: the API is for localhost and a temporary tunnel.

## Start here

| If you want to… | Read |
|---|---|
| add a country | `countries/_template/README.md` — four files, no code |
| know what a country supplies | blank template, 5–10 dummy cards with known values, a location list; ground truth is the entry ticket |
| reuse the code | `LICENSING.md` — Apache-2.0; country packs stay under their own terms |
| contribute | `CONTRIBUTING.md` — data rule, DCO sign-off, pack checklist |

## Decisions taken 2026-10-23

| | |
|---|---|
| Design vs build | `hanna_next/` = design notes, `hanna_server/` = code. One repo each. |
| GitHub | local first, push when the code is stable. Private first, public later. |
| Inference | OpenRouter, same path for local work and for the VPS. |
| VPS | Oracle VPS currently running a Hermes agent. Wiped and rebuilt only when the test server goes public; not now. |
| Local runtime | `~/.venv` (Python 3.14, already has cv2, opencv, requests, openai, pyyaml, pytest). |
| Entry ticket | undecided: 3 or 10 dummy cards per country (`hanna_next/PURPOSE.md` D-C). |

## Layout

```
hanna_server/
├── core/                     the shared pipeline
│   ├── schema.py             the run ledger (12 tables, 2 views)
│   ├── forms.py              form-pack loader and contract checks
│   ├── align.py              SIFT/CLAHE/USAC_MAGSAC alignment and crops
│   ├── prompts.py            one generated prompt per block type
│   ├── inference.py          OpenRouter client
│   ├── normalise.py          card-vocabulary canonicaliser and comparison
│   ├── validate.py           required fields, rules and clinical cross-checks
│   ├── treatment.py          treatment-grid parser and derived metrics
│   └── run.py                one card through one profile
├── migrations/               Alembic baseline
├── countries/
│   ├── _template/            starter pack for a new country
│   └── mozambique/
│       ├── form.draft.yaml   generated from the v5 config: 8 blocks, 62 pairs
│       ├── labels.yaml       printed labels, block hints and box corrections
│       ├── validation.yaml   required fields, Portuguese advice, rules
│       ├── template.jpg      the blank card, MD5 pinned by a test
│       ├── template.md       which scan, and why the other one must not be used
│       ├── location.yaml     hierarchy, card mapping, fuzzy rules and tiers
│       ├── gazetteer/        COD-AB v02 admin units + MFL facilities
│       └── ground_truth/     7 sheets, 61 fields each, evidence ledger
├── docs/
│   ├── concepts/concepts.yaml   the shared vocabulary, 70 concepts
│   ├── baseline-2026-02.md      what v5 actually scored, and where it fails
│   ├── requirements-generic-forms.md
│   └── benchmark-plan.md
├── benchmarks/benchmark_harness.py   loader + scorer; no backend wired yet
├── tools/
│   ├── load_pack.py                  load a country pack into the ledger
│   ├── run_card.py                   run one card through one profile
│   ├── build_gazetteer.py            COD-AB + MFL -> country-pack CSVs
│   ├── location_resolver.py          district -> ADM3 -> facility -> ADM4
│   ├── location_combinations.py      rank chains from all three written names
│   ├── convert_v5_config.py          v5 config -> form.draft.yaml
│   ├── new_ground_truth_form.py      regenerate a sheet from the ledger
│   └── gt_capture/                   ground-truth capture UI (standalone, disposable)
├── data/baseline/field_accuracy.csv  per-field baseline, aggregates only
└── tests/                            206 checks; the database tests need the dev instance on 55432
```

## Run it

```bash
cd ~/Documents/hanna_project/hanna_server
~/.venv/bin/python tools/load_pack.py                 # concepts, form, cards, truth
~/.venv/bin/python tools/run_card.py --form form16    # one card, one profile
# each run writes output/runs/<id>/validation.json and runs.validation_status
~/.venv/bin/python -m pytest tests -q                 # 206 passed
```

The ledger is PostgreSQL. `DATABASE_URL` defaults to the rootless dev instance
on port 55432; the durable system install is still pending. Card images come
from `HANNA_IMAGES_DIR`.

Credentials come from `.env` (gitignored, `OPENROUTER_API_KEY`). `.env.example`
shows the three names. The key was verified against OpenRouter on 2026-10-23:
auth OK, $20 limit, $0 used.

Card images are **not** in this repo. Point the harness at them:

```bash
export HANNA_IMAGES_DIR=/home/sam/Documents/hanna_project/hanna_archive/versions/v4_hanna3_2025-2026/test_forms
```

## What exists now, measured

**Latest 2026-10-28.** Hand/feet with the block legend and 9B: **26/28 (92.9%)** with the required `is_filled` field; 27/28 before that field was added.
Clinical history with the 27B: **41/42 (97.6%)**. Treatment with the 8B:
**21/23 (91.3%)**; the only miss is form22's August 7 read as a missed F. The
validator records `blocked`, `review` or `ok` in `runs.validation_status` and
writes the issue list with Portuguese advice and highlight boxes to
`validation.json`. route v3 is 8B for classification/nerve/skin/treatment, 9B for
demographics/hand-feet, 27B for clinical/disability.

**Older slice, 2026-10-26.** form16, `qwen/qwen3-vl-8b-instruct`, four
parallel calls: **47/53 = 88.7%**, about 17 s, $0.0032. The treatment block is
now a `dose_grid` and scores 4/4 (start date, doses, months, missed). The other
per-block scores: nerve 12/12, def_exam 12/14, classification 3/3, demographics
8/9, clinical history 5/6, skin 1/1, hand/feet 2/4.

form19: 8B 43/49 (87.8%), 9B 42/49 (85.7%). form21, the hardest capture
(230 alignment matches): 8B 32/48 (66.7%), 9B 38/48 (79.2%), 27B low 44/48
(91.7%) but very slow. Across the three cards the 8B is 122/150 (81.3%) and the
9B 121/150 (80.7%).

**Model comparison on the same card and prompt:**

| Profile | Score | Wall | Cost |
|---|---|---|---|
| qwen3-vl-8b, 4 parallel | 44/49 (89.8%) | 16.8 s | $0.0032 |
| qwen3.5-27b, reasoning low | 48/49 (98.0%) | 121 s | $0.0129 |
| qwen3.8-27b, tuned | 47/49 (95.9%) | 202 s | $0.0092 |
| qwen3.8-27b, default settings | 33/52 (63.5%) | 277 s | $0.0147 |

Qwen3.8 at its default output budget returned empty content for the disability
table. With `reasoning_effort: low`, `max_tokens: 8000`, `temperature: 0.3` and
`top_p: 0.95` it scored 14/14 on that block. The 27B at `reasoning_effort: low`
keeps 48/49 and cuts the wall time from 416 s to 121 s. Parallel calls help the
8B and hurt the 27B, which the provider queues.

Field accuracy of v5, from 53 human-reviewed jobs on the VPS: **94.2% over 3,045
judgements**. Read that with the first caveat in `docs/baseline-2026-02.md`: the
53 jobs are only 15 patients, and one patient supplies 37% of the judgements. The
weakest fields are the three blocks v5 never declared as value pairs:
`right_foot_dorsiflex` 58%, `nr_skinpatches` 70%, `insensitive_points_*` 62–74%.

Ground truth for the seven test cards: 337 verified values of 427. All seven are
read (46 to 51 of 61 fields each); the gaps are fields that are blank on the card.
form20 was dropped: it is a duplicate of form16.

## Case model (2026-11-09)

The durable case record the next capture reads back is in place, additive to the
run ledger:

- `core/caseid.py` — nine-digit HANNA number: eight payload digits plus a Luhn
  check digit, generated at first capture, grouped `123 456 789`.
- `core/cases.py` — create/find a case, add a capture and its pages, upsert a
  visit, close or re-open a section, write and read the confirmed-value
  projection, list closed sections.
- `core/schema.py` — `cases`, `captures`, `capture_pages`, `visits`,
  `section_states`, `case_values`; migration `9a1c2e3f4b5d`.
- 12 new tests in the case model; full suite 187 passed.
- A new case is provisional and has **no HANNA number**. `cases.allocate_number()`
  assigns the nine digits only after extraction and validation are clear.

`cases`, `captures` and `visits` follow the DHIS2 tracker shape (case = tracked
entity, visit = event, concept = data element) so a later sync is a mapping, not
a migration. The run ledger and the web layer are unchanged. The capture API,
page admission gate, three-state validation and DHIS2 sync are the next steps;
see `../memory/decisions/2026-11-09-case-foundation.md`.

## Capture ground truth

```bash
~/.venv/bin/python tools/gt_capture/app.py     # http://127.0.0.1:8765
```

Standalone page: card on the left with zoom, fields on the right with the widget
each concept declares (`+`/`-`, dropdown, number, text). Save writes the sheet.
Identifying fields are excluded from the committed file by default. See
`tools/gt_capture/README.md`.

## The web layer (Phase 1)

Three processes, no framework beyond FastAPI on the existing venv:

```bash
~/.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8100   # HTTP + SSE
~/.venv/bin/python -m server.worker                                # claims jobs
curl -s localhost:8100/healthz
```

Bench screen 1 is at `http://127.0.0.1:8100/` — consent, country, camera with the client
gate (sharpness, light, glare), upload, and the live stream. No framework, no web fonts,
about 20 KB of HTML/CSS/JS in total.

Upload and watch one card:

```bash
curl -s -F file=@form16.jpg -F country=mozambique -F attested_dummy=true \
     localhost:8100/api/runs
curl -N localhost:8100/api/runs/<run_id>/events        # block events, then one verdict
```

- `POST /api/runs` refuses any upload without the dummy-card attestation.
- The run id exists before the work starts, so the stream can be opened immediately.
- Progress comes from `block_runs`, so a worker restart does not lose the stream.
- Images are served only through HMAC-signed URLs with a 15-minute expiry.
- `tools/cleanup_images.py --days 30` implements the retention rule; it writes an
  audit line per deletion.

### Two-sided capture, admission and the phone route (2026-11-09)

`/capture` is the phone screen: consent, front, turn the card, back, review,
send. `POST /api/captures` identifies each side by SIFT/USAC alignment and
returns a binary admission result. It creates the provisional case, capture and
pages only when every required side is good. A retake leaves no case and no
image behind; its measurements go to `output/admission.jsonl`.

On admission the extraction starts: one run per page, returned as `runs` with an
SSE URL. The case has **no HANNA number yet**. `POST /api/cases/{id}/close`
records a treatment outcome and date; `POST /api/cases/{id}/allocate` assigns the
nine-digit number only after both page runs are done and clear (no blocked page),
and a closed card also needs its outcome and date. The number is the proof of
notification, not a capture receipt.

`POST /api/runs/{id}/ground-truth` saves the values a human confirmed or
corrected as that card's ground truth — how a phone-captured dummy card becomes
a test card without writing a sheet by hand.

```bash
curl -s -X POST localhost:8100/api/captures \
  -F files=@front.jpg -F files=@back.jpg \
  -F country=mozambique -F attested_dummy=true
```

`status: admitted` returns one entry per page and the `runs` that were started.
`status: retake` returns `missing`, `duplicates` and the reason for each page.
Page templates and form variants come from `countries/mozambique/pages.yaml`;
`GET /api/captures/{id}/page/{n}?sig=...` serves a page through a signed URL.

Default routing is OpenRouter Qwen3.5 9B with the Qwen3-VL 8B fallback. The
printed tables — front `def_exam`/`nerve_assessment` and back
`visitN_def_exam`/`visitN_nerve` — go to the **local** MinerU
(`MINERU_LOCAL_BASE=http://127.0.0.1:8001`); no Modal.com box is used. MinerU
has no VLM fallback: a failed table block fails visibly and is fixed
structurally.

Still missing: the UI (Phase 2), login and country scoping (Phase 3), and the form
builder (Studio).

## Not built yet, deliberately

- The form builder (Studio) and the review screen (Bench).
- Login, invite codes and country scoping; the API is localhost-only.
- The verification UI and the re-photograph workflow; the validator output exists.
- HANNA-issued patient numbers and district-supervisor notifications.
- The location ladder beyond the declaration in `location.yaml`.
- A durable PostgreSQL install; the dev instance on port 55432 is still the ledger.

## Rules

- No real patient data in this repo. Cards stay in the archive; only the blank
  template is tracked.
- No secrets in git. `.env` is ignored; `.env.example` is not.
- One commit per logical unit, conventional messages, a verification step per
  milestone (the existing repo convention).

## Licence and contributing

Code is Apache-2.0; documents and the concept index are CC BY-SA 4.0; a country
pack stays under the terms in its own `NOTICE.md`. See `LICENSING.md`.

Contributions: `CONTRIBUTING.md` (DCO sign-off, country-pack checklist, the data
rule), `SECURITY.md` (private reporting), `CODE_OF_CONDUCT.md`.

