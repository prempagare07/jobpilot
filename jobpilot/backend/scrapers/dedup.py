from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.scrapers.normalizer import NormalizedJob


class DedupEngine:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def is_duplicate(self, job_id: str) -> bool:
        return self.db.get(Job, job_id) is not None

    async def filter_new(self, jobs: list[NormalizedJob]) -> list[NormalizedJob]:
        if not jobs:
            return []
        ids = [job.id for job in jobs]
        existing = set(self.db.scalars(select(Job.id).where(Job.id.in_(ids))).all())
        seen: set[str] = set()
        new_jobs: list[NormalizedJob] = []
        for job in jobs:
            if job.id in existing or job.id in seen:
                continue
            seen.add(job.id)
            new_jobs.append(job)
        return new_jobs
