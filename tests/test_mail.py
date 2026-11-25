"""The mailer's disabled path. Sending is exercised by hand, not in CI."""

from core import mail, settings


def test_missing_configuration_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "")
    assert mail.send_mail("someone@example.test", "subject", "<p>x</p>") is False
