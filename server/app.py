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

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select, text, update

from core import admission, caseid, cases as cases_mod, jobs, settings, store
from core.forms import prompt_options
from core.db import connect
from core.schema import capture_pages as capture_pages_t
from core.schema import captures as captures_t
from core.schema import cards as cards_t
from core.schema import countries as countries_t
from core.schema import forms as forms_t
from core.schema import runs as runs_t

from . import events, signing, uploads
from .studio import router as studio_router

app = FastAPI(title="HANNA", version="0.1-phase1")
app.include_router(studio_router)

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


@app.get("/capture", include_in_schema=False)
def capture_page():
    """Phone route: front and back, admission gate, extraction, HANNA number."""
    return FileResponse(STATIC_DIR / "capture.html")


@app.get("/studio", include_in_schema=False)
def studio_page():
    """Studio: the country team defines their card — upload, draw, link to WHO."""
    return FileResponse(STATIC_DIR / "studio.html")

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


def _form(conn, country_id: int | None = None, variant: str = "draft"):
    """The latest published form for a country and variant.

    The variant rides in the form definition (`definition->>'variant'`); rows
    published before variants existed are the default 'draft'.
    """
    sql = "SELECT * FROM forms WHERE coalesce(definition->>'variant','draft') = :variant"
    params: dict = {"variant": variant}
    if country_id is not None:
        sql += " AND country_id = :cid"
        params["cid"] = country_id
    sql += " ORDER BY version DESC LIMIT 1"
    return conn.execute(text(sql), params).mappings().first()


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
    variant: str = Form("draft"),
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
    if not re.fullmatch(r"[a-z0-9_]{1,24}", variant):
        raise HTTPException(422, "variante inválida")

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
        form = _form(conn, country_row["id"], variant)
        if form is None:
            raise HTTPException(
                503, f"nenhum formulário publicado para {country}/{variant}; publique-o no Studio"
            )
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


def _page_json(match: admission.PageMatch, page: dict | None = None) -> dict:
    payload = {
        "side": match.spec.id if match.spec else None,
        "label": match.spec.label if match.spec else "",
        "page_no": match.spec.page_no if match.spec else None,
        "shot_no": match.shot_no,
        "admission": "good" if match.admitted else "retake",
        "inliers": match.inliers,
        "margin": match.margin,
        "rotation": match.rotation,
        "area_ratio": match.area_ratio,
        "reasons": match.reasons,
    }
    if page is not None:
        capture_id = page["capture_id"]
        page_no = page["page_no"]
        signature = signing.query(f"{capture_id}:page:{page_no}")
        payload["page_id"] = page["id"]
        payload["image_url"] = f"/api/captures/{capture_id}/page/{page_no}?{signature}"
    return payload


def _start_capture_runs(
    conn,
    pages: list[dict],
    forms_by_side: dict[str, dict],
    country_id: int,
) -> list[dict]:
    """Create one extraction run and job per admitted page. Idempotent."""
    runs = []
    for page in pages:
        if page.get("run_id"):
            run_id = page["run_id"]
        else:
            form = forms_by_side.get(page["variant"])
            if form is None:
                raise HTTPException(
                    503, f"Nenhum formulário para o lado {page['variant']}."
                )
            card_id = store.upsert_card(
                conn,
                country_id,
                Path(page["image_path"]),
                kind="upload",
                attested_dummy=True,
            )
            run_id = store.create_run(
                conn,
                card_id,
                form["id"],
                form["version"],
                "or-routing-v1",
                "upload",
                status="queued",
            )
            jobs.enqueue(
                conn,
                "extract",
                {
                    "run_id": str(run_id),
                    "card_id": card_id,
                    "routing": True,
                    "purpose": "upload",
                },
            )
            cases_mod.set_page_run(conn, page["id"], run_id)
        runs.append(
            {
                "side": page["variant"],
                "page_no": page["page_no"],
                "run_id": str(run_id),
                "events_url": f"/api/runs/{run_id}/events",
            }
        )
    return runs


@app.post("/api/captures")
def create_capture(
    files: list[UploadFile] = File(...),
    country: str = Form("mozambique"),
    attested_dummy: bool = Form(False),
    hanna_number: str | None = Form(None),
    government_id: str | None = Form(None),
    client: str | None = Form(None),
    require_back: bool = Form(True),
    start_extraction: bool = Form(True),
):
    """Photograph the card sides, identify them, and admit or ask for a retake.

    No model is called here. The gate aligns each photo against the registered
    page templates and returns a binary answer. A rejected attempt creates no
    case and leaves no image behind; its measurements go to `admission.jsonl`.
    """
    if not attested_dummy:
        raise HTTPException(
            422,
            "É necessário confirmar que o cartão é um cartão de teste (não contém dados "
            "de um doente real).",
        )
    if not files:
        raise HTTPException(422, "Envie pelo menos a frente do cartão.")
    if len(files) > 6:
        raise HTTPException(422, "Demasiadas páginas num só cartão.")

    client_data = None
    if client:
        try:
            client_data = json.loads(client)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "Os dados do dispositivo não são JSON válido.") from exc

    number = caseid.normalise(hanna_number) if hanna_number else None
    if number and not caseid.is_valid(number):
        raise HTTPException(422, "O número HANNA não é válido. Verifique os dígitos.")

    try:
        specs = admission.load_pages(country)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc

    with connect() as conn:
        country_row = dict(_country(conn, country))
        existing = (
            cases_mod.find_case(conn, country_id=country_row["id"], hanna_number=number)
            if number
            else None
        )
        if number and existing is None:
            raise HTTPException(
                404,
                "Não há nenhum caso com este número HANNA. Corrija o número ou "
                "fotografe como um caso novo.",
            )
        forms: dict[str, dict | None] = {}
        for spec in specs:
            row = _form(conn, country_row["id"], spec.form_variant)
            forms[spec.form_variant] = dict(row) if row else None

    missing_forms = [spec.id for spec in specs if forms.get(spec.form_variant) is None]
    if missing_forms:
        raise HTTPException(
            503, "Nenhum formulário publicado para: " + ", ".join(missing_forms) + "."
        )

    saved: dict[int, tuple[Path, str]] = {}
    matches: list[admission.PageMatch] = []
    try:
        for shot_no, upload in enumerate(files, start=1):
            data = upload.file.read()
            error = uploads.validate(upload.filename, upload.content_type, len(data))
            if error:
                raise HTTPException(422, error)
            try:
                image = uploads.decode(data)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            quality = uploads.check_quality(image)
            suffix = Path(upload.filename or "").suffix.lower()
            path = uploads.save(data, suffix)
            saved[shot_no] = (path, hashlib.md5(data).hexdigest())
            if quality:
                matches.append(
                    admission.PageMatch(
                        spec=None,
                        shot_no=shot_no,
                        reasons=[quality],
                        quality={"reason": quality},
                    )
                )
                continue
            matches.append(admission.match_image(path, specs, shot_no=shot_no))
    except Exception:
        for path, _ in saved.values():
            path.unlink(missing_ok=True)
        raise

    assessment = admission.assess(matches, specs, require_back=require_back)
    try:
        admission.log_attempt(country, assessment)
    except OSError:
        pass
    if not assessment.admitted:
        for path, _ in saved.values():
            path.unlink(missing_ok=True)
        return {
            "status": "retake",
            "message": assessment.message,
            "missing": assessment.pages_missing,
            "duplicates": assessment.duplicates,
            "pages": [_page_json(match) for match in assessment.pages],
        }

    runs_out: list[dict] = []
    try:
        with connect() as conn:
            if existing is not None:
                case = existing
            else:
                case = cases_mod.create_case(
                    conn,
                    country_row["id"],
                    government_id=government_id or None,
                    identity_status="new",
                )
            capture = cases_mod.add_capture(
                conn,
                case["id"],
                source="camera",
                attested_dummy=True,
                client=client_data,
                status="admitted",
            )
            page_rows = []
            for match in assessment.pages:
                if match.spec is None:
                    continue
                path, checksum = saved[match.shot_no]
                form = forms[match.spec.form_variant]
                page_rows.append(
                    cases_mod.add_page(
                        conn,
                        capture["id"],
                        page_no=match.spec.page_no,
                        shot_no=match.shot_no,
                        image_path=path,
                        md5=checksum,
                        variant=match.spec.id,
                        form_id=form["id"],
                        admission="good",
                        quality={
                            "inliers": match.inliers,
                            "margin": match.margin,
                            "rotation": match.rotation,
                            "area_ratio": match.area_ratio,
                            "reasons": match.reasons,
                        },
                        match_inliers=match.inliers,
                        match_margin=match.margin,
                        rotation=match.rotation,
                    )
                )
            if start_extraction:
                forms_by_side = {spec.id: forms[spec.form_variant] for spec in specs}
                runs_out = _start_capture_runs(
                    conn, page_rows, forms_by_side, country_row["id"]
                )
            conn.commit()
    except Exception:
        for path, _ in saved.values():
            path.unlink(missing_ok=True)
        raise

    number_value = case.get("hanna_number") or None
    admitted_matches = [match for match in assessment.pages if match.spec is not None]
    runs_by_page = {run["page_no"]: run for run in runs_out}
    pages_json = []
    for match, page in zip(admitted_matches, page_rows):
        item = _page_json(match, page)
        run = runs_by_page.get(page["page_no"])
        if run:
            item["run_id"] = run["run_id"]
            item["events_url"] = run["events_url"]
        pages_json.append(item)
    return {
        "status": "admitted",
        "case_id": str(case["id"]),
        "capture_id": str(capture["id"]),
        "hanna_number": number_value,
        "hanna_number_display": caseid.format_number(number_value) if number_value else None,
        "number_allocated": bool(number_value),
        "identity_status": case["identity_status"],
        "matched_by": "hanna_number" if number else "new",
        "message": assessment.message,
        "missing": assessment.pages_missing,
        "duplicates": assessment.duplicates,
        "pages": pages_json,
        "runs": runs_out,
    }


@app.get("/api/captures/{capture_id}/page/{page_no}")
def capture_page_image(capture_id: str, page_no: int, sig: str = Query(...)):
    """A page image behind a short-lived signed URL, never a static directory."""
    capture_id = _uuid(capture_id)
    if not signing.verify(f"{capture_id}:page:{page_no}", sig):
        raise HTTPException(403, "ligação expirada; recarregue a página")
    with connect() as conn:
        page = conn.execute(
            select(capture_pages_t).where(
                capture_pages_t.c.capture_id == uuid.UUID(capture_id),
                capture_pages_t.c.page_no == page_no,
            )
        ).mappings().first()
    if page is None or not Path(page["image_path"]).exists():
        raise HTTPException(404, "imagem não encontrada")
    media_type = "image/png" if page["image_path"].lower().endswith(".png") else "image/jpeg"
    return FileResponse(page["image_path"], media_type=media_type)


@app.post("/api/captures/{capture_id}/extract")
def extract_capture(capture_id: str):
    """Start one extraction run per admitted page. Idempotent."""
    capture_id = _uuid(capture_id)
    with connect() as conn:
        capture = conn.execute(
            select(captures_t).where(captures_t.c.id == uuid.UUID(capture_id))
        ).mappings().first()
        if capture is None:
            raise HTTPException(404, "captura não encontrada")
        case = cases_mod.find_case(conn, case_id=capture["case_id"])
        if case is None:
            raise HTTPException(404, "caso não encontrado")
        country = conn.execute(
            select(countries_t).where(countries_t.c.id == case["country_id"])
        ).mappings().one()
        specs = admission.load_pages(country["code"])
        forms_by_side = {}
        for spec in specs:
            row = _form(conn, case["country_id"], spec.form_variant)
            if row:
                forms_by_side[spec.id] = dict(row)
        missing = [spec.id for spec in specs if spec.id not in forms_by_side]
        if missing:
            raise HTTPException(
                503, "Nenhum formulário publicado para: " + ", ".join(missing) + "."
            )
        pages = [
            dict(row)
            for row in conn.execute(
                select(capture_pages_t)
                .where(capture_pages_t.c.capture_id == uuid.UUID(capture_id))
                .order_by(capture_pages_t.c.page_no)
            ).mappings()
        ]
        runs = _start_capture_runs(conn, pages, forms_by_side, case["country_id"])
        conn.commit()
    return {"capture_id": capture_id, "runs": runs}


@app.post("/api/cases/{case_id}/allocate")
def allocate_case_number(case_id: str):
    """Allocate the HANNA number only after both pages are read and clear.

    The number is proof that notification was completed, not a capture receipt.
    A blocked page, a page still reading, or a closed card without an outcome
    leaves the case without a number.
    """
    case_id = _uuid(case_id)
    with connect() as conn:
        case = cases_mod.find_case(conn, case_id=uuid.UUID(case_id))
        if case is None:
            raise HTTPException(404, "caso não encontrado")
        if case["hanna_number"]:
            return {
                "hanna_number": case["hanna_number"],
                "hanna_number_display": caseid.format_number(case["hanna_number"]),
                "allocated": False,
            }

        capture = conn.execute(
            select(captures_t)
            .where(captures_t.c.case_id == uuid.UUID(case_id))
            .order_by(captures_t.c.captured_at.desc())
            .limit(1)
        ).mappings().first()
        if capture is None:
            raise HTTPException(409, "O caso ainda não tem uma captura.")
        pages = conn.execute(
            select(capture_pages_t)
            .where(capture_pages_t.c.capture_id == capture["id"])
            .order_by(capture_pages_t.c.page_no)
        ).mappings().all()
        if not pages:
            raise HTTPException(409, "A captura não tem páginas.")

        problems = []
        for page in pages:
            label = page["variant"] or f"página {page['page_no']}"
            if page["run_id"] is None:
                problems.append(f"{label}: leitura não iniciada")
                continue
            run = conn.execute(
                select(runs_t).where(runs_t.c.id == page["run_id"])
            ).mappings().first()
            if run is None:
                problems.append(f"{label}: leitura não encontrada")
            elif run["status"] not in ("done", "partial"):
                problems.append(f"{label}: ainda a ler")
            elif run["validation_status"] == "blocked":
                problems.append(f"{label}: bloqueado")
            elif run["validation_status"] not in ("ok", "review"):
                problems.append(f"{label}: leitura incompleta")
        if problems:
            raise HTTPException(
                409, "Ainda há condições bloqueantes: " + "; ".join(problems) + "."
            )
        if case["status"] == "closed" and (
            not case["outcome"] or not case["outcome_date"]
        ):
            raise HTTPException(
                409, "O resultado do tratamento está em falta para fechar o caso."
            )

        allocated = cases_mod.allocate_number(conn, uuid.UUID(case_id))
        conn.commit()
    return {
        "hanna_number": allocated["hanna_number"],
        "hanna_number_display": caseid.format_number(allocated["hanna_number"]),
        "allocated": True,
    }


@app.post("/api/runs/{run_id}/ground-truth")
def save_run_ground_truth(run_id: str):
    """Save the reviewed values as this card's ground truth.

    Only values a human confirmed or corrected are exported; skipped and
    untouched values are not truth. This is how a phone-captured dummy card
    becomes a test card without a separate sheet-writing step.
    """
    run_id = _uuid(run_id)
    with connect() as conn:
        run = conn.execute(
            select(runs_t).where(runs_t.c.id == uuid.UUID(run_id))
        ).mappings().first()
        if run is None:
            raise HTTPException(404, "análise não encontrada")
        rows = conn.execute(
            text(
                """
                SELECT pair_id, concept_id,
                       COALESCE(reviewed_value, value) AS value
                  FROM observations
                 WHERE run_id = CAST(:run_id AS uuid)
                   AND review_mode IN ('confirmed', 'corrected')
                   AND COALESCE(reviewed_value, value) IS NOT NULL
                 ORDER BY pair_id
                """
            ),
            {"run_id": run_id},
        ).mappings().all()
        if not rows:
            raise HTTPException(
                409,
                "Não há valores revistos para guardar. Reveja os campos e tente de novo.",
            )
        truth_rows = [
            {
                "gt_key": row["pair_id"],
                "concept_id": row["concept_id"],
                "pair_id": row["pair_id"],
                "value": row["value"],
                "status": "verified",
                "scoreable": True,
                "source": "phone review",
                "verified_by": "phone review",
                "verified_at": datetime.now(timezone.utc),
            }
            for row in rows
        ]
        store.upsert_ground_truth(conn, run["card_id"], truth_rows)
        conn.commit()
    return {"run_id": run_id, "card_id": run["card_id"], "saved": len(truth_rows)}


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


@app.get("/api/runs/{run_id}/map.png")
def run_map(run_id: str):
    """The review map: a static PNG so the coordinate never reaches the phone's tile client."""
    from core import mapimage

    run_id = _uuid(run_id)
    with connect() as conn:
        location = conn.execute(
            select(runs_t.c.location).where(runs_t.c.id == run_id)
        ).scalar()
    if not location or location.get("lat") is None or location.get("lon") is None:
        raise HTTPException(404, "sem coordenadas para este cartão")

    import hashlib

    reviewed = (location.get("review") or {}).get("mode") == "chosen"
    lat, lon = float(location["lat"]), float(location["lon"])
    # The point moves when a reviewer corrects it, so the file name has to follow it;
    # a single map.png would keep serving the old picture from cache.
    digest = hashlib.sha256(f"{lat:.5f},{lon:.5f},{reviewed}".encode()).hexdigest()[:10]
    out = settings.OUTPUT_DIR / "runs" / run_id / f"map-{digest}.png"
    path = mapimage.compose(
        lat=lat,
        lon=lon,
        radius_m=float(location.get("precision_m") or 1500),
        out=out,
        cache=settings.OUTPUT_DIR / "tiles",
        reviewed=reviewed,
    )
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- review


class ReviewItem(BaseModel):
    pair_id: str
    mode: str  # accepted | corrected | skipped
    value: str | None = None


class ReviewBody(BaseModel):
    reviews: list[ReviewItem]


class LocationReview(BaseModel):
    mode: str                       # accepted | chosen | unknown
    lat: float | None = None
    lon: float | None = None
    name: str | None = None
    pcode: str | None = None


@app.post("/api/runs/{run_id}/location")
def post_location(run_id: str, body: LocationReview):
    """Record what the reviewer did with the location, inside the run's location record.

    A correction is stored with the original decision intact, so the pair
    (gazetteer answer, human answer) survives as evidence for the alias table later.
    """
    run_id = _uuid(run_id)
    with connect() as conn:
        location = conn.execute(
            select(runs_t.c.location).where(runs_t.c.id == run_id)
        ).scalar()
        if not location:
            raise HTTPException(404, "sem localização para este cartão")
        from core.location import _km, reverse_admin

        location = dict(location)
        previous = location.get("review") or {}
        base_lat = previous.get("lat", location.get("lat"))
        base_lon = previous.get("lon", location.get("lon"))
        # Confirming is also a placement: the clinician is saying the pin as shown is the
        # house, so the point is stored as reviewer-sourced either way. `changed` and
        # `moved_m` are what let us measure later how far reviewers move pins.
        lat = body.lat if body.lat is not None else base_lat
        lon = body.lon if body.lon is not None else base_lon
        moved = (_km(float(base_lat), float(base_lon), float(lat), float(lon))
                 if base_lat is not None and base_lon is not None and lat is not None else 0.0)
        # the human pin is the truth; the official ADM chain (COD-AB names and
        # p-codes) is derived from it for the programme hand-off
        adm = (reverse_admin(float(lat), float(lon))
               if lat is not None and lon is not None else {})
        location["review"] = {
            "mode": body.mode,
            "lat": lat,
            "lon": lon,
            "name": body.name,
            "pcode": body.pcode,
            "source": "revisor",
            "changed": body.mode == "chosen",
            "moved_m": round(moved, 1),
            "adm": adm,
        }
        conn.execute(update(runs_t).where(runs_t.c.id == run_id).values(location=location))
        conn.commit()

        # A confirmation or a candidate pick is a teaching moment: the spellings on the
        # card now have a human-attested canonical unit. A free pin dragged on the map
        # ("ajustado no mapa") corrects the house, not the name, so it writes no alias.
        try:
            written = {
                row[0]: row[1]
                for row in conn.execute(
                    text("SELECT concept_id, value FROM observations "
                         "WHERE run_id = CAST(:run_id AS uuid) AND value IS NOT NULL "
                         "AND concept_id IN ('district', 'locality', 'village')"),
                    {"run_id": run_id},
                )
            }
            written = {"district": written.get("district") or "",
                       "locality": written.get("locality") or "",
                       "village": written.get("village") or ""}
            if body.mode == "accepted":
                chosen = location.get("chosen") or {}
            elif body.pcode or (body.name and body.name != "ajustado no mapa"):
                # The reviewer picked a candidate: prefer the full candidate from the
                # resolution (chain, level, coordinates) over the bare name the card sent.
                chosen = next(
                    (c for c in location.get("candidates") or []
                     if c.get("name") == body.name
                     and (not body.pcode or c.get("pcode") == body.pcode)),
                    {},
                ) or {"name": body.name, "pcode": body.pcode}
            else:
                chosen = {}
            if chosen and any(written.values()):
                from core.aliases import record as record_aliases

                written_rows = record_aliases(
                    conn, form_country(conn, run_id), written, chosen, run_id
                )
                conn.commit()
                if written_rows:
                    location["review"]["aliases_recorded"] = written_rows
                    conn.execute(
                        update(runs_t).where(runs_t.c.id == run_id).values(location=location)
                    )
                    conn.commit()
        except Exception:
            conn.rollback()  # a failed alias write must not fail the review
    return {"ok": True, "review": location["review"]}


def form_country(conn, run_id: str) -> str:
    """The run's country, via its form definition."""
    row = conn.execute(
        text("SELECT form.definition->>'country' FROM forms form "
             "JOIN runs run ON run.form_id = form.id WHERE run.id = CAST(:run_id AS uuid)"),
        {"run_id": run_id},
    ).scalar()
    return row or "mozambique"


_TEMPLATE_SIZE: dict[str, tuple[int, int]] = {}
_UI_LAYOUT: dict[str, dict] = {}


def _ui_layout(form_definition: dict) -> dict:
    """How the review screen lays this form out.

    Per form variant first (ui.<variant>.yaml — the back card has no map and
    different sections), then the country default (ui.yaml).
    """
    import yaml

    country = form_definition.get("country", "mozambique")
    variant = form_definition.get("variant") or "draft"
    key = f"{country}:{variant}"
    if key not in _UI_LAYOUT:
        directory = settings.COUNTRIES_DIR / country
        variant_path = directory / f"ui.{variant}.yaml"
        default_path = directory / "ui.yaml"
        path = variant_path if variant_path.exists() else default_path
        _UI_LAYOUT[key] = yaml.safe_load(path.read_text()) if path.exists() else {}
    return _UI_LAYOUT[key]


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
        "location": run.get("location"),
        "show_location": bool(layout.get("show_location", True)),
        "block_titles": layout.get("block_titles") or {},
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
