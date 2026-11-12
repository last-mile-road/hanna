"""Case-level decisions after both pages have been extracted.

The block pipeline answers "what is written here". This module answers the
case questions that need the whole card: is this a known or new case, is it
still in treatment, what should review show, and what blocks the HANNA number.
Country values live in countries/<country>/case_rules.yaml.
"""

from __future__ import annotations

import calendar
import fnmatch
import re
import uuid
from datetime import date, datetime
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import settings
from .caseid import format_number, is_valid

TERMINAL = {"done", "partial", "failed"}
CLOSED_CODES = {"released", "ltfu", "died", "misclassification", "other", "transferred"}


def load_rules(country: str = "mozambique") -> dict[str, Any]:
    path = settings.COUNTRIES_DIR / country / "case_rules.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _add_months(value: date, months: int) -> date:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _as_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _page_rows(conn: Connection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT p.page_no, p.variant, p.run_id, r.status, r.validation_status, "
            "       r.validation, r.started_at "
            "  FROM capture_pages p "
            "  JOIN captures c ON c.id = p.capture_id "
            "  LEFT JOIN runs r ON r.id = p.run_id "
            " WHERE c.case_id = :case_id "
            " ORDER BY p.page_no, r.started_at DESC"
        ),
        {"case_id": case_id},
    ).mappings().all()
    # A retake leaves old pages behind. Keep the latest run for each side.
    pages: dict[str, dict[str, Any]] = {}
    for row in rows:
        page = dict(row)
        side = page.get("variant") or f"page{page['page_no']}"
        pages.setdefault(side, page)
    return sorted(pages.values(), key=lambda p: p["page_no"])


def _readings(conn: Connection, case_id: uuid.UUID) -> dict[str, str]:
    rows = conn.execute(
        text(
            "SELECT o.concept_id, COALESCE(o.reviewed_value, o.value) AS value, "
            "       r.started_at "
            "  FROM observations o "
            "  JOIN runs r ON r.id = o.run_id "
            "  JOIN capture_pages p ON p.run_id = r.id "
            "  JOIN captures c ON c.id = p.capture_id "
            " WHERE c.case_id = :case_id "
            " ORDER BY r.started_at DESC"
        ),
        {"case_id": case_id},
    ).mappings().all()
    readings: dict[str, str] = {}
    for row in rows:
        value = row["value"]
        if row["concept_id"] in readings or value is None or str(value).strip() == "":
            continue
        readings[row["concept_id"]] = str(value).strip()
    return readings


def _blocking_issues(page: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    for issue in (page.get("validation") or {}).get("issues", []):
        if issue.get("severity") not in {"blocking", "error"}:
            continue
        issues.append(
            {
                "page": page.get("variant") or f"page{page['page_no']}",
                "kind": issue.get("kind"),
                "pair_id": issue.get("pair_id"),
                "block_id": issue.get("block_id"),
                "message": issue.get("advice") or issue.get("kind"),
            }
        )
    return issues


def decide_case(
    conn: Connection,
    case_id: uuid.UUID,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return the case decision. Read-only; the caller persists a number or close."""
    as_of = as_of or date.today()
    case = conn.execute(
        text(
            "SELECT id, country_id, government_id, hanna_number, status, classification, "
            "       treatment_start_date, outcome, outcome_date "
            "  FROM cases WHERE id = :case_id"
        ),
        {"case_id": case_id},
    ).mappings().first()
    if case is None:
        raise ValueError("case not found")
    # country code is not the id; the country row carries it.
    country = conn.execute(
        text("SELECT code FROM countries WHERE id = :id"), {"id": case["country_id"]}
    ).scalar()
    rules = load_rules(country or "mozambique")

    pages = _page_rows(conn, case_id)
    readings = _readings(conn, case_id)
    page_issues = [issue for page in pages for issue in _blocking_issues(page)]
    terminal = bool(pages) and all(
        page.get("status") in TERMINAL for page in pages
    )
    review_ready = terminal

    treatment = rules.get("treatment") or {}
    durations = {str(k).upper(): int(v) for k, v in (treatment.get("durations_months") or {}).items()}
    default_outcome = str(treatment.get("default_outcome") or "Em tratamento")
    closed_outcomes = {str(v) for v in (treatment.get("closed_outcomes") or [])} | CLOSED_CODES

    classification = str(
        readings.get("classification") or case.get("classification") or ""
    ).upper()
    duration = None
    if classification.startswith("PB"):
        duration = durations.get("PB")
    elif classification.startswith("MB"):
        duration = durations.get("MB")

    start = _as_date(readings.get("treatment_start_date") or case.get("treatment_start_date"))
    outcome = readings.get("outcome_decision") or case.get("outcome")
    if outcome == "in_treatment":
        outcome = default_outcome
    outcome_date = _as_date(readings.get("outcome_date") or case.get("outcome_date"))

    recorded_months = None
    try:
        recorded_months = (
            int(float(readings.get("treatment_months_total")))
            if readings.get("treatment_months_total") is not None
            else None
        )
    except (TypeError, ValueError):
        recorded_months = None

    elapsed = False
    elapsed_at = None
    if duration:
        if start:
            elapsed_at = _add_months(start, duration)
            elapsed = as_of >= elapsed_at
        elif recorded_months is not None:
            elapsed = recorded_months >= duration

    state = "unknown"
    if not review_ready:
        state = "processing"
    elif outcome in closed_outcomes:
        state = "closed"
    elif outcome in {default_outcome, "in_treatment"}:
        state = "in_treatment"
    elif elapsed and not outcome:
        state = "outcome_overdue"
    elif outcome:
        # An unexpected outcome is still a closed value; review it.
        state = "closed"
    else:
        state = "in_treatment" if (start or classification or recorded_months is not None) else "unknown"

    effective_outcome = outcome
    if state == "in_treatment" and not effective_outcome:
        effective_outcome = default_outcome

    # The number is read from the card margin. The write space sits just above
    # the NIL row, so the block model sometimes reads it as the NIL instead;
    # a NIL that is a valid nine-digit HANNA number is taken as the number.
    number_read = readings.get("hanna_number")
    number_source = "margin" if number_read else None
    nil_read = readings.get("patient_id")
    nil_digits = re.sub(r"\D", "", str(nil_read or ""))
    if not number_read and len(nil_digits) == 9 and is_valid(nil_digits):
        number_read = nil_digits
        number_source = "nil_field"
    same_as_nil = bool(
        number_read and len(nil_digits) == 9 and nil_digits == number_read
    )

    identity_status = "new"
    if case.get("hanna_number") or number_read:
        identity_status = "existing_hanna"
    elif case.get("government_id") or readings.get("patient_id"):
        identity_status = "known_nil"

    # A number read from the card may already belong to another case: the same
    # patient photographed again after the first number was written. Name it,
    # so the capture can be joined to that case instead of held as a new one.
    match_case = None
    if number_read:
        row = conn.execute(
            text(
                "SELECT id, hanna_number FROM cases "
                " WHERE country_id = :country_id AND hanna_number = :number AND id <> :case_id"
            ),
            {"country_id": case["country_id"], "number": number_read, "case_id": case_id},
        ).mappings().first()
        if row:
            match_case = str(row["id"])

    blocking: list[dict[str, Any]] = []
    if review_ready:
        ignore = (rules.get("blocking") or {}).get("closed_case_ignore_blocks") or []
        old_or_closed = state == "closed" or identity_status == "existing_hanna"
        for issue in page_issues:
            if old_or_closed and any(
                fnmatch.fnmatch(issue.get("block_id") or "", pattern)
                for pattern in ignore
            ):
                continue
            blocking.append(issue)
        if state == "outcome_overdue":
            ended = f" em {elapsed_at.isoformat()}" if elapsed_at else ""
            blocking.append(
                {
                    "page": "back",
                    "kind": "outcome_overdue",
                    "pair_id": "outcome_decision",
                    "block_id": "outcome_choice",
                    "message": (
                        "Resultado do tratamento em falta: o regime "
                        f"{'PB' if classification.startswith('PB') else 'MB'} terminou"
                        f"{ended}. Registe o resultado no verso."
                    ),
                }
            )
        if state == "closed" and treatment.get("require_outcome_date", True) and not outcome_date:
            blocking.append(
                {
                    "page": "back",
                    "kind": "outcome_date_missing",
                    "pair_id": "outcome_date",
                    "block_id": "outcome_date",
                    "message": "O resultado está marcado mas a data do resultado está em falta no verso.",
                }
            )

    can_allocate = review_ready and not blocking
    feedback: list[dict[str, Any]] = []
    if not review_ready:
        feedback.append(
            {
                "kind": "processing",
                "severity": "info",
                "message": "Ainda a ler as duas páginas. A revisão abre quando ambas terminarem.",
            }
        )
    else:
        for item in blocking:
            feedback.append(
                {
                    "kind": item.get("kind") or "blocking",
                    "severity": "blocking",
                    "message": item.get("message") or "Condição bloqueante.",
                }
            )
        if number_source == "nil_field":
            feedback.append(
                {
                    "kind": "hanna_number_in_nil",
                    "severity": "info",
                    "message": (
                        "O número HANNA foi lido no campo NIL, porque está escrito "
                        "logo acima dele. Confirme o NIL na revisão."
                    ),
                }
            )
        if number_read and not is_valid(number_read):
            feedback.append(
                {
                    "kind": "hanna_number_invalid",
                    "severity": "info",
                    "message": (
                        f"O número escrito no cartão ({format_number(number_read)}) não "
                        "passa o dígito de controlo. Confirme os dígitos."
                    ),
                }
            )
        if match_case:
            feedback.append(
                {
                    "kind": "hanna_number_exists",
                    "severity": "info",
                    "message": (
                        f"Este número já pertence a outro caso "
                        f"({format_number(number_read)}). A captura deve juntar-se a esse "
                        "caso, não criar um novo."
                    ),
                }
            )
        if not blocking and state == "closed":
            feedback.append(
                {
                    "kind": "closed",
                    "severity": "info",
                    "message": "Caso fechado: resultado e data lidos do verso.",
                }
            )
        elif not blocking and state == "in_treatment":
            feedback.append(
                {
                    "kind": "in_treatment",
                    "severity": "info",
                    "message": "Caso em tratamento. Pode obter o número HANNA e continuar o seguimento.",
                }
            )

    return {
        "case_id": str(case_id),
        "review_ready": review_ready,
        "can_allocate": can_allocate,
        "state": state,
        "status": "blocked" if blocking else ("closed" if state == "closed" else state),
        "identity": {
            "status": identity_status,
            "government_id": (
                None
                if same_as_nil
                else (readings.get("patient_id") or case.get("government_id"))
            ),
            "hanna_number": number_read or case.get("hanna_number"),
            "number_read": number_read,
            "number_source": number_source,
            "number_valid": (
                is_valid(number_read) if number_read else None
            ),
            "match_case_id": match_case,
        },
        "classification": classification or None,
        "duration_months": duration,
        "treatment_start": start.isoformat() if start else None,
        "recorded_months": recorded_months,
        "outcome": effective_outcome,
        "outcome_date": outcome_date.isoformat() if outcome_date else None,
        "outcome_default": effective_outcome == default_outcome,
        "elapsed_at": elapsed_at.isoformat() if elapsed_at else None,
        "blocking": blocking,
        "feedback": feedback,
        "pages": [
            {
                "page_no": page["page_no"],
                "variant": page.get("variant"),
                "run_id": str(page["run_id"]) if page.get("run_id") else None,
                "status": page.get("status"),
                "validation_status": page.get("validation_status"),
            }
            for page in pages
        ],
        "as_of": as_of.isoformat(),
    }
