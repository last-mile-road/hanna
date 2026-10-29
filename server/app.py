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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select

from core import jobs, settings, store
from core.db import connect
from core.schema import cards as cards_t
from core.schema import countries as countries_t
from core.schema import forms as forms_t
from core.schema import runs as runs_t

from . import events, signing, uploads

app = FastAPI(title="HANNA", version="0.1-phase1")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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
            {"run_id": str(run_id), "card_id": card_id, "routing": True, "purpose": "upload"},
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
