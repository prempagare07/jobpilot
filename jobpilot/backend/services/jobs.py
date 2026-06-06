from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.scrapers.base import ScrapedJob
from backend.services.hash_utils import job_id_for


def upsert_scraped_job(db: Session, scraped_job: ScrapedJob) -> Job:
    job_id = job_id_for(scraped_job.title, scraped_job.company, scraped_job.url)
    description = getattr(scraped_job, "job_description", getattr(scraped_job, "description", ""))
    existing = db.get(Job, job_id)
    if existing:
        existing.title = scraped_job.title
        existing.company = scraped_job.company
        existing.location = scraped_job.location
        existing.job_description = description
        existing.url = scraped_job.url
        existing.platform = scraped_job.platform
        existing.date_posted = scraped_job.date_posted
        existing.easy_apply = scraped_job.easy_apply
        existing.scraped_at = datetime.utcnow()
        return existing

    job = Job(
        id=job_id,
        title=scraped_job.title,
        company=scraped_job.company,
        location=scraped_job.location,
        job_description=description,
        url=scraped_job.url,
        platform=scraped_job.platform,
        date_posted=scraped_job.date_posted,
        easy_apply=scraped_job.easy_apply,
        status="new",
    )
    db.add(job)
    return job


def list_jobs(db: Session, status: str | None = None, limit: int = 100) -> list[Job]:
    statement = select(Job).order_by(Job.scraped_at.desc()).limit(limit)
    if status:
        statement = select(Job).where(Job.status == status).order_by(Job.scraped_at.desc()).limit(limit)
    return list(db.scalars(statement))
