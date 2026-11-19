# Developing HANNA

Developer notes. The top-level `README.md` is written for a country team; this
file is for someone running or changing the code.

## Layout

```
core/                     the shared pipeline
  schema.py               run ledger, case tables, views
  forms.py                form-pack loader and contract checks
  align.py                SIFT/CLAHE/USAC_MAGSAC alignment and crops
  prompts.py              one generated prompt per block type
  inference.py            OpenRouter client
  normalise.py            card-vocabulary canonicaliser and comparison
  validate.py             required fields, rules, clinical cross-checks
  treatment.py            treatment-grid parser and derived metrics
  run.py                  one card through one profile
migrations/               Alembic baseline
countries/
  _template/              starter pack: form, template, location, ground truth
  mozambique/             one participating country
    form.draft.yaml       front: blocks, value pairs, concepts, sensitivity
    form.back.yaml        back
    labels.yaml           printed labels, block hints, box corrections
    validation.yaml       required fields, rules, Portuguese advice
    template.jpg          the blank card, MD5 pinned by a test
    location.yaml         hierarchy, country name and ISO code, match rules
    gazetteer/            COD-AB units and facility list
    docs/diagnostics/     block overlays on the blank template
    ground_truth/         README only; the sheets are country data
server/                   FastAPI app, worker, capture and review screens
tools/                    pack loader, card runner, gazetteer builder, gt capture
benchmarks/               reader comparisons and the scoring harness
tests/                    206 checks; database tests need the dev instance
```

## Country data is not source

Ground truth, identifying sidecars and the evidence ledger live outside the
repository:

```
country_data/<country>/ground_truth/
```

Point `HANNA_COUNTRY_DATA` at that directory. `tools/load_pack.py` reads it
first and falls back to `countries/<country>/ground_truth`. Tests that need the
sheets skip when it is absent, which is what a public clone sees.

## Run it locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill DATABASE_URL, OPENROUTER_API_KEY, HANNA_IMAGES_DIR
python tools/load_pack.py                 # concepts, form, cards, truth
python tools/run_card.py --form form16    # one card, one profile
python -m pytest tests -q                 # 206 passed
```

- The ledger is PostgreSQL. Start it, then point `DATABASE_URL` at it; the
  development instance in this project listens on `127.0.0.1:55432`.
- Card images are not in the repository; `HANNA_IMAGES_DIR` points at them.
- A run writes `output/runs/<id>/validation.json` and `runs.validation_status`.

## The web layer

```bash
uvicorn server.app:app --host 127.0.0.1 --port 8100   # HTTP + SSE
python -m server.worker                               # claims jobs
curl -s localhost:8100/healthz
```

- `/capture` is the phone route: consent, front, turn the card, back, review,
  send.
- Table blocks go to a local MinerU at `MINERU_LOCAL_BASE`
  (`http://127.0.0.1:8001`). The other blocks go to the model route.
- Images are served only through HMAC-signed URLs with a short expiry.
- `tools/cleanup_images.py --days 30` implements the retention rule and writes an
  audit line per deletion.

Upload and watch one card:

```bash
curl -s -F file=@form16.jpg -F country=mozambique -F attested_dummy=true \
     localhost:8100/api/runs
curl -N localhost:8100/api/runs/<run_id>/events
```

`POST /api/runs` refuses an upload without the dummy-card attestation. The run id
exists before the work starts, so the stream can be opened immediately; progress
comes from `block_runs`, so a worker restart does not lose it.

Two-sided capture:

```bash
curl -s -X POST localhost:8100/api/captures \
  -F files=@front.jpg -F files=@back.jpg \
  -F country=mozambique -F attested_dummy=true
```

`status: admitted` returns one entry per page and the runs that were started;
`status: retake` returns what was missing and why. The case has no HANNA number
until both pages are extracted and clear.

## The case model

`cases`, `captures`, `capture_pages`, `visits`, `section_states` and
`case_values`. `core/caseid.py` holds the nine-digit HANNA number: eight payload
digits plus a Luhn check digit, allocated only after both pages are extracted and
clear. The shape follows DHIS2 tracker — case as tracked entity, visit as event,
concept as data element — so an export is a mapping, not a migration.

## Ground-truth capture

```bash
python tools/gt_capture/app.py     # http://127.0.0.1:8765
```

A standalone page: card on the left, fields on the right with the widget each
concept declares. Saving writes the sheet to the country data directory.
Identifying fields go to a sidecar and are merged back when the benchmark loads
the sheet.
