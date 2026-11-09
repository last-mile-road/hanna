"""The HANNA case number.

Nine digits: eight payload digits and a Luhn check digit. Written by hand on the
card as nine digits, grouped ``123 456 789``. Numbers only: letters in
handwriting vary between readers, and the number is a key, not a mnemonic. Nine
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
    """Strip the spaces and hyphens a reader may have copied with the number."""
    return str(number).replace(" ", "").replace("-", "").strip()


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
