"""Runtime settings. One place, environment first, no silent defaults for paths."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader. Keeps the repo free of python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+pg8000://sam@127.0.0.1:55432/hanna_dev"
)
IMAGES_DIR = Path(
    os.environ.get(
        "HANNA_IMAGES_DIR",
        "/home/sam/Documents/hanna_project/hanna_archive/versions/v4_hanna3_2025-2026/test_forms",
    )
)
OUTPUT_DIR = Path(os.environ.get("HANNA_OUTPUT_DIR", str(ROOT / "output")))
COUNTRIES_DIR = ROOT / "countries"
# Country data (ground truth, identifying sidecars) is not source. It lives
# outside the repository; the portal will hold it per country at the same shape.
COUNTRY_DATA_DIR = Path(
    os.environ.get("HANNA_COUNTRY_DATA", str(ROOT / "country_data"))
)
CONCEPTS_PATH = ROOT / "docs" / "concepts" / "concepts.yaml"

# Web layer (Phase 1). Uploads land outside the repo; images are served only through
# signed URLs, never a static directory mount.
UPLOAD_DIR = Path(os.environ.get("HANNA_UPLOAD_DIR", str(OUTPUT_DIR / "uploads")))
MAX_UPLOAD_BYTES = int(os.environ.get("HANNA_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))
MIN_UPLOAD_LONG_EDGE = int(os.environ.get("HANNA_MIN_UPLOAD_LONG_EDGE", "1200"))
IMAGE_RETENTION_DAYS = int(os.environ.get("HANNA_IMAGE_RETENTION_DAYS", "30"))
SIGNING_KEY_PATH = Path(
    os.environ.get("HANNA_SIGNING_KEY", str(OUTPUT_DIR / ".signing_key"))
)
SIGNED_URL_TTL_SECONDS = int(os.environ.get("HANNA_SIGNED_URL_TTL", "900"))

OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# The table reader lives on the HANNA box: MINERU_URL is the full endpoint, e.g.
# https://<workspace>--hanna-box-box.<region>.modal.run/parse/mineru
MINERU_URL = os.environ.get("MINERU_URL", "")
MINERU_KEY = os.environ.get("MINERU_KEY", "")

# Tier 2 geocoding. Empty disables it, which is the default: a query carries a place
# name, so it is opt-in per deployment. Local Nominatim: http://127.0.0.1:8080
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "")

# The local OSM import (osm2pgsql of the country extract) — tier 2's workhorse.
# Separate database from the ledger; see tools/build_osm_extract.sh.
OSM_DATABASE_URL = os.environ.get(
    "OSM_DATABASE_URL", "postgresql+pg8000://sam@127.0.0.1:55432/hanna_osm"
)

# The self-hosted box (modal profiles). Primary for the VLM blocks; OpenRouter
# remains the fallback provider.
MODAL_BOX_URL = os.environ.get("HANNA_BOX_URL", "")
MODAL_BOX_KEY = os.environ.get("HANNA_BOX_KEY", "")

# Which routing file a run uses. The box deployment points this at the box
# routing; OpenRouter stays the default so a plain checkout behaves as before.
ROUTING_FILE = os.environ.get("HANNA_ROUTING_FILE", "routing.yaml")

# A local mineru-api (JSON + local source) instead of the box proxy (multipart). Set this
# for local experimentation: MINERU_LOCAL_BASE=http://127.0.0.1:8001
MINERU_LOCAL_BASE = os.environ.get("MINERU_LOCAL_BASE", "")

# Authentication (D-2026-11-17-04). Secure cookies on the VPS; plain HTTP locally.
SESSION_COOKIE = os.environ.get("HANNA_SESSION_COOKIE", "hanna_session")
SESSION_TTL_DAYS = int(os.environ.get("HANNA_SESSION_TTL_DAYS", "90"))
COOKIE_SECURE = os.environ.get("HANNA_COOKIE_SECURE", "0") == "1"
INVITE_TTL_DAYS = int(os.environ.get("HANNA_INVITE_TTL_DAYS", "7"))

# Outgoing mail (ZeptoMail SMTP relay). Leave empty to disable sending.
SMTP_HOST = os.environ.get("ZEPTOMAIL_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("ZEPTOMAIL_SMTP_PORT", "465"))
SMTP_USER = os.environ.get("ZEPTOMAIL_SMTP_USER", "emailapikey")
SMTP_PASSWORD = os.environ.get("ZEPTOMAIL_SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("ZEPTOMAIL_FROM", "support@lastmileroad.org")

# The official portal: a field user signs in with their MoH ID (no password).
FIELD_SESSION_COOKIE = os.environ.get("HANNA_FIELD_COOKIE", "hanna_field")
FIELD_SESSION_TTL_DAYS = int(os.environ.get("HANNA_FIELD_SESSION_TTL_DAYS", "30"))
# On the VPS and any public host this is 1: capture requires an identity.
AUTH_REQUIRED = os.environ.get("HANNA_AUTH_REQUIRED", "0") == "1"

# Desktop accounts are invitation-only. The country QR registers field users,
# not accounts. Set to 1 only for a throwaway local experiment.
ALLOW_OPEN_REGISTRATION = os.environ.get("HANNA_ALLOW_OPEN_REGISTRATION", "0") == "1"
