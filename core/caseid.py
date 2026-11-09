"""The HANNA case number.

Nine digits: eight payload digits and a Luhn check digit. Written by hand on the
card as ``H 123 456 789``: the H is a fixed visual marker that separates the
HANNA number from the NIL, not part of the payload or the check digit. The
payload stays numeric because letters in handwriting vary between readers. Nine
digits hold 10^8 values per country, which is ample, and the check digit rejects
the ordinary single-digit and adjacent-transposition errors before a read can
link two patients.

The number is generated at the first capture, including for retrospective cards:
it is the visual proof that the card has been captured and the key the next
capture reads back. The government NIL stays the national key when present.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.engine import Connection

from .schema import cases


def check_digit(payload: str) -> str:
    """Luhn check digit for an eight-digit payload."""
    if len(payload) != 8 or not payload.isdigit():
        raise ValueError("payload must be eight digits")
    total = 0
    for index, character in enumerate(reversed(payload)):
        digit = int(character)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def normalise(number: str) -> str:
    """Strip the H marker and the separators a reader may have copied."""
    digits = str(number).replace(" ", "").replace("-", "").strip()
    if digits[:1].lower() == "h":
        digits = digits[1:]
    return digits


def is_valid(number: str) -> bool:
    digits = normalise(number)
    if len(digits) != 9 or not digits.isdigit():
        return False
    return check_digit(digits[:8]) == digits[8]


def format_number(number: str) -> str:
    digits = normalise(number)
    if len(digits) != 9:
        return digits
    return f"{digits[:3]} {digits[3:6]} {digits[6:]}"


def display_number(number: str) -> str:
    """How the number is written on the card: H marker, then three groups of three."""
    digits = normalise(number)
    return f"H {format_number(digits)}" if len(digits) == 9 else digits


def generate() -> str:
    payload = "".join(str(secrets.randbelow(10)) for _ in range(8))
    return payload + check_digit(payload)


def generate_unique(conn: Connection, country_id: int, attempts: int = 20) -> str:
    """Generate a number not already used in this country.

    The unique constraint on ``(country_id, hanna_number)`` is the real guard;
    this loop keeps the ordinary race out of the exception path.
    """
    for _ in range(attempts):
        number = generate()
        exists = conn.execute(
            select(cases.c.id).where(
                cases.c.country_id == country_id,
                cases.c.hanna_number == number,
            )
        ).first()
        if exists is None:
            return number
    raise RuntimeError("could not allocate a unique HANNA number")
