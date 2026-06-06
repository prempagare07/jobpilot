from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Annotated, Any

import uuid

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Application, Job
from backend.services.application_service import ApplicationService
from backend.services.database import SessionLocal, get_db
from backend.services.task_manager import task_registry

router = APIRouter(prefix="/api/applications", tags=["applications"])


class JobSummaryOut(BaseModel):
    id: str
    title: str
    company: str
    platform: str
    status: str
    ats_score: float | None

    model_config = {"from_attributes": True}


class ApplicationOut(BaseModel):
    id: int
    job_id: str
    resume_version_id: int | None
    resume_name: str | None
    resume_path: str | None = None
    cover_letter_text: str | None
    cover_letter_path: str | None = None
    applied_at: datetime
    follow_up_sent_at: datetime | None
    status: str
    apply_status: str
    ats_platform: str | None
    task_id: str | None
    questions_encountered_json: list[str]
    questions_needing_human_json: list[str]
    failure_reason: str | None
    screenshot_url: str | None
    response_received: bool
    notes: str | None
    audit_log_json: list[dict] = Field(default_factory=list)
    job: JobSummaryOut | None = None

    model_config = {"from_attributes": True}


class ApplyTaskOut(BaseModel):
    task_id: str


class TaskStatusOut(BaseModel):
    task_id: str
    name: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    result: Any = None
    error: str | None = None


class BatchApplyIn(BaseModel):
    job_ids: list[str] = Field(min_length=1)
    delay_between: int = Field(default=30, ge=0)
    human_approved: bool = False


class ApplyIn(BaseModel):
    human_approved: bool = False
    cover_letter_text: str | None = None


@router.get("/", response_model=list[ApplicationOut])
def list_applications(db: Annotated[Session, Depends(get_db)]) -> list[ApplicationOut]:
    recover_orphaned_applications(db)
    applications = list(db.scalars(select(Application).order_by(Application.applied_at.desc())))
    return [application_to_out(application) for application in applications]


@router.get("/today", response_model=list[ApplicationOut])
def applications_today(db: Annotated[Session, Depends(get_db)]) -> list[ApplicationOut]:
    recover_orphaned_applications(db)
    today_start = datetime.combine(datetime.utcnow().date(), time.min)
    applications = list(
        db.scalars(
            select(Application)
            .where(Application.applied_at >= today_start)
            .order_by(Application.applied_at.desc())
        )
    )
    return [application_to_out(application) for application in applications]


@router.get("/apply/status/{task_id}", response_model=TaskStatusOut)
def apply_status(task_id: str) -> dict[str, Any]:
    task = task_registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/apply/{job_id}", response_model=ApplyTaskOut)
async def apply_to_job(
    job_id: str,
    payload: ApplyIn | None = Body(default=None),
    human_approved: bool = False,
) -> ApplyTaskOut:
    approved = payload.human_approved if payload is not None else human_approved
    cover_letter_text = payload.cover_letter_text if payload is not None else None
    task_id = str(uuid.uuid4())
    task_registry.create(
        "apply",
        run_apply_background(
            job_id=job_id,
            human_approved=approved,
            cover_letter_text=cover_letter_text,
            task_id=task_id,
        ),
        task_id=task_id,
    )
    return ApplyTaskOut(task_id=task_id)


@router.post("/apply/batch", response_model=ApplyTaskOut)
async def batch_apply(payload: BatchApplyIn) -> ApplyTaskOut:
    task_id = str(uuid.uuid4())
    task_registry.create(
        "batch_apply",
        run_batch_apply_background(
            job_ids=payload.job_ids,
            delay_between=payload.delay_between,
            human_approved=payload.human_approved,
            task_id=task_id,
        ),
        task_id=task_id,
    )
    return ApplyTaskOut(task_id=task_id)


@router.post("/{application_id}/retry", response_model=ApplyTaskOut)
async def retry_application(
    application_id: int,
    payload: ApplyIn | None = Body(default=None),
    db: Session = Depends(get_db),
) -> ApplyTaskOut:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    job = db.get(Job, application.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Reset job so the apply service will accept it
    job.status = "reviewed"
    db.commit()

    approved = payload.human_approved if payload is not None else False
    cover_letter_text = payload.cover_letter_text if payload is not None else None
    task_id = str(uuid.uuid4())
    task_registry.create(
        "apply",
        run_apply_background(
            job_id=job.id,
            human_approved=approved,
            cover_letter_text=cover_letter_text,
            task_id=task_id,
        ),
        task_id=task_id,
    )
    return ApplyTaskOut(task_id=task_id)


@router.delete("/{application_id}", response_model=dict)
def delete_application(application_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    db.delete(application)
    db.commit()
    return {"deleted": True, "id": application_id}


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(application_id: int, db: Annotated[Session, Depends(get_db)]) -> ApplicationOut:
    recover_orphaned_applications(db)
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application_to_out(application)


@router.get("/{application_id}/cover-letter-pdf")
def download_cover_letter(application_id: int, db: Annotated[Session, Depends(get_db)]) -> FileResponse:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    path = application.cover_letter_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Cover letter PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=Path(path).name)


@router.get("/{application_id}/resume-pdf")
def download_resume(application_id: int, db: Annotated[Session, Depends(get_db)]) -> FileResponse:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    path = application.resume_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Resume PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=Path(path).name)


async def run_apply_background(
    job_id: str,
    human_approved: bool,
    cover_letter_text: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    with SessionLocal() as db:
        result = await ApplicationService(db).apply_to_job(
            job_id=job_id,
            human_approved=human_approved,
            cover_letter_override=cover_letter_text,
            task_id=task_id,
        )
        apply_result = result.apply_result
        return {
            "job_id": result.job_id,
            "status": result.status,
            "success": result.success,
            "screenshot_path": result.screenshot_path,
            "screenshot_url": screenshot_url(result.screenshot_path),
            "questions": result.questions,
            "questions_encountered": apply_result.questions_encountered if apply_result else [],
            "questions_needing_human": apply_result.questions_needing_human if apply_result else result.questions,
            "final_url": apply_result.url if apply_result else None,
            "reason": apply_result.reason if apply_result else None,
            "error": result.error,
        }


async def run_batch_apply_background(
    job_ids: list[str],
    delay_between: int,
    human_approved: bool,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with SessionLocal() as db:
        service = ApplicationService(db)
        for index, job_id in enumerate(job_ids):
            result = await service.apply_to_job(job_id=job_id, human_approved=human_approved, task_id=task_id)
            apply_result = result.apply_result
            results.append(
                {
                    "job_id": result.job_id,
                    "status": result.status,
                    "success": result.success,
                    "screenshot_path": result.screenshot_path,
                    "screenshot_url": screenshot_url(result.screenshot_path),
                    "questions": result.questions,
                    "questions_encountered": apply_result.questions_encountered if apply_result else [],
                    "questions_needing_human": apply_result.questions_needing_human if apply_result else result.questions,
                    "final_url": apply_result.url if apply_result else None,
                    "reason": apply_result.reason if apply_result else None,
                    "error": result.error,
                }
            )
            if result.status == "needs_human":
                break
            if index < len(job_ids) - 1 and delay_between:
                await asyncio.sleep(delay_between)
    return results


def application_to_out(application: Application) -> ApplicationOut:
    job = application.job
    return ApplicationOut(
        id=application.id,
        job_id=application.job_id,
        resume_version_id=application.resume_version_id,
        resume_name=application.resume_name,
        cover_letter_text=application.cover_letter_text,
        applied_at=application.applied_at,
        follow_up_sent_at=application.follow_up_sent_at,
        status=application.status,
        apply_status=getattr(application, "apply_status", "applied"),
        ats_platform=getattr(application, "ats_platform", None),
        task_id=getattr(application, "task_id", None),
        questions_encountered_json=getattr(application, "questions_encountered_json", []) or [],
        questions_needing_human_json=getattr(application, "questions_needing_human_json", []) or [],
        failure_reason=getattr(application, "failure_reason", None),
        screenshot_url=getattr(application, "screenshot_url", None),
        response_received=application.response_received,
        notes=application.notes,
        audit_log_json=getattr(application, "audit_log_json", []) or [],
        job=JobSummaryOut.model_validate(job) if isinstance(job, Job) else None,
    )


def screenshot_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"/screenshots/{path.rsplit('/', 1)[-1]}"


def recover_orphaned_applications(db: Session) -> None:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    live_statuses = ("pending", "preparing", "running", "waiting_review", "waiting_captcha")
    applications = list(
        db.scalars(
            select(Application)
            .where(Application.apply_status.in_(live_statuses), Application.applied_at < cutoff)
            .order_by(Application.applied_at.asc())
        )
    )
    changed = False
    for application in applications:
        if application.task_id and task_registry.get(application.task_id):
            continue
        message = (
            "Application automation was interrupted, most likely by a FastAPI reload or server restart. "
            "Click Resume Application to start it again."
        )
        audit_entries = list(application.audit_log_json or [])
        audit_entries.append(
            {
                "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "event": "error",
                "message": message,
            }
        )
        application.apply_status = "needs_human"
        application.failure_reason = message
        application.questions_needing_human_json = [message]
        application.audit_log_json = audit_entries
        changed = True
    if changed:
        db.commit()
