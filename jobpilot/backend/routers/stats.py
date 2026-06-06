from __future__ import annotations

from datetime import datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.models import Application, Job, OutreachContact, SchedulerRun
from backend.services.database import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])


class PlatformCountOut(BaseModel):
    platform: str
    count: int


class WeekCountOut(BaseModel):
    week: str
    count: int


class DashboardStatsOut(BaseModel):
    jobs_scraped_total: int
    jobs_applied_total: int
    jobs_applied_today: int
    daily_limit: int
    interviews: int
    offers: int
    emails_sent: int
    reply_rate: float
    top_platforms: list[PlatformCountOut]
    applications_by_week: list[WeekCountOut]
    avg_ats_score: float
    last_scrape: datetime | None


@router.get("/dashboard", response_model=DashboardStatsOut)
def dashboard_stats(db: Annotated[Session, Depends(get_db)]) -> DashboardStatsOut:
    today_start = datetime.combine(datetime.utcnow().date(), time.min)
    jobs_scraped_total = int(db.scalar(select(func.count()).select_from(Job)) or 0)
    submitted_applications = Application.apply_status == "applied"
    jobs_applied_total = int(
        db.scalar(select(func.count()).select_from(Application).where(submitted_applications)) or 0
    )
    jobs_applied_today = int(
        db.scalar(
            select(func.count())
            .select_from(Application)
            .where(Application.applied_at >= today_start, submitted_applications)
        )
        or 0
    )
    interviews = int(db.scalar(select(func.count()).select_from(Job).where(Job.status == "interview")) or 0)
    offers = int(db.scalar(select(func.count()).select_from(Job).where(Job.status == "offer")) or 0)
    emails_sent = int(
        db.scalar(select(func.count()).select_from(OutreachContact).where(OutreachContact.email_sent.is_(True))) or 0
    )
    replies = int(
        db.scalar(
            select(func.count()).select_from(OutreachContact).where(OutreachContact.reply_received.is_(True))
        )
        or 0
    )
    avg_ats_score = float(db.scalar(select(func.coalesce(func.avg(Job.ats_score), 0.0))) or 0.0)
    last_scrape = db.scalar(
        select(SchedulerRun.finished_at)
        .where(SchedulerRun.run_type == "scrape", SchedulerRun.finished_at.is_not(None))
        .order_by(SchedulerRun.finished_at.desc())
        .limit(1)
    )

    platform_rows = db.execute(
        select(Job.platform, func.count())
        .group_by(Job.platform)
        .order_by(func.count().desc())
        .limit(5)
    ).all()
    week_rows = db.execute(
        select(func.strftime("%Y-%W", Application.applied_at), func.count())
        .where(submitted_applications)
        .group_by(func.strftime("%Y-%W", Application.applied_at))
        .order_by(func.strftime("%Y-%W", Application.applied_at))
    ).all()

    return DashboardStatsOut(
        jobs_scraped_total=jobs_scraped_total,
        jobs_applied_total=jobs_applied_total,
        jobs_applied_today=jobs_applied_today,
        daily_limit=settings.apply_daily_limit,
        interviews=interviews,
        offers=offers,
        emails_sent=emails_sent,
        reply_rate=round((replies / emails_sent) * 100, 2) if emails_sent else 0.0,
        top_platforms=[PlatformCountOut(platform=row[0], count=row[1]) for row in platform_rows],
        applications_by_week=[WeekCountOut(week=row[0], count=row[1]) for row in week_rows],
        avg_ats_score=round(avg_ats_score, 2),
        last_scrape=last_scrape,
    )
