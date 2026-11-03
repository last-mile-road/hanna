"""Studio — where a country team defines its card.

Not a builder's tool: an engagement window. The country uploads their blank
card (we often cannot read the language), draws boxes over the values they
recognise, and links each one to the shared WHO concept index. From that
draft, we build the extraction pipeline together and they send ~10 test cards
to see the first results. The walking-the-road part — the relationship —
happens around this screen.

Files (deliberate, per CONTRACTS.md §6 — data, not code):
    countries/<country>/template.jpg      the blank card, checksum-pinned by load
    countries/<country>/form.studio.yaml  the Studio draft — written here
    countries/<country>/form.draft.yaml   the live pack the bench runs on

The Studio draft never overwrites the live pack. Publishing (freezing a draft
as an immutable version the bench runs) is a later milestone; until then the
moderator moves a draft to form.draft.yaml deliberately.

    from server.studio import router
    app.include_router(router)
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import cv2
import numpy as np
import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core import settings
from core.forms import load_concepts, load_form_pack

router = APIRouter(prefix="/api/studio", tags=["studio"])

STUDIO_DRAFT = "form.studio.yaml"
LIVE_DRAFT = "form.draft.yaml"
TEMPLATE = "template.jpg"


def _country_dir(country: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]{2,32}", country):
        raise HTTPException(404, f"país desconhecido: {country}")
    return settings.COUNTRIES_DIR / country


def _template_size(path: Path) -> tuple[int, int] | None:
    image = cv2.imread(str(path))
    if image is None:
        return None
    return image.shape[1], image.shape[0]  # width, height


def _pack_with_errors(country: str, form_file: str) -> tuple[dict | None, list[str] | None]:
    """The raw YAML plus the validation errors, instead of raising.

    A draft mid-edit is usually invalid — that is information for the screen,
    not an exception. Returns (None, None) when the file does not exist.
    """
    path = _country_dir(country) / form_file
    if not path.exists():
        return None, None
    data = yaml.safe_load(path.read_text()) or {}
    errors = None
    try:
        load_form_pack(country, form_file)
    except ValueError as exc:
        errors = [line.strip().lstrip("- ").strip() for line in str(exc).splitlines()]
        errors = [line for line in errors if line and line != "form pack is invalid:"]
    return data, errors


# --------------------------------------------------------------------- read


@router.get("/countries")
def list_countries():
    """Every country folder, with what onboarding has reached."""
    out = []
    for path in sorted(settings.COUNTRIES_DIR.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue  # _template is the scaffold folder, not a country
        template = path / TEMPLATE
        size = _template_size(template) if template.exists() else None
        out.append({
            "country": path.name,
            "has_template": template.exists(),
            "template_size": size,
            "has_live_draft": (path / LIVE_DRAFT).exists(),
            "has_studio_draft": (path / STUDIO_DRAFT).exists(),
        })
    return out


@router.get("/{country}/pack")
def get_pack(country: str, source: str = "studio"):
    """The draft the screen edits, the concepts it links to, and its errors."""
    form_file = STUDIO_DRAFT if source == "studio" else LIVE_DRAFT
    data, errors = _pack_with_errors(country, form_file)
    if data is None:
        # fall back to the live draft so a country that has only been set up
        # by hand still shows something on first open
        if form_file == STUDIO_DRAFT:
            data, errors = _pack_with_errors(country, LIVE_DRAFT)
            form_file = LIVE_DRAFT
        if data is None:
            raise HTTPException(404, f"nenhum draft para {country}")

    template = _country_dir(country) / str(data.get("form", {}).get("template", TEMPLATE))
    size = _template_size(template) if template.exists() else None
    concepts = [
        {
            "id": c["id"],
            "who_name": c.get("who_name", c["id"]),
            "value_type": c.get("value_type", "text"),
            "sensitivity": c.get("sensitivity", "clinical"),
            "tier": c.get("tier", "extended"),
            "aliases": c.get("aliases") or [],
        }
        for c in load_concepts().values()
    ]
    return {
        "country": country,
        "source_file": form_file,
        "editable": form_file == STUDIO_DRAFT,
        "form": data.get("form", {}),
        "blocks": data.get("blocks", []),
        "concepts": concepts,
        "template_size": size,
        "errors": errors or [],
    }


@router.get("/{country}/template")
def get_template(country: str):
    path = _country_dir(country) / TEMPLATE
    if not path.exists():
        raise HTTPException(404, "sem template — carregue primeiro o cartão em branco")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------------- write


class PairIn(BaseModel):
    """A value pair. Unknown keys (allowed_values, derived, geometry_source…)
    are preserved on save — a Studio round-trip must not silently strip fields
    the pipeline uses."""

    model_config = {"extra": "allow"}

    id: str
    concept: str
    value_type: str | None = None
    bbox: list[int] | None = None
    label_local: str | None = None
    sensitivity: str | None = None


class BlockIn(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    block_type: str
    bbox: list[int] | None = None
    hint: str | None = None
    value_pairs: list[PairIn] = []


class DraftIn(BaseModel):
    form: dict
    blocks: list[BlockIn]


def _dump_pair(pair: PairIn) -> dict:
    known = {
        k: v for k, v in {
            "id": pair.id,
            "concept": pair.concept,
            "value_type": pair.value_type,
            "bbox": pair.bbox,
            "label_local": pair.label_local,
            "sensitivity": pair.sensitivity,
        }.items() if v is not None
    }
    return {**known, **(pair.model_extra or {})}


def _dump_block(block: BlockIn) -> dict:
    known = {
        k: v for k, v in {
            "id": block.id,
            "block_type": block.block_type,
            "bbox": block.bbox,
            "hint": block.hint,
        }.items() if v is not None
    }
    out = {**known, **(block.model_extra or {})}
    out["value_pairs"] = [_dump_pair(p) for p in block.value_pairs]
    return out


@router.put("/{country}/draft")
def save_draft(country: str, draft: DraftIn):
    """Write the Studio draft and validate it against the real rules.

    Returns the errors list — empty means the draft would load. The live
    form.draft.yaml is never touched from here.
    """
    directory = _country_dir(country)
    directory.mkdir(parents=True, exist_ok=True)

    form = dict(draft.form)
    form["country"] = country
    form["template"] = str(form.get("template") or TEMPLATE)
    form["status"] = form.get("status", "draft")
    if form.get("reference_year") is None:
        # CONSTRAINTS §1.6: no silent default for the reference year.
        form["reference_year_note"] = (
            "REQUIRED, no silent default (CONSTRAINTS §1.6). Set the year printed on the card."
        )

    payload = {"form": form, "blocks": [_dump_block(b) for b in draft.blocks]}
    path = directory / STUDIO_DRAFT
    backup = directory / f"{STUDIO_DRAFT}.bak"
    if path.exists():
        shutil.copy(path, backup)  # one click of insurance, not versioning
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))

    _, errors = _pack_with_errors(country, STUDIO_DRAFT)
    return {"ok": True, "errors": errors or [], "saved": str(path)}


@router.post("/{country}/template")
async def upload_template(country: str, file: UploadFile = File(...)):
    """Step 1 of onboarding: the country's blank card becomes the template."""
    directory = _country_dir(country)
    directory.mkdir(parents=True, exist_ok=True)
    raw = await file.read()
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(422, "ficheiro não é uma imagem legível (JPEG ou PNG)")
    height, width = decoded.shape[:2]
    if width < 600 or height < 400:
        raise HTTPException(422, f"imagem demasiado pequena ({width}x{height}) — envie um scan ou foto nítida do cartão inteiro")
    path = directory / TEMPLATE
    if path.exists():
        shutil.copy(path, directory / "template.jpg.bak")
    path.write_bytes(raw)
    return {"ok": True, "size": [width, height], "saved": str(path)}


@router.post("/countries")
def create_country(body: dict):
    """A new country folder with a minimal skeleton, ready for its template."""
    name = str(body.get("name") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not re.fullmatch(r"[a-z0-9_]{2,32}", name):
        raise HTTPException(422, "nome do país: 2–32 caracteres a-z, 0-9, _")
    directory = settings.COUNTRIES_DIR / name
    if directory.exists():
        raise HTTPException(422, f"{name} já existe")
    directory.mkdir(parents=True)
    (directory / STUDIO_DRAFT).write_text(yaml.safe_dump({
        "form": {
            "id": f"{name}_card",
            "country": name,
            "template": TEMPLATE,
            "status": "draft",
            "reference_year": None,
        },
        "blocks": [],
    }, allow_unicode=True, sort_keys=False))
    return {"ok": True, "country": name}
