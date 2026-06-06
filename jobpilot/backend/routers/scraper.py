from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import SchedulerRun
from backend.scrapers.runner import run_scrape_cycle
from backend.services.database import SessionLocal, get_db
from backend.services.task_manager import task_registry

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


class TaskOut(BaseModel):
    task_id: str


class SchedulerRunOut(BaseModel):
    id: int
    run_type: str
    started_at: datetime
    finished_at: datetime | None
    jobs_found: int
    jobs_applied: int
    errors_json: list[str]

    model_config = {"from_attributes": True}


@router.post("/run", response_model=TaskOut)
async def run_scraper() -> TaskOut:
    task_id = task_registry.create("scrape", run_scrape_background())
    return TaskOut(task_id=task_id)


@router.get("/status")
def scraper_status(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    last_run = db.scalar(
        select(SchedulerRun)
        .where(SchedulerRun.run_type == "scrape")
        .order_by(SchedulerRun.started_at.desc())
        .limit(1)
    )
    active_tasks = [
        task
        for task in task_registry.list()
        if task["name"] == "scrape" and task["status"] in {"queued", "running"}
    ]
    return {"last_run": scheduler_run_to_dict(last_run), "active_tasks": active_tasks}


@router.get("/runs", response_model=list[SchedulerRunOut])
def scraper_runs(
    db: Annotated[Session, Depends(get_db)],
    limit: int = 50,
) -> list[SchedulerRun]:
    return list(
        db.scalars(
            select(SchedulerRun)
            .where(SchedulerRun.run_type == "scrape")
            .order_by(SchedulerRun.started_at.desc())
            .limit(limit)
        )
    )


async def run_scrape_background() -> dict[str, object]:
    with SessionLocal() as db_session:
        return await run_scrape_cycle(db_session)


def scheduler_run_to_dict(run: SchedulerRun | None) -> dict[str, object] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "run_type": run.run_type,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "jobs_found": run.jobs_found,
        "jobs_applied": run.jobs_applied,
        "errors_json": run.errors_json,
    }
