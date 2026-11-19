"""Validation layer: required fields, allowed parameters and clinical flags.

The contract lives in the country pack (`validation.yaml`). The validator does not
know Mozambique field names; it reads the block/pair ids and the rules.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from . import settings
from .forms import Block, Pair

EMPTY = {"", "empty", "none", "null", "not filled", "not_filled"}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in EMPTY


def _key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _truthy(value: Any) -> bool:
    return _key(value) in {"true", "1", "yes", "sim"}


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def load_contract(country: str) -> dict:
    path = settings.COUNTRIES_DIR / country / "validation.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _bbox(pair: Pair | None, block: Block | None, override=None):
    if override:
        return override
    if pair and pair.bbox:
        return pair.bbox
    if block and block.bbox:
        return block.bbox
    return None


def _issue(
    issues: list,
    kind: str,
    severity: str,
    advice: str | None,
    *,
    pair: Pair | None = None,
    block: Block | None = None,
    audience: list[str] | None = None,
    bbox=None,
) -> None:
    issues.append(
        {
            "pair_id": pair.id if pair else None,
            "block_id": block.id if block else (pair.block_id if pair else None),
            "kind": kind,
            "severity": severity,
            "audience": audience or ["clinician"],
            "advice": advice,
            "bbox": _bbox(pair, block, bbox),
        }
    )


def validate_run(
    blocks: list[Block],
    pairs: list[Pair],
    concepts: dict[str, dict],
    values: dict[str, Any],
    *,
    country: str,
    as_of: date | None = None,
) -> dict:
    contract = load_contract(country)
    as_of = as_of or date.today()
    pair_by_id = {p.id: p for p in pairs}
    block_by_id = {b.id: b for b in blocks}
    value_by_concept: dict[str, Any] = {}
    for pair in pairs:
        value_by_concept.setdefault(pair.concept, values.get(pair.id))
    issues: list[dict] = []

    def value(pair_id: str):
        return values.get(pair_id)

    def cv(concept_id: str):
        return value_by_concept.get(concept_id)

    # 1. Required pairs.
    for pair_id, cfg in (contract.get("required") or {}).items():
        pair = pair_by_id.get(pair_id)
        if not pair:
            continue
        if _is_empty(value(pair_id)):
            _issue(
                issues,
                "missing",
                "blocking",
                cfg.get("advice"),
                pair=pair,
                block=block_by_id.get(pair.block_id),
                bbox=cfg.get("bbox"),
            )

    # 2. Block-level requirements.
    for block_id, cfg in (contract.get("blocks") or {}).items():
        block = block_by_id.get(block_id)
        if not block:
            continue
        severity = cfg.get("severity", "blocking")
        mode = cfg.get("mode")
        if mode == "all_filled":
            missing = [
                p for p in block.pairs
                if not p.derived and _is_empty(value(p.id))
            ]
            if missing:
                if cfg.get("highlight") == "pair":
                    for pair in missing:
                        _issue(
                            issues, "missing", severity, cfg.get("advice"),
                            pair=pair, block=block,
                        )
                else:
                    _issue(
                        issues, "missing", severity, cfg.get("advice"),
                        block=block,
                    )
        elif mode == "section_filled":
            field = cfg.get("field", "is_filled")
            if not _truthy(value(field)):
                _issue(
                    issues, "missing", severity, cfg.get("advice"), block=block
                )

    # 3. Choice values: allowed options and multiple selections.
    # A value the model could not resolve (an empty cell read as the printed
    # legend, "True / False") is a review item, not a reason to hold the
    # notification — unless the field is contractually required.
    required_ids = set((contract.get("required") or {}).keys())
    for pair in pairs:
        if pair.derived or _is_empty(value(pair.id)):
            continue
        concept = concepts.get(pair.concept, {})
        if pair.value_type not in {"choice_single", "tick_box"}:
            continue
        allowed = (
            concept.get("observed")
            or pair.options
            or concept.get("options")
            or []
        )
        allowed_keys = {_key(a) for a in allowed}
        if not allowed_keys:
            continue
        current = _key(value(pair.id))
        if current in allowed_keys:
            continue
        severity = "blocking" if (
            pair.id in required_ids or pair.concept in required_ids
        ) else "info"
        tokens = [
            t for t in re.split(r"\s*(?:,|/|\band\b|\be\b)\s*", current)
            if t
        ]
        matched = [t for t in tokens if t in allowed_keys]
        if len(matched) > 1:
            _issue(
                issues, "multiple_options", severity,
                f"Marque apenas uma opção: {' ou '.join(str(a) for a in allowed)}.",
                pair=pair, block=block_by_id.get(pair.block_id),
            )
        else:
            _issue(
                issues, "invalid_option", severity,
                f"Valor fora das opções permitidas: {' / '.join(str(a) for a in allowed)}.",
                pair=pair, block=block_by_id.get(pair.block_id),
            )

    # 4. Declared rules.
    for pattern, cfg in (contract.get("rules") or {}).items():
        for pair in pairs:
            if not (
                fnmatch.fnmatch(pair.id, pattern)
                or fnmatch.fnmatch(pair.concept, pattern)
            ):
                continue
            if _is_empty(value(pair.id)):
                continue
            current = value(pair.id)
            kind = cfg.get("kind")
            severity = cfg.get("severity", "blocking")
            advice = cfg.get("advice")
            block = block_by_id.get(pair.block_id)

            if kind == "one_of":
                options = {_key(o) for o in cfg.get("options") or []}
                if _key(current) not in options:
                    _issue(issues, "out_of_range", severity, advice, pair=pair, block=block)
            elif kind in {"integer", "integer_range"}:
                number = _int(current)
                if number is None:
                    _issue(issues, "out_of_range", severity, advice, pair=pair, block=block)
                elif cfg.get("min") is not None and number < cfg["min"]:
                    _issue(issues, "out_of_range", severity, advice, pair=pair, block=block)
                elif cfg.get("max") is not None and number > cfg["max"]:
                    _issue(issues, "out_of_range", severity, advice, pair=pair, block=block)
            elif kind == "date":
                parsed = _date(current)
                if parsed is None or (cfg.get("not_future") and parsed > as_of):
                    _issue(issues, "invalid_date", severity, advice, pair=pair, block=block)

    # 5. Cross-field checks.
    birth_year = _int(cv("date_of_birth_or_age"))
    diagnosis = _date(cv("date_diagnosed"))
    treatment_start = _date(cv("treatment_start_date"))
    doses = _int(cv("treatment_doses_total"))
    classification = _key(cv("classification"))

    if _is_empty(value("unique_nr")):
        _issue(
            issues, "system_flag", "info",
            "Sem NIL: pode ser a primeira notificação ou uma notificação anterior. "
            "O DHIS2 terá de reconciliar; na produção a HANNA emitirá um número de doente.",
            audience=["system"],
            block=block_by_id.get("general_demographic"),
        )

    if birth_year and diagnosis and diagnosis.year < birth_year:
        _issue(
            issues, "cross_check", "blocking",
            "A data do diagnóstico não pode ser anterior ao ano de nascimento. Verifique as datas na ficha.",
            pair=pair_by_id.get("date_diagnosed"),
            block=block_by_id.get("classification"),
        )

    if diagnosis and treatment_start and treatment_start < diagnosis:
        _issue(
            issues, "cross_check", "blocking",
            "A data de início do tratamento não pode ser anterior à data do diagnóstico. Corrija a data na ficha.",
            pair=pair_by_id.get("treatment_start_date"),
            block=block_by_id.get("treatment_follow"),
        )

    if diagnosis:
        age_days = (as_of - diagnosis).days
        if age_days > 60 and _is_empty(value("treatment_start_date")):
            _issue(
                issues, "cross_check", "info",
                "Passaram mais de 2 meses desde o diagnóstico e não há tratamento registado. "
                "Confirme se está à espera dos medicamentos.",
                audience=["clinician", "supervisor"],
                block=block_by_id.get("treatment_follow"),
            )
        if doses is not None:
            if classification.startswith("pb") and age_days > 270 and doses < 6:
                _issue(
                    issues, "cross_check", "info",
                    "Passaram mais de 9 meses (PB) e o número de doses é inferior a 6. "
                    "Verifique se o tratamento está incompleto ou não foi marcado.",
                    audience=["clinician", "supervisor"],
                    block=block_by_id.get("treatment_follow"),
                )
            if classification.startswith("mb") and age_days > 540 and doses < 12:
                _issue(
                    issues, "cross_check", "info",
                    "Passaram mais de 18 meses (MB) e o número de doses é inferior a 12. "
                    "Verifique se o tratamento está incompleto ou não foi marcado.",
                    audience=["clinician", "supervisor"],
                    block=block_by_id.get("treatment_follow"),
                )

    # Prednisolone started but not completed: a silent supervisor note, never a
    # block. The weekly dates are not reviewed; the count is enough to flag it.
    pred_status = _key(cv("prednisolone_status"))
    if pred_status == "in_progress":
        weeks = _int(cv("prednisolone_weeks_recorded"))
        count = f"{weeks}/12" if weeks is not None else "menos de 12/12"
        _issue(
            issues,
            "cross_check",
            "info",
            f"Prednisolona iniciada mas só {count} semanas registadas. "
            "Confirmar o seguimento com o supervisor.",
            audience=["supervisor"],
            block=block_by_id.get("prednisolone"),
        )

    # No cardinal signs: zero patches and every enlargement is minus.
    patches = _int(cv("skin_patch_count"))
    enlargements = [
        cv(k)
        for k in (
            "nerve_enlargement_cubital_right", "nerve_enlargement_cubital_left",
            "nerve_enlargement_radial_cutaneous_right", "nerve_enlargement_radial_cutaneous_left",
            "nerve_enlargement_popliteal_external_right", "nerve_enlargement_popliteal_external_left",
        )
    ]
    if (
        patches == 0
        and all(not _is_empty(v) for v in enlargements)
        and all(_key(v) == "-" for v in enlargements)
    ):
        _issue(
            issues, "cross_check", "info",
            "Não há sinais cardinais de lepra marcados. Considere esfregaço cutâneo ou outros exames; "
            "o supervisor distrital deve ser informado.",
            audience=["clinician", "supervisor"],
            block=block_by_id.get("skin_patch"),
        )

    # Grade versus signs, per side: a mismatch is information, not a block.
    grade_checks = [
        (
            "ehf_right_eye",
            lambda: _key(cv("eye_blinking_right")) == "no"
            or (_int(cv("eye_lagophthalmos_right")) or 0) > 0,
            "olho direito",
        ),
        (
            "ehf_left_eye",
            lambda: _key(cv("eye_blinking_left")) == "no"
            or (_int(cv("eye_lagophthalmos_left")) or 0) > 0,
            "olho esquerdo",
        ),
        (
            "ehf_right_hand",
            lambda: _key(cv("hand_ulnar_right")) not in {"", "normal"}
            or (_int(cv("insensitive_points_right_hand")) or 0) > 0,
            "mão direita",
        ),
        (
            "ehf_left_hand",
            lambda: _key(cv("hand_ulnar_left")) not in {"", "normal"}
            or (_int(cv("insensitive_points_left_hand")) or 0) > 0,
            "mão esquerda",
        ),
        (
            "ehf_right_foot",
            lambda: _key(cv("foot_dorsiflex_right")) not in {"", "normal"}
            or (_int(cv("insensitive_points_right_foot")) or 0) > 0,
            "pé direito",
        ),
        (
            "ehf_left_foot",
            lambda: _key(cv("foot_dorsiflex_left")) not in {"", "normal"}
            or (_int(cv("insensitive_points_left_foot")) or 0) > 0,
            "pé esquerdo",
        ),
    ]
    for grade_pair_id, abnormal_check, label in grade_checks:
        grade = _int(value(grade_pair_id))
        if grade is None or grade not in {0, 1, 2}:
            continue
        abnormal = bool(abnormal_check())
        if (grade == 0 and abnormal) or (grade > 0 and not abnormal):
            _issue(
                issues,
                "cross_check",
                "info",
                f"O grau de incapacidade ({label}) e os sinais clínicos não coincidem. "
                "Verifique com o supervisor.",
                pair=pair_by_id.get(grade_pair_id),
                block=block_by_id.get("def_exam"),
                audience=["clinician", "supervisor"],
            )

    blocking = sum(1 for i in issues if i["severity"] == "blocking")
    info = sum(1 for i in issues if i["severity"] == "info")
    status = "blocked" if blocking else ("review" if info else "ok")
    return {
        "status": status,
        "blocking": blocking,
        "info": info,
        "issues": issues,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
    }
