from __future__ import annotations

from fastapi import APIRouter

from backend.scheduler import scheduler
from backend.scrapers.runner import run_scrape

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get("/status")
def scheduler_status() -> dict[str, object]:
    return {
        "running": scheduler.running,
        "jobs": [{"id": job.id, "next_run_time": str(job.next_run_time)} for job in scheduler.get_jobs()],
    }


@router.post("/scrape")
async def scrape_now() -> dict[str, object]:
    return await run_scrape()
