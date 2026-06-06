from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.models import OutreachContact


@dataclass(frozen=True)
class EmailSendResult:
    success: bool
    message_id: str
    error: str | None = None


class EmailService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_name: str | None = None,
    ) -> EmailSendResult:
        message_id = make_msgid(domain=email_domain(settings.smtp_user))
        try:
            await asyncio.to_thread(self._send_sync, to_email, subject, body, from_name, message_id)
            self._log_sent_email(to_email=to_email, subject=subject, body=body)
            return EmailSendResult(success=True, message_id=message_id)
        except Exception as exc:
            return EmailSendResult(success=False, message_id=message_id, error=str(exc))

    async def send_bulk(self, contacts: list, delay_seconds: int = 180) -> list[EmailSendResult]:
        results: list[EmailSendResult] = []
        consecutive_failures = 0
        for index, contact in enumerate(contacts):
            to_email = contact.get("email") if isinstance(contact, dict) else getattr(contact, "email", None)
            subject = (
                contact.get("email_subject")
                if isinstance(contact, dict)
                else getattr(contact, "email_subject", None)
            )
            body = contact.get("email_body") if isinstance(contact, dict) else getattr(contact, "email_body", None)
            if not to_email or not subject or not body:
                results.append(EmailSendResult(success=False, message_id="", error="Missing email, subject, or body."))
                consecutive_failures += 1
            else:
                result = await self.send_email(to_email=to_email, subject=subject, body=body)
                results.append(result)
                consecutive_failures = 0 if result.success else consecutive_failures + 1

            if consecutive_failures >= 3:
                break
            if index < len(contacts) - 1:
                await asyncio.sleep(delay_seconds)
        return results

    def _send_sync(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_name: str | None,
        message_id: str,
    ) -> None:
        if not settings.smtp_user or not settings.smtp_password:
            raise RuntimeError("SMTP_USER and SMTP_PASSWORD must be set. Use a Gmail app password.")

        host = settings.smtp_host or "smtp.gmail.com"
        message = MIMEMultipart("alternative")
        message["From"] = formataddr((from_name or settings.smtp_user, settings.smtp_user))
        message["To"] = to_email
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(render_html_email(body), "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(host, settings.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)

    def _log_sent_email(self, to_email: str, subject: str, body: str) -> None:
        if self.db is None:
            return
        contact = self.db.scalar(select(OutreachContact).where(OutreachContact.email == to_email).limit(1))
        if contact is None:
            return
        contact.email_sent = True
        contact.email_sent_at = datetime.utcnow()
        contact.email_subject = subject
        contact.email_body = body
        self.db.commit()


def render_html_email(body: str) -> str:
    paragraphs = [line.strip() for line in body.splitlines() if line.strip()]
    rendered_paragraphs = "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    font_stack = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"
    container_style = (
        "max-width:600px;margin:0 auto;padding:24px;"
        f"font-family:{font_stack};font-size:15px;line-height:1.6;color:#111827;"
    )
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#ffffff;">
    <div style="{container_style}">
      {rendered_paragraphs}
      <p style="margin-top:28px;color:#6b7280;font-size:12px;">
        If you'd prefer not to receive messages from me, reply STOP.
      </p>
    </div>
  </body>
</html>"""


def email_domain(email_address: str) -> str:
    if "@" not in email_address:
        return "jobpilot.local"
    return email_address.rsplit("@", 1)[-1]
