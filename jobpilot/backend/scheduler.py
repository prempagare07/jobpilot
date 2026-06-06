from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from tzlocal import get_localzone

from backend.config import settings
from backend.db.models import SchedulerRun
from backend.scrapers.runner import run_scrape_cycle
from backend.services.database import SessionLocal
from backend.services.outreach_service import OutreachService


def scheduler_database_url() -> str:
    return f"sqlite:///{settings.sqlite_path}"


def build_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(
        timezone=get_localzone(),
        jobstores={"default": SQLAlchemyJobStore(url=scheduler_database_url())},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60 * 30},
    )


scheduler = build_scheduler()


async def scheduled_scrape_job() -> dict[str, Any]:
    with SessionLocal() as db_session:
        return await run_scrape_cycle(db_session)


async def run_outreach_followup() -> dict[str, Any]:
    started_at = datetime.utcnow()
    with SessionLocal() as db_session:
        run = SchedulerRun(run_type="outreach", started_at=started_at, jobs_found=0, jobs_applied=0, errors_json=[])
        db_session.add(run)
        try:
            await OutreachService(db_session).run_followup_cycle()
            run.errors_json = []
        except Exception as exc:
            run.errors_json = [str(exc)]
        run.finished_at = datetime.utcnow()
        db_session.commit()
        print(f"[scheduler] outreach_followup run_id={run.id} errors={len(run.errors_json)}")
        return {"run_id": run.id, "errors": run.errors_json}


def start_scheduler() -> AsyncIOScheduler:
    configure_jobs()
    if not scheduler.running:
        scheduler.start()
    if should_run_scrape_immediately():
        asyncio.get_running_loop().create_task(scheduled_scrape_job())
    return scheduler


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        event_loop = getattr(scheduler, "_eventloop", None)
        if event_loop and not event_loop.is_running() and not event_loop.is_closed():
            event_loop.run_until_complete(asyncio.sleep(0))


def configure_jobs() -> None:
    scheduler.add_job(
        scheduled_scrape_job,
        trigger="interval",
        hours=settings.scrape_interval_hours,
        id="scrape_jobs",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_outreach_followup,
        trigger="cron",
        hour=9,
        minute=0,
        id="outreach_followup",
        replace_existing=True,
        max_instances=1,
    )


def should_run_scrape_immediately() -> bool:
    with SessionLocal() as db_session:
        last_run = db_session.scalar(
            select(SchedulerRun)
            .where(SchedulerRun.run_type == "scrape", SchedulerRun.finished_at.is_not(None))
            .order_by(SchedulerRun.finished_at.desc())
        )
    if last_run is None or last_run.finished_at is None:
        return True
    return datetime.utcnow() - last_run.finished_at > timedelta(hours=settings.scrape_interval_hours)
