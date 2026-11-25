"""Outgoing mail over ZeptoMail's SMTP relay.

One function, so invitations and password resets do not care which transport is
used. Credentials come from the environment; nothing is logged.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from . import settings


def send_mail(to: str, subject: str, html: str, to_name: str | None = None) -> bool:
    """Send one HTML message. Returns False when mail is not configured or fails."""
    if not (settings.SMTP_HOST and settings.SMTP_PASSWORD):
        return False

    message = EmailMessage()
    message["From"] = formataddr(("HANNA", settings.MAIL_FROM))
    message["To"] = formataddr((to_name or "", to))
    message["Subject"] = subject
    message.set_content("This message requires an HTML-capable mail client.")
    message.add_alternative(html, subtype="html")

    try:
        if settings.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=20,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
            server.starttls(context=ssl.create_default_context())
        try:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(message)
        finally:
            server.quit()
        return True
    except Exception:
        return False
