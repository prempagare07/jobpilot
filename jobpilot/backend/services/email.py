from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from backend.config import settings


@dataclass(frozen=True)
class OutboundEmail:
    to_email: str
    subject: str
    body: str
    from_email: str | None = None


def send_email(message: OutboundEmail) -> None:
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        raise RuntimeError("SMTP_HOST, SMTP_USER, and SMTP_PASSWORD must be configured before sending email")

    email = EmailMessage()
    email["From"] = message.from_email or settings.smtp_user
    email["To"] = message.to_email
    email["Subject"] = message.subject
    email.set_content(message.body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(email)
