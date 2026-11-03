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
    except (ValueError, KeyError) as exc:
        # a draft mid-edit is normally invalid — and may be structurally
        # incomplete (a pair with no concept yet). Both are screen content.
        errors = [line.strip().lstrip("- ").strip() for line in str(exc).splitlines()]
        errors = [line for line in errors if line and "form pack is invalid" not in line]
    return data, errors


def _studio_file(variant: str) -> str:
    """The editable draft file for a form variant.

    A country has more than one form: the registration card (front), the
    follow-up card (back), later others. Each variant keeps its own draft;
    the default variant keeps its legacy filename (form.studio.yaml).
    """
    return "form.studio.yaml" if variant == "draft" else f"form.{variant}.studio.yaml"


def _live_file(variant: str) -> str:
    return "form.draft.yaml" if variant == "draft" else f"form.{variant}.yaml"


def _template_name(variant: str) -> str:
    return TEMPLATE if variant == "draft" else f"template.{variant}.jpg"


def _list_forms(country: str) -> list[dict]:
    directory = _country_dir(country)
    variants: dict[str, dict] = {}
    for path in sorted(directory.glob("form*.yaml")):
        stem = path.stem
        if stem == "form":
            variant = "draft"
        elif stem.endswith(".studio"):
            variant = stem[len("form."):-len(".studio")] or "draft"
        else:
            variant = stem[len("form."):]
        entry = variants.setdefault(variant, {"variant": variant, "has_live": False,
                                              "has_studio": False})
        if path.name == _studio_file(variant):
            entry["has_studio"] = True
        elif path.name == _live_file(variant):
            entry["has_live"] = True
    out = []
    for variant, entry in variants.items():
        template = directory / _template_name(variant) if variant != "draft" else directory / TEMPLATE
        if variant != "draft" and not template.exists():
            # a variant may name its template explicitly in the form header
            data, _ = _pack_with_errors(country, _studio_file(variant))
            if data and data.get("form", {}).get("template"):
                template = directory / str(data["form"]["template"])
        size = _template_size(template) if template.exists() else None
        entry["has_template"] = template.exists()
        entry["template_size"] = size
        out.append(entry)
    return out


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
            "forms": _list_forms(path.name),
        })
    return out


@router.get("/{country}/pack")
def get_pack(country: str, source: str = "studio", variant: str = "draft"):
    """The draft the screen edits, the concepts it links to, and its errors."""
    studio_file, live_file = _studio_file(variant), _live_file(variant)
    form_file = studio_file if source == "studio" else live_file
    data, errors = _pack_with_errors(country, form_file)
    if data is None:
        # fall back to the live draft so a form that has only been set up
        # by hand still shows something on first open
        if source == "studio":
            data, errors = _pack_with_errors(country, live_file)
            form_file = live_file
        if data is None:
            raise HTTPException(404, f"nenhum draft para {country}/{variant}")

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
        "variant": variant,
        "source_file": form_file,
        "editable": form_file == studio_file,
        "form": data.get("form", {}),
        "blocks": data.get("blocks", []),
        "concepts": concepts,
        "template_size": size,
        "errors": errors or [],
    }


@router.get("/{country}/template")
def get_template(country: str, variant: str = "draft"):
    name = TEMPLATE if variant == "draft" else _template_name(variant)
    path = _country_dir(country) / name
    if not path.exists():
        # fall back to the template named in the draft itself
        data, _ = _pack_with_errors(country, _studio_file(variant)) or (None, None)
        if data and data.get("form", {}).get("template"):
            path = _country_dir(country) / str(data["form"]["template"])
        if not path.exists():
            raise HTTPException(404, "sem template — carregue primeiro o cartão em branco")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------------- write


class PairIn(BaseModel):
    """One value pair as drawn by the country team (or admin).

    Unknown keys (allowed_values, derived, geometry_source…) are preserved on
    save — a Studio round-trip must not silently strip fields the pipeline
    uses. The country team supplies the display name, description and
    requiredness; ids are generated, blocks are assigned later (admin).
    """

    model_config = {"extra": "allow"}

    id: str
    concept: str = ""
    value_type: str | None = None
    bbox: list[int] | None = None
    label_local: str | None = None
    description: str | None = None
    sensitivity: str | None = None
    required: bool | None = None
    required_hint: str | None = None


class BlockIn(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    block_type: str
    bbox: list[int] | None = None
    hint: str | None = None
    value_pairs: list[PairIn] = []


class DraftIn(BaseModel):
    form: dict
    blocks: list[BlockIn] = []
    pairs: list[PairIn] = []      # loose pairs; the server wraps them into provisional blocks


def _dump_pair(pair: PairIn) -> dict:
    known = {
        k: v for k, v in {
            "id": pair.id,
            "concept": pair.concept or None,
            "value_type": pair.value_type,
            "bbox": pair.bbox,
            "label_local": pair.label_local,
            "description": pair.description,
            "sensitivity": pair.sensitivity,
            "required": pair.required,
            "required_hint": pair.required_hint,
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
def save_draft(country: str, draft: DraftIn, variant: str = "draft"):
    """Write the Studio draft and validate it against the real rules.

    Returns the errors list — empty means the draft would load. The live
    form.draft.yaml is never touched from here.
    """
    if not re.fullmatch(r"[a-z0-9_]{1,24}", variant):
        raise HTTPException(422, "variante inválida")
    directory = _country_dir(country)
    directory.mkdir(parents=True, exist_ok=True)

    form = dict(draft.form)
    form["country"] = country
    form["template"] = str(form.get("template") or _template_name(variant))
    form["status"] = form.get("status", "draft")
    if form.get("reference_year") is None:
        # CONSTRAINTS §1.6: no silent default for the reference year.
        form["reference_year_note"] = (
            "REQUIRED, no silent default (CONSTRAINTS §1.6). Set the year printed on the card."
        )

    payload = {"form": form, "blocks": [_dump_block(b) for b in draft.blocks]}

    # The country team draws value pairs, not blocks — block grouping and typing
    # is the extraction-strategy decision (the moderator's). Until that step,
    # every loose pair is wrapped in its own provisional block so the draft
    # loads; `studio_auto` marks them for the admin pass to merge or grow.
    loose = [p for p in draft.pairs if p.bbox is not None]
    for pair in loose:
        payload["blocks"].append({
            "id": f"auto_{pair.id}",
            "block_type": "value_pairs",
            "bbox": pair.bbox,
            "studio_auto": True,
            "value_pairs": [_dump_pair(pair)],
        })
    path = directory / _studio_file(variant)
    backup = directory / f"{path.name}.bak"
    if path.exists():
        shutil.copy(path, backup)  # one click of insurance, not versioning
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))

    _, errors = _pack_with_errors(country, STUDIO_DRAFT)
    return {"ok": True, "errors": errors or [], "saved": str(path)}


def _skew_angle(img) -> float:
    """Skew in degrees, normalised to the long side of the text mass."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(thr)
    if coords is None or len(coords) < 100:
        return 0.0
    (_cx, _cy), (w, h), angle = cv2.minAreaRect(coords)
    if w < h:
        angle += 90
    return float(angle)


def _clean_template(img):
    """Deskew and normalise lighting before the drawing is measured on it.

    A skew of 2° at the top of the card is a skew of 2° in every block box the
    team draws on top of it, so the template must be straight before step 2.
    Returns (cleaned_image, report). The rotation is capped at ±10°: beyond
    that the scan is bad enough that a re-scan is the honest answer, and
    silently straightening a 30° photo produces a warped template.
    """
    report = {"deskew_applied": 0.0, "contrast_applied": False, "skew_refused": None}
    angle = _skew_angle(img)
    if abs(angle) > 10:
        report["skew_refused"] = round(angle, 2)
    elif abs(angle) > 0.3:
        h, w = img.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        img = cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
        report["deskew_applied"] = round(angle, 2)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    report["contrast_applied"] = True
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), report


@router.post("/{country}/template")
async def upload_template(country: str, file: UploadFile = File(...),
                          variant: str = "draft"):
    """Step 1 of onboarding: the country's blank card becomes the template.

    The upload is deskewed and contrast-normalised; the untouched original is
    kept beside it as template_original.jpg (or template.<v>_original.jpg).
    """
    if not re.fullmatch(r"[a-z0-9_]{1,24}", variant):
        raise HTTPException(422, "variante inválida")
    directory = _country_dir(country)
    directory.mkdir(parents=True, exist_ok=True)
    raw = await file.read()
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(422, "ficheiro não é uma imagem legível (JPEG ou PNG)")
    height, width = decoded.shape[:2]
    if width < 600 or height < 400:
        raise HTTPException(422, f"imagem demasiado pequena ({width}x{height}) — envie um scan ou foto nítida do cartão inteiro")
    path = directory / _template_name(variant)
    if path.exists():
        shutil.copy(path, directory / f"{path.stem}_previous.jpg")
    cleaned, report = _clean_template(decoded)
    cv2.imwrite(str(directory / f"{path.stem}_original.jpg"), decoded)
    cv2.imwrite(str(path), cleaned, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return {"ok": True, "size": [width, height], "template": path.name,
            "cleaning": report, "saved": str(path)}


@router.post("/{country}/forms")
def create_form(country: str, body: dict):
    """A new form variant for a country that already exists (front, back, …)."""
    variant = str(body.get("variant") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not re.fullmatch(r"[a-z0-9_]{1,24}", variant) or variant == "draft":
        raise HTTPException(422, "variante: 1–24 caracteres a-z, 0-9, _ (não 'draft')")
    directory = _country_dir(country)
    studio_path = directory / _studio_file(variant)
    if studio_path.exists():
        raise HTTPException(422, f"formulário '{variant}' já existe")
    if not directory.exists():
        raise HTTPException(404, f"país desconhecido: {country}")
    (directory / _studio_file(variant)).write_text(yaml.safe_dump({
        "form": {
            "id": f"{country}_{variant}_card",
            "country": country,
            "template": _template_name(variant),
            "status": "draft",
            "reference_year": None,
        },
        "blocks": [],
    }, allow_unicode=True, sort_keys=False))
    return {"ok": True, "variant": variant}


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
