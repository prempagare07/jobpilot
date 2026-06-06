from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.email_writer import ColdEmailWriter, EmailDraft
from backend.db.models import Job, OutreachContact, Profile
from backend.services.apollo import ApolloClient, ApolloContact, apollo_contact_from_model
from backend.services.apply_common import model_to_dict
from backend.services.email_service import EmailSendResult, EmailService


@dataclass(frozen=True)
class OutreachPreview:
    contact_id: int
    contact_name: str
    email: str | None
    subject: str
    body: str
    personalization_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OutreachResult:
    contacts_found: int
    emails_sent: int
    skipped: int
    previews: list[OutreachPreview] = field(default_factory=list)


class OutreachService:
    def __init__(
        self,
        db: Session,
        apollo_client: ApolloClient | None = None,
        email_service: EmailService | None = None,
        email_writer: ColdEmailWriter | None = None,
    ) -> None:
        self.db = db
        self.apollo_client = apollo_client or ApolloClient(db)
        self.email_service = email_service or EmailService(db)
        self.email_writer = email_writer or ColdEmailWriter()

    async def run_outreach_for_job(self, job_id: str) -> OutreachResult:
        job = self._get_job(job_id)
        profile = self._get_profile()
        contacts = await self.apollo_client.find_hiring_managers(job.company, job.title)
        previews: list[OutreachPreview] = []
        emails_sent = 0
        skipped = 0

        for contact in contacts:
            db_contact = self._save_contact_for_job(contact, job.id)
            if db_contact.email_sent:
                skipped += 1
                continue
            if not is_verified_email_contact(db_contact):
                skipped += 1
                continue

            draft = await self._draft_email(db_contact, job, profile)
            db_contact.email_subject = draft.subject
            db_contact.email_body = draft.body
            self.db.commit()
            previews.append(
                OutreachPreview(
                    contact_id=db_contact.id,
                    contact_name=db_contact.name,
                    email=db_contact.email,
                    subject=draft.subject,
                    body=draft.body,
                    personalization_notes=draft.personalization_notes,
                )
            )
            print_email_preview(db_contact, draft)
            approval = input("Send this email? [y/n/edit]: ").strip().lower()
            if approval == "edit":
                draft = EmailDraft(
                    subject=input("Subject: ").strip() or draft.subject,
                    body=input("Body: ").strip() or draft.body,
                    personalization_notes=draft.personalization_notes,
                )
            if approval in {"y", "yes", "edit"} and db_contact.email:
                result = await self.email_service.send_email(
                    to_email=db_contact.email,
                    subject=draft.subject,
                    body=draft.body,
                    from_name=profile.full_name,
                )
                emails_sent += 1 if result.success else 0
            else:
                skipped += 1

        return OutreachResult(
            contacts_found=len(contacts),
            emails_sent=emails_sent,
            skipped=skipped,
            previews=previews,
        )

    async def preview_outreach_for_job(self, job_id: str) -> OutreachResult:
        job = self._get_job(job_id)
        profile = self._get_profile()
        contacts = await self.apollo_client.find_hiring_managers(job.company, job.title)
        previews: list[OutreachPreview] = []
        skipped = 0
        for contact in contacts:
            db_contact = self._save_contact_for_job(contact, job.id)
            if not is_verified_email_contact(db_contact):
                skipped += 1
                continue
            draft = await self._draft_email(db_contact, job, profile)
            db_contact.email_subject = draft.subject
            db_contact.email_body = draft.body
            self.db.commit()
            previews.append(
                OutreachPreview(
                    contact_id=db_contact.id,
                    contact_name=db_contact.name,
                    email=db_contact.email,
                    subject=draft.subject,
                    body=draft.body,
                    personalization_notes=draft.personalization_notes,
                )
            )
        return OutreachResult(contacts_found=len(contacts), emails_sent=0, skipped=skipped, previews=previews)

    async def send_contact_email(self, contact_id: str) -> EmailSendResult:
        contact = self._get_contact(contact_id)
        profile = self._get_profile()
        if not contact.email:
            return EmailSendResult(success=False, message_id="", error="Contact has no email address.")
        if not contact.email_subject or not contact.email_body:
            if not contact.job_id:
                return EmailSendResult(success=False, message_id="", error="No email draft and no linked job.")
            job = self._get_job(contact.job_id)
            draft = await self._draft_email(contact, job, profile)
            contact.email_subject = draft.subject
            contact.email_body = draft.body
            self.db.commit()
        return await self.email_service.send_email(
            to_email=contact.email,
            subject=contact.email_subject,
            body=contact.email_body,
            from_name=profile.full_name,
        )

    async def run_send_bulk_for_job(
        self,
        job_id: str,
        require_console_approval: bool = True,
    ) -> OutreachResult:
        job = self._get_job(job_id)
        profile = self._get_profile()
        contacts = list(
            self.db.scalars(
                select(OutreachContact).where(
                    OutreachContact.job_id == job.id,
                    OutreachContact.email_sent.is_(False),
                )
            )
        )
        emails_sent = 0
        skipped = 0
        previews: list[OutreachPreview] = []
        for contact in contacts:
            if not is_verified_email_contact(contact):
                skipped += 1
                continue
            draft = await self._draft_email(contact, job, profile)
            contact.email_subject = draft.subject
            contact.email_body = draft.body
            self.db.commit()
            previews.append(
                OutreachPreview(
                    contact_id=contact.id,
                    contact_name=contact.name,
                    email=contact.email,
                    subject=draft.subject,
                    body=draft.body,
                    personalization_notes=draft.personalization_notes,
                )
            )
            if require_console_approval:
                print_email_preview(contact, draft)
                approval = input("Send this email? [y/n/edit]: ").strip().lower()
                if approval not in {"y", "yes"}:
                    skipped += 1
                    continue
            result = await self.email_service.send_email(
                to_email=contact.email or "",
                subject=draft.subject,
                body=draft.body,
                from_name=profile.full_name,
            )
            emails_sent += 1 if result.success else 0
            await asyncio.sleep(180)
        return OutreachResult(
            contacts_found=len(contacts),
            emails_sent=emails_sent,
            skipped=skipped,
            previews=previews,
        )

    async def run_followup_cycle(self) -> None:
        cutoff = datetime.utcnow() - timedelta(days=5)
        contacts = list(
            self.db.scalars(
                select(OutreachContact).where(
                    OutreachContact.email_sent.is_(True),
                    OutreachContact.reply_received.is_(False),
                    OutreachContact.email_sent_at < cutoff,
                    OutreachContact.follow_up_sent_at.is_(None),
                    OutreachContact.email.is_not(None),
                )
            )
        )
        profile = self._get_profile(required=False)
        from_name = profile.full_name if profile else None
        for contact in contacts:
            subject = followup_subject(contact)
            body = followup_body(contact, profile)
            original_sent_at = contact.email_sent_at
            result = await self.email_service.send_email(
                to_email=contact.email or "",
                subject=subject,
                body=body,
                from_name=from_name,
            )
            if result.success:
                contact.email_sent_at = original_sent_at
                contact.follow_up_sent_at = datetime.utcnow()
                contact.email_subject = subject
                contact.email_body = body
                self.db.commit()
            await asyncio.sleep(180)

    async def mark_reply_received(self, contact_id: str) -> None:
        contact = self._get_contact(contact_id)
        contact.reply_received = True
        contact.reply_at = datetime.utcnow()
        self.db.commit()

    def _save_contact_for_job(self, contact: ApolloContact, job_id: str) -> OutreachContact:
        db_contact = self.apollo_client._upsert_contact(contact, job_id=job_id)
        if db_contact.job_id is None:
            db_contact.job_id = job_id
        self.db.commit()
        self.db.refresh(db_contact)
        return db_contact

    async def _draft_email(self, contact: OutreachContact, job: Job, profile: Profile) -> EmailDraft:
        return await self.email_writer.write(
            contact=model_to_dict(contact),
            job=model_to_dict(job),
            profile=model_to_dict(profile),
        )

    def _get_job(self, job_id: str) -> Job:
        job = self.db.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")
        return job

    def _get_contact(self, contact_id: str) -> OutreachContact:
        contact = None
        if contact_id.isdigit():
            contact = self.db.get(OutreachContact, int(contact_id))
        if contact is None:
            contact = self.db.scalar(select(OutreachContact).where(OutreachContact.apollo_id == contact_id))
        if contact is None:
            raise ValueError(f"Outreach contact not found: {contact_id}")
        return contact

    def _get_profile(self, required: bool = True) -> Profile | None:
        profile = self.db.scalar(select(Profile).order_by(Profile.id.asc()))
        if profile is None and required:
            raise ValueError("Profile must be created before outreach.")
        return profile


def is_verified_email_contact(contact: OutreachContact) -> bool:
    if not contact.email:
        return False
    status = (contact.email_status or "").lower()
    return status in {"verified", "likely to engage", ""} and not contact.email_sent


def print_email_preview(contact: OutreachContact, draft: EmailDraft) -> None:
    print("\n" + "=" * 72)
    print(f"To: {contact.name} <{contact.email}>")
    print(f"Company: {contact.company}")
    print(f"Subject: {draft.subject}")
    print("-" * 72)
    print(draft.body)
    print("=" * 72 + "\n")


def followup_subject(contact: OutreachContact) -> str:
    company = contact.company or "your team"
    return f"Quick follow-up on {company}"[:80]


def followup_body(contact: OutreachContact, profile: Profile | None) -> str:
    name = contact.name.split()[0] if contact.name else "there"
    sender = profile.full_name if profile else "Prem"
    linkedin = profile.linkedin_url if profile and profile.linkedin_url else ""
    github = profile.github_url if profile and profile.github_url else ""
    links = "\n".join(link for link in (linkedin, github) if link)
    return (
        f"Hi {name},\n\n"
        "I wanted to briefly follow up on my note about the engineering role. "
        "If there is someone better to speak with, I would appreciate the pointer.\n\n"
        "Happy to share more if helpful.\n\n"
        f"Best,\n{sender}"
        + (f"\n{links}" if links else "")
        + "\n\nIf you'd prefer not to receive messages from me, reply STOP."
    )


def outreach_contact_to_apollo(contact: OutreachContact) -> ApolloContact:
    return apollo_contact_from_model(contact)
