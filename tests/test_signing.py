"""Signed image URLs: sign, verify, expiry, tamper."""

from __future__ import annotations

import time

from core import settings
from server import signing


def test_sign_and_verify(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNING_KEY_PATH", tmp_path / "key")
    token = signing.sign("run-1:aligned", ttl=60)
    assert signing.verify("run-1:aligned", token)
    assert not signing.verify("run-1:card", token), "resource is part of the signature"


def test_expired_token_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNING_KEY_PATH", tmp_path / "key")
    token = signing.sign("run-1:card", ttl=-1)
    assert not signing.verify("run-1:card", token)


def test_tampered_token_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNING_KEY_PATH", tmp_path / "key")
    expires, signature = signing.sign("run-1:card").split(".", 1)
    forged = f"{int(expires) + 3600}.{signature}"
    assert not signing.verify("run-1:card", forged)
    assert not signing.verify("run-1:card", "not-a-token")


def test_key_file_is_private(tmp_path, monkeypatch):
    key = tmp_path / "key"
    monkeypatch.setattr(settings, "SIGNING_KEY_PATH", key)
    signing.sign("x")
    assert key.exists()
    assert oct(key.stat().st_mode)[-3:] == "600"


def test_ttl_defaults_to_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNING_KEY_PATH", tmp_path / "key")
    monkeypatch.setattr(settings, "SIGNED_URL_TTL_SECONDS", 120)
    expires = int(signing.sign("x").split(".", 1)[0])
    assert 110 <= expires - int(time.time()) <= 125
