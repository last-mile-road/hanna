"""HTTP surface, Phase 1. No UI, no auth yet (Phase 3), localhost only.

    uvicorn server.app:app --port 8100
    python -m server.worker            # in another shell

Routes
    POST /api/runs                  upload one card, queue it, return the run id
    GET  /api/runs/{run_id}         state, blocks, values, validation
    GET  /api/runs/{run_id}/events  SSE: block events, then one verdict
    GET  /api/runs/{run_id}/image/{kind}?sig=...   original or aligned card

The run id is created before the work starts, so the client can open the stream
immediately. Uploads are refused unless the caller attests the card is a dummy
(`DECISIONS.md` D-2026-10-28-15).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select, text

from core import jobs, settings, store
from core.forms import prompt_options
from core.db import connect
from core.schema import cards as cards_t
from core.schema import countries as countries_t
from core.schema import forms as forms_t
from core.schema import runs as runs_t

from . import events, signing, uploads

app = FastAPI(title="HANNA", version="0.1-phase1")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/review", include_in_schema=False)
def review_page():
    """Review screen: one row per declared pair, accept or correct, submit once."""
    return FileResponse(STATIC_DIR / "review.html")


@app.get("/", include_in_schema=False)
def index():
    """Bench screen 1: consent, capture, client gate, upload, live stream."""
    return FileResponse(STATIC_DIR / "index.html")

TERMINAL = {"done", "partial", "failed"}


def _uuid(run_id: str) -> str:
    """Reject a malformed id before it reaches Postgres (22P02 would be a 500)."""
    import uuid

    try:
        return str(uuid.UUID(run_id))
    except ValueError as exc:
        raise HTTPException(404, "run não encontrado") from exc


def _country(conn, code: str):
    row = conn.execute(select(countries_t).where(countries_t.c.code == code)).mappings().first()
    if row is None:
        raise HTTPException(404, f"país desconhecido: {code}")
    return row


def _form(conn):
    return conn.execute(
        select(forms_t).order_by(forms_t.c.version.desc()).limit(1)
    ).mappings().first()


def _run_card_path(conn, run_id: str) -> tuple[Path, str]:
    import uuid

    try:
        key = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(404, "run não encontrado") from exc
    row = conn.execute(select(runs_t).where(runs_t.c.id == key)).mappings().first()
    if row is None:
        raise HTTPException(404, "run não encontrado")
    from core.schema import cards as cards_t

    card = conn.execute(select(cards_t).where(cards_t.c.id == row["card_id"])).mappings().first()
    return Path(card["image_path"]), str(row["id"])


@app.get("/healthz")
def healthz():
    try:
        with connect() as conn:
            conn.execute(select(1))
        return {"ok": True}
    except Exception as exc:  # surfaced, not swallowed
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.post("/api/runs", status_code=202)
async def create_run(
    file: UploadFile = File(...),
    country: str = Form("mozambique"),
    attested_dummy: bool = Form(False),
    gate_blur: float | None = Form(None),
    gate_brightness: float | None = Form(None),
    gate_glare: float | None = Form(None),
):
    if not attested_dummy:
        raise HTTPException(
            422,
            "É necessário confirmar que o cartão é um cartão de teste (não contém dados "
            "de um doente real).",
        )

    data = await file.read()
    error = uploads.validate(file.filename, file.content_type, len(data))
    if error:
        raise HTTPException(422, error)
    try:
        image = uploads.decode(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    quality = uploads.check_quality(image)
    if quality:
        raise HTTPException(422, quality)

    suffix = Path(file.filename or ".jpg").suffix.lower()
    path = uploads.save(data, suffix)

    with connect() as conn:
        country_row = _country(conn, country)
        form = _form(conn)
        if form is None:
            raise HTTPException(503, "nenhum formulário carregado; corra tools/load_pack.py")
        # The same bytes may already be a known card (a dummy card, or an earlier
        # upload). Never repoint or restyle an existing row: upsert_card updates on an
        # md5 conflict, which silently turned three ground-truth cards into uploads.
        checksum = store.md5(path)
        existing = conn.execute(
            select(cards_t).where(cards_t.c.md5 == checksum)
        ).mappings().first()
        if existing is not None:
            card_id = existing["id"]
            path.unlink(missing_ok=True)
        else:
            card_id = store.upsert_card(
                conn, country_row["id"], path, kind="upload", attested_dummy=True
            )
        run_id = store.create_run(
            conn, card_id, form["id"], form["version"], "or-routing-v1", "upload",
            status="queued",
        )
        job_id = jobs.enqueue(
            conn,
            "extract",
            {
                "run_id": str(run_id),
                "card_id": card_id,
                "routing": True,
                "purpose": "upload",
                "client_gate": {"blur": gate_blur, "brightness": gate_brightness, "glare": gate_glare},
            },
        )
        conn.commit()

    return {
        "run_id": str(run_id),
        "job_id": str(job_id),
        "status": "queued",
        "events_url": f"/api/runs/{run_id}/events",
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run_id = _uuid(run_id)
    with connect() as conn:
        state = events.snapshot(conn, run_id)
    if state is None:
        raise HTTPException(404, "run não encontrado")
    state["card_url"] = f"/api/runs/{run_id}/image/card?{signing.query(f'{run_id}:card')}"
    if state["blocks"]:
        state["aligned_url"] = (
            f"/api/runs/{run_id}/image/aligned?{signing.query(f'{run_id}:aligned')}"
        )
    return state


def _sse(kind: str, payload: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


def _stream(run_id: str, timeout_seconds: int = 900):
    # Open the stream immediately and forbid intermediaries from buffering it.
    yield ": open\n\n"
    seen: set[str] = set()
    previous_status: str | None = None
    heartbeat = time.monotonic()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with connect() as conn:
            state = events.snapshot(conn, run_id)
        if state is None:
            yield _sse("error", {"message": "run não encontrado"})
            return

        for block in events.new_blocks(state["blocks"], seen):
            yield _sse("block", block)

        if state["status"] != previous_status:
            yield _sse("status", {"status": state["status"], "blocks_done": state["blocks_done"]})
            previous_status = state["status"]

        if state["status"] in TERMINAL:
            yield _sse(
                "verdict",
                {
                    "run_id": run_id,
                    "status": state["status"],
                    "validation_status": state["validation_status"],
                    "validation": state["validation"],
                    "blocks_done": state["blocks_done"],
                    "latency_ms": state["latency_ms"],
                    "cost_usd": state["cost_usd"],
                    "error": state["error"],
                },
            )
            return

        if time.monotonic() - heartbeat > 10:
            yield ": ping\n\n"
            heartbeat = time.monotonic()
        time.sleep(0.4)


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    run_id = _uuid(run_id)
    with connect() as conn:
        if events.run_row(conn, run_id) is None:
            raise HTTPException(404, "run não encontrado")
    return StreamingResponse(
        _stream(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity",
        },
    )


@app.get("/api/runs/{run_id}/image/{kind}")
def run_image(run_id: str, kind: str, sig: str = Query(...)):
    resource = f"{run_id}:{kind}"
    if not signing.verify(resource, sig):
        raise HTTPException(403, "ligação expirada; recarregue a página")

    with connect() as conn:
        card_path, _ = _run_card_path(conn, run_id)

    if kind == "card":
        path = card_path
    elif kind == "aligned":
        path = settings.OUTPUT_DIR / "runs" / run_id / "aligned.jpg"
    else:
        raise HTTPException(404, "imagem desconhecida")
    if not path.exists():
        raise HTTPException(404, "imagem ainda não disponível")
    return FileResponse(path, media_type="image/jpeg")


# --------------------------------------------------------------------------- review


class ReviewItem(BaseModel):
    pair_id: str
    mode: str  # accepted | corrected | skipped
    value: str | None = None


class ReviewBody(BaseModel):
    reviews: list[ReviewItem]


_TEMPLATE_SIZE: dict[str, tuple[int, int]] = {}
_UI_LAYOUT: dict[str, dict] = {}


def _ui_layout(form_definition: dict) -> dict:
    """countries/<country>/ui.yaml — how the review screen lays a block out."""
    import yaml

    country = form_definition.get("country", "mozambique")
    if country not in _UI_LAYOUT:
        path = settings.COUNTRIES_DIR / country / "ui.yaml"
        _UI_LAYOUT[country] = yaml.safe_load(path.read_text()) if path.exists() else {}
    return _UI_LAYOUT[country]


def _template_size(path: str) -> tuple[int, int]:
    import cv2

    if path not in _TEMPLATE_SIZE:
        image = cv2.imread(path)
        if image is None:
            raise HTTPException(500, "template não encontrado")
        _TEMPLATE_SIZE[path] = (image.shape[1], image.shape[0])
    return _TEMPLATE_SIZE[path]


@app.get("/api/runs/{run_id}/review")
def get_review(run_id: str):
    """Everything a reviewer needs: each declared pair, what was read, and the issue."""
    run_id = _uuid(run_id)
    with connect() as conn:
        run = events.run_row(conn, run_id)
        if run is None:
            raise HTTPException(404, "run não encontrado")
        blocks, pairs, concepts = store.form_rows(conn, run["form_id"])
        form = conn.execute(select(forms_t).where(forms_t.c.id == run["form_id"])).mappings().one()
        observations = {
            row["pair_id"]: dict(row)
            for row in conn.execute(
                text(
                    "SELECT pair_id, value, raw_value, expected_value, correct, "
                    "review_mode, reviewed_value FROM observations "
                    "WHERE run_id = CAST(:run_id AS uuid)"
                ),
                {"run_id": run_id},
            ).mappings()
        }
        card = conn.execute(
            select(cards_t).where(cards_t.c.id == run["card_id"])
        ).mappings().one()

    layout = _ui_layout((form["definition"] or {}))
    hidden = set(layout.get("hidden") or [])
    dev_only = set(layout.get("dev_only") or [])
    labels = layout.get("labels") or {}
    option_labels = layout.get("option_labels") or {}
    tables_by_block: dict[str, list] = {}
    for table in layout.get("tables") or []:
        tables_by_block.setdefault(table.get("block"), []).append(table)

    issues = (run["validation"] or {}).get("issues", [])
    by_pair = {i.get("pair_id"): i for i in issues if i.get("pair_id")}
    by_block = {i.get("block_id"): i for i in issues if i.get("block_id") and not i.get("pair_id")}

    width, height = _template_size(form["template_path"])
    sections = []
    counts = {"accepted": 0, "corrected": 0, "skipped": 0, "reviewed": 0, "total": 0}
    for block in blocks:
        if not block.pairs:
            continue
        rows = []
        skipped_hidden = 0
        for pair in block.pairs:
            if pair.id in hidden:
                skipped_hidden += 1
                continue
            concept = concepts.get(pair.concept, {})
            obs = observations.get(pair.id, {})
            option_list = prompt_options(pair, concept) or []
            row_issue = by_pair.get(pair.id)
            rows.append(
                {
                    "pair_id": pair.id,
                    "concept": pair.concept,
                    "label": labels.get(pair.id) or pair.label_local or concept.get("who_name") or pair.id,
                    "value_type": pair.value_type,
                    "value": obs.get("value"),
                    "raw_value": obs.get("raw_value"),
                    "expected_value": obs.get("expected_value"),
                    "correct": obs.get("correct"),
                    "review_mode": obs.get("review_mode"),
                    "reviewed_value": obs.get("reviewed_value"),
                    "options": [str(o) for o in option_list],
                    "option_labels": {
                        str(o): str(option_labels.get(str(o), o)) for o in option_list
                    },
                    "issue": row_issue,
                    "dev_only": pair.id in dev_only,
                }
            )
            counts["total"] += 1
            mode = obs.get("review_mode")
            if mode in counts:
                counts[mode] += 1
                counts["reviewed"] += 1
        sections.append(
            {
                "id": block.id,
                "type": block.block_type,
                "bbox": block.bbox,
                "issue": by_block.get(block.id),
                "pairs": rows,
                "tables": tables_by_block.get(block.id, []),
                "hidden_count": skipped_hidden,
            }
        )

    return {
        "run_id": run_id,
        "status": run["status"],
        "validation_status": run["validation_status"],
        "issues": issues,
        "counts": counts,
        "image": {
            "url": f"/api/runs/{run_id}/image/aligned?{signing.query(f'{run_id}:aligned')}",
            "card_url": f"/api/runs/{run_id}/image/card?{signing.query(f'{run_id}:card')}",
            "width": width,
            "height": height,
        },
        "blocks": sections,
    }


@app.post("/api/runs/{run_id}/review")
def post_review(run_id: str, body: ReviewBody):
    """Record what a human did with each value. Append-only in spirit: the extracted value
    stays in `value`, the reviewer's decision goes beside it."""
    run_id = _uuid(run_id)
    allowed = {"accepted", "corrected", "skipped"}
    updated = 0
    missing: list[str] = []
    changed: list[str] = []
    with connect() as conn:
        if events.run_row(conn, run_id) is None:
            raise HTTPException(404, "run não encontrado")
        for item in body.reviews:
            if item.mode not in allowed:
                raise HTTPException(422, f"modo inválido: {item.mode}")
            if item.mode == "corrected" and not (item.value or "").strip():
                raise HTTPException(422, "uma correcção precisa de um valor")
            result = conn.execute(
                text(
                    "UPDATE observations SET review_mode = :mode, reviewed_value = :value "
                    "WHERE run_id = CAST(:run_id AS uuid) AND pair_id = :pair_id"
                ),
                {"mode": item.mode, "value": item.value, "run_id": run_id, "pair_id": item.pair_id},
            )
            if result.rowcount:
                updated += 1
                if item.mode == "corrected":
                    changed.append(item.pair_id)
            else:
                missing.append(item.pair_id)
        conn.commit()

    return {"updated": updated, "corrected": changed, "not_in_ledger": missing}
