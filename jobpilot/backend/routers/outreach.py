from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import OutreachContact
from backend.services.database import get_db
from backend.services.email_service import EmailSendResult
from backend.services.outreach_service import OutreachPreview, OutreachResult, OutreachService

router = APIRouter(prefix="/api/outreach", tags=["outreach"])


class OutreachContactOut(BaseModel):
    id: int
    name: str
    title: str | None
    email: str | None
    email_status: str | None
    company: str
    seniority: str | None
    department: str | None
    linkedin_url: str | None
    job_id: str | None
    email_sent: bool
    email_sent_at: datetime | None
    follow_up_sent_at: datetime | None
    email_subject: str | None
    email_body: str | None
    reply_received: bool
    reply_at: datetime | None
    notes: str | None
    apollo_id: str | None

    model_config = {"from_attributes": True}


class OutreachPreviewOut(BaseModel):
    contact_id: int
    contact_name: str
    email: str | None
    subject: str
    body: str
    personalization_notes: list[str]


class OutreachResultOut(BaseModel):
    contacts_found: int
    emails_sent: int
    skipped: int
    previews: list[OutreachPreviewOut]


class EmailSendResultOut(BaseModel):
    success: bool
    message_id: str
    error: str | None = None


class OutreachStatsOut(BaseModel):
    sent: int
    opened: int
    replied: int
    reply_rate: float


@router.get("/contacts", response_model=list[OutreachContactOut])
def list_contacts(db: Annotated[Session, Depends(get_db)]) -> list[OutreachContact]:
    return list(db.scalars(select(OutreachContact).order_by(OutreachContact.id.desc())))


@router.post("/find/{job_id}", response_model=OutreachResultOut)
async def find_hiring_managers(job_id: str, db: Annotated[Session, Depends(get_db)]) -> OutreachResult:
    try:
        return await OutreachService(db).preview_outreach_for_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/send/{contact_id}", response_model=EmailSendResultOut)
async def send_contact_email(contact_id: str, db: Annotated[Session, Depends(get_db)]) -> EmailSendResult:
    try:
        return await OutreachService(db).send_contact_email(contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/send-bulk/{job_id}", response_model=OutreachResultOut)
async def send_bulk_for_job(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    approved: bool = Query(default=False),
) -> OutreachResult:
    try:
        if not approved:
            return await OutreachService(db).preview_outreach_for_job(job_id)
        return await OutreachService(db).run_send_bulk_for_job(job_id, require_console_approval=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/contacts/{contact_id}/replied")
async def mark_replied(contact_id: str, db: Annotated[Session, Depends(get_db)]) -> dict[str, bool]:
    try:
        await OutreachService(db).mark_reply_received(contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"updated": True}


@router.get("/stats", response_model=OutreachStatsOut)
def outreach_stats(db: Annotated[Session, Depends(get_db)]) -> OutreachStatsOut:
    sent = int(
        db.scalar(select(func.count()).select_from(OutreachContact).where(OutreachContact.email_sent.is_(True))) or 0
    )
    replied = int(
        db.scalar(
            select(func.count()).select_from(OutreachContact).where(OutreachContact.reply_received.is_(True))
        )
        or 0
    )
    reply_rate = round((replied / sent) * 100, 2) if sent else 0.0
    return OutreachStatsOut(sent=sent, opened=0, replied=replied, reply_rate=reply_rate)


def preview_to_out(preview: OutreachPreview) -> OutreachPreviewOut:
    return OutreachPreviewOut(**preview.__dict__)
