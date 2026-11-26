#!/usr/bin/env python3
"""Encrypted daily backup of the HANNA database and uploads to Dropbox.

Same Dropbox app and folder root as the transcription service, a separate
subfolder (`/lrm-backups/hanna`). The archive is encrypted with Fernet before it
leaves the server; the key never leaves `/etc/hanna/hanna.env`.

Run by `hanna-backup.timer` at 03:30. Restore steps are in `deploy/BACKUP.md`.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
RETENTION_DAYS = 14
DROPBOX_ROOT = os.environ.get("DROPBOX_HANNA_ROOT", "/lrm-backups/hanna")
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"
LIST_URL = "https://api.dropboxapi.com/2/files/list_folder"
DELETE_URL = "https://api.dropboxapi.com/2/files/delete_v2"


def fail(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    raise SystemExit(1)


def dropbox_token() -> str:
    refresh = os.environ.get("DROPBOX_REFRESH_TOKEN")
    key = os.environ.get("DROPBOX_APP_KEY")
    secret = os.environ.get("DROPBOX_APP_SECRET")
    if not (refresh and key and secret):
        fail("DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY and DROPBOX_APP_SECRET are required")
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": key,
            "client_secret": secret,
        },
        timeout=30,
    )
    if response.status_code != 200:
        fail(f"token refresh failed: {response.status_code} {response.text[:200]}")
    return response.json()["access_token"]


def database_parts() -> tuple[str, str, str, str]:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        fail("DATABASE_URL is not set")
    parsed = urlparse(url.replace("+pg8000", ""))
    return (
        parsed.hostname or "127.0.0.1",
        str(parsed.port or 5432),
        unquote(parsed.username or ""),
        unquote(parsed.path.lstrip("/")),
    )


def dump_database(target: Path) -> None:
    host, port, user, name = database_parts()
    password = os.environ.get("PGPASSWORD") or ""
    env = {**os.environ, "PGPASSWORD": password}
    result = subprocess.run(
        ["pg_dump", "-h", host, "-p", port, "-U", user, "-Fc", "-f", str(target), name],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"pg_dump failed: {result.stderr[:300]}")


def build_archive(folder: Path) -> Path:
    date = datetime.date.today().isoformat()
    dump = folder / "hanna.dump"
    dump_database(dump)
    uploads = Path(os.environ.get("HANNA_UPLOAD_DIR", "/var/lib/hanna/uploads"))
    manifest = {
        "backup_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": os.uname().nodename,
        "database": "hanna (pg_dump custom format)",
        "uploads": str(uploads),
        "retention_days": RETENTION_DAYS,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    archive = folder / f"hanna-{date}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(dump, arcname="hanna.dump")
        tar.add(folder / "manifest.json", arcname="manifest.json")
        if uploads.exists():
            tar.add(uploads, arcname="uploads")
    return archive


def upload(token: str, data: bytes, remote_path: str) -> None:
    response = requests.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Dropbox-API-Arg": json.dumps(
                {"path": remote_path, "mode": "overwrite", "autorename": False, "mute": True}
            ),
            "Content-Type": "application/octet-stream",
        },
        data=data,
        timeout=300,
    )
    if response.status_code != 200:
        fail(f"upload failed: {response.status_code} {response.text[:200]}")


def prune(token: str) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.post(LIST_URL, headers=headers, json={"path": DROPBOX_ROOT}, timeout=30)
    if response.status_code == 409:
        return  # nothing there yet
    if response.status_code != 200:
        fail(f"list failed: {response.status_code} {response.text[:200]}")
    cutoff = datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)
    for entry in response.json().get("entries", []):
        if entry.get(".tag") != "folder":
            continue
        try:
            day = datetime.date.fromisoformat(entry["name"])
        except ValueError:
            continue
        if day < cutoff:
            requests.post(
                DELETE_URL,
                headers=headers,
                json={"path": entry["path_lower"]},
                timeout=30,
            )
            print(f"pruned {entry['name']}")


def main() -> None:
    key = os.environ.get("BACKUP_ENCRYPTION_KEY")
    if not key:
        fail("BACKUP_ENCRYPTION_KEY is not set")
    date = datetime.date.today().isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        archive = build_archive(folder)
        payload = archive.read_bytes()
        encrypted = Fernet(key.encode()).encrypt(payload)
        token = dropbox_token()
        remote = f"{DROPBOX_ROOT}/{date}/hanna-{date}.enc"
        upload(token, encrypted, remote)
        prune(token)
        print(
            f"backup {date}: {len(payload)/1048576:.1f} MB -> "
            f"{len(encrypted)/1048576:.1f} MB encrypted, {remote}"
        )


if __name__ == "__main__":
    main()
