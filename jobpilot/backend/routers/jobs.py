from __future__ import annotations

from datetime import datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.scrapers.runner import run_scrape_cycle
from backend.services.database import SessionLocal, get_db
from backend.services.job_preparation import JobPreparationService
from backend.services.task_manager import task_registry

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

VALID_STATUSES = {"new", "queued", "reviewed", "applied", "skip", "interview", "offer", "rejected", "failed"}


class JobOut(BaseModel):
    id: str
    title: str
    company: str
    location: str | None
    job_description: str
    url: str
    platform: str
    date_posted: datetime | None
    easy_apply: bool
    scraped_at: datetime
    status: str
    resume_version_id: int | None
    ats_score: float | None
    notes: str | None
    applied_at: datetime | None

    model_config = {"from_attributes": True}


class JobStatusIn(BaseModel):
    status: str = Field(pattern="^(new|queued|reviewed|applied|skip|interview|offer|rejected|failed)$")


class JobNotesIn(BaseModel):
    notes: str | None = None


class TriggerTaskOut(BaseModel):
    task_id: str


class ATSResultOut(BaseModel):
    resume_name: str
    score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    recommendation: str
    tailoring_suggestions: list[str]


class PreparedApplicationOut(BaseModel):
    job_id: str
    resume_version_id: int
    resume_name: str
    ats_result: ATSResultOut
    cover_letter_text: str
    warnings: list[str]
    job: JobOut


@router.get("/", response_model=list[JobOut])
def list_jobs(
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    min_ats: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Job]:
    statement = select(Job)
    if status:
        statement = statement.where(Job.status == status)
    if platform:
        statement = statement.where(Job.platform == platform)
    if min_ats is not None:
        statement = statement.where(Job.ats_score >= min_ats)
    statement = statement.order_by(Job.scraped_at.desc()).offset(offset).limit(limit)
    return list(db.scalars(statement))


@router.get("/stats")
def job_stats(db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    today_start = datetime.combine(datetime.utcnow().date(), time.min)
    by_status = dict(
        db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    )
    by_platform = dict(
        db.execute(select(Job.platform, func.count()).group_by(Job.platform)).all()
    )
    count_today = int(
        db.scalar(select(func.count()).select_from(Job).where(Job.scraped_at >= today_start)) or 0
    )
    return {"by_status": by_status, "by_platform": by_platform, "count_today": count_today}


@router.post("/trigger-scrape", response_model=TriggerTaskOut)
async def trigger_scrape() -> TriggerTaskOut:
    task_id = task_registry.create("scrape", run_scrape_background())
    return TriggerTaskOut(task_id=task_id)


@router.post("/{job_id}/prepare", response_model=PreparedApplicationOut)
async def prepare_job(job_id: str, db: Annotated[Session, Depends(get_db)]) -> PreparedApplicationOut:
    try:
        prepared = await JobPreparationService(db).prepare(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return PreparedApplicationOut(
        job_id=prepared.job_id,
        resume_version_id=prepared.resume_version_id,
        resume_name=prepared.resume_name,
        ats_result=ATSResultOut(**prepared.ats_result.__dict__),
        cover_letter_text=prepared.cover_letter_text,
        warnings=prepared.warnings,
        job=JobOut.model_validate(job),
    )


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Annotated[Session, Depends(get_db)]) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.put("/{job_id}/status", response_model=JobOut)
def update_job_status(job_id: str, payload: JobStatusIn, db: Annotated[Session, Depends(get_db)]) -> Job:
    if payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Status must be one of {sorted(VALID_STATUSES)}")
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = payload.status
    db.commit()
    db.refresh(job)
    return job


@router.put("/{job_id}/notes", response_model=JobOut)
def update_job_notes(job_id: str, payload: JobNotesIn, db: Annotated[Session, Depends(get_db)]) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.notes = payload.notes
    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/refresh-jd", response_model=JobOut)
async def refresh_job_description(job_id: str, db: Annotated[Session, Depends(get_db)]) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    from backend.services.jd_fetcher import fetch_full_jd
    full_jd = await fetch_full_jd(job.url, timeout=30.0)
    if not full_jd:
        raise HTTPException(
            status_code=422,
            detail="Source URL returned an application form instead of a job description, or was unreachable. Original description kept.",
        )
    job.job_description = full_jd
    db.commit()
    db.refresh(job)
    return job


async def run_scrape_background() -> dict[str, object]:
    with SessionLocal() as db_session:
        return await run_scrape_cycle(db_session)
