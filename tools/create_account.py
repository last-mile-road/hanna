"""Create an account by hand — the bootstrap before the admin pages exist.

    python tools/create_account.py --email a@b.c --name "A B" --role project_admin
    python tools/create_account.py --email c@d.e --name "C D" --role country_admin --country mozambique

The password is asked for twice and never echoed. Roles: project_admin,
country_admin, contributor, reviewer, viewer. A country role needs --country.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from core import auth
from core.db import connect
from core.schema import countries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", default="project_admin")
    ap.add_argument("--country", help="country code, e.g. mozambique")
    args = ap.parse_args()

    password = getpass.getpass("password: ")
    if password != getpass.getpass("again: "):
        raise SystemExit("passwords do not match")

    with connect() as conn:
        country_id = None
        if args.country:
            country_id = conn.execute(
                select(countries.c.id).where(countries.c.code == args.country.lower())
            ).scalar()
            if country_id is None:
                raise SystemExit(f"unknown country code: {args.country}")
        try:
            row = auth.create_account(
                conn,
                email=args.email,
                password=password,
                display_name=args.name,
                country_id=country_id,
                role=args.role,
            )
            auth.audit(
                conn,
                "account_created",
                account_id=row["id"],
                country_id=country_id,
                detail={"role": args.role, "source": "cli"},
            )
            conn.commit()
        except Exception as exc:  # unique email, role/country mismatch, short password
            conn.rollback()
            raise SystemExit(str(exc))
    print(f"created {row['email']} ({args.role}) id={row['id']}")


if __name__ == "__main__":
    main()
