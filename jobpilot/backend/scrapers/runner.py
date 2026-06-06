from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.ats_scorer import ATSResult, ATSScorer
from backend.agents.job_classifier import JobClassifier
from backend.config import PROJECT_ROOT
from backend.db.models import Job, Profile, ResumeVersion, SchedulerRun
from backend.scrapers.base import BaseScraper, RawJob
from backend.scrapers.dedup import DedupEngine
from backend.scrapers.indeed import IndeedScraper
from backend.scrapers.jobright import JobrightScraper
from backend.scrapers.linkedin import LinkedInScraper
from backend.scrapers.monster import MonsterScraper
from backend.scrapers.normalizer import NormalizedJob, normalize
from backend.scrapers.simplify import SimplifyScraper
from backend.services.database import SessionLocal

DEFAULT_QUERIES = ["AI Engineer", "Machine Learning Engineer", "Software Engineer", "Software Development Engineer"]
DEFAULT_LOCATION = "United States"
MAX_JOBS_PER_QUERY_PER_SCRAPER = 25


def build_scrapers() -> list[BaseScraper]:
    return [
        LinkedInScraper(),
        JobrightScraper(),
        IndeedScraper(),
        MonsterScraper(),
        SimplifyScraper(),
    ]


async def run_scrape_cycle(db_session: Session, queries: list[str] | None = None) -> dict[str, Any]:
    active_queries = queries or DEFAULT_QUERIES
    started_at = datetime.utcnow()
    run = SchedulerRun(run_type="scrape", started_at=started_at, jobs_found=0, jobs_applied=0, errors_json=[])
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    errors: list[str] = []
    saved_count = 0
    skipped_count = 0
    saved_ids: set[str] = set()
    profile = db_session.scalar(select(Profile).order_by(Profile.id.asc()))
    classifier = JobClassifier(applicant_years_experience=profile.years_experience if profile else 0)
    ats_scorer = ATSScorer()
    resume_versions = active_resume_versions(db_session)

    print(f"[scrape-cycle] started_at={started_at.isoformat()}Z queries={active_queries}")
    for scraper in build_scrapers():
        scraper_raw_count = 0
        scraper_saved_count = 0
        try:
            for query in active_queries:
                raw_jobs = await scraper.scrape(
                    query=query,
                    location=DEFAULT_LOCATION,
                    max_jobs=MAX_JOBS_PER_QUERY_PER_SCRAPER,
                )
                scraper_raw_count += len(raw_jobs)
                normalized_jobs = normalize_jobs(raw_jobs, scraper.platform)
                relevant_jobs = await filter_relevant_jobs(classifier, normalized_jobs, errors)
                deduped_jobs = await DedupEngine(db_session).filter_new(
                    [job for job in relevant_jobs if job.id not in saved_ids]
                )
                for job in deduped_jobs:
                    ats_result: ATSResult | None = None
                    resume_version_id: int | None = None
                    if resume_versions:
                        ats_result, resume_version_id = await score_job(job, resume_versions, ats_scorer, errors)
                    save_job(db_session, job, ats_result, resume_version_id)
                    saved_ids.add(job.id)
                    saved_count += 1
                    scraper_saved_count += 1
                db_session.commit()
                skipped_count += len(normalized_jobs) - len(relevant_jobs)
            print(
                f"[scrape-cycle] platform={scraper.platform} raw={scraper_raw_count} "
                f"saved={scraper_saved_count}"
            )
        except Exception as exc:
            error = f"{scraper.platform}: {exc}"
            print(f"[scrape-cycle] error={error}")
            errors.append(error)
            db_session.rollback()
            continue

    run.finished_at = datetime.utcnow()
    run.jobs_found = saved_count
    run.errors_json = errors
    db_session.commit()
    print(
        f"[scrape-cycle] finished_at={run.finished_at.isoformat()}Z saved={saved_count} "
        f"skipped={skipped_count} errors={len(errors)}"
    )
    return {"run_id": run.id, "jobs_found": saved_count, "skipped": skipped_count, "errors": errors}


def normalize_jobs(raw_jobs: list[RawJob], platform: str) -> list[NormalizedJob]:
    normalized: list[NormalizedJob] = []
    for raw in raw_jobs:
        try:
            if not raw.url:
                continue
            normalized.append(normalize(raw, platform))
        except Exception as exc:
            print(f"[scrape-cycle] normalize_error platform={platform} error={exc}")
    return normalized


async def filter_relevant_jobs(
    classifier: JobClassifier,
    jobs: list[NormalizedJob],
    errors: list[str],
) -> list[NormalizedJob]:
    relevant: list[NormalizedJob] = []
    for job in jobs:
        try:
            classification = await classifier.classify(job.title, job.job_description)
            if classification.is_relevant and classification.seniority != "skip":
                relevant.append(job)
            else:
                print(f"[scrape-cycle] skipped job_id={job.id} reason={classification.skip_reason}")
        except Exception as exc:
            errors.append(f"classifier:{job.id}:{exc}")
            relevant.append(job)
    return relevant


async def score_job(
    job: NormalizedJob,
    resume_versions: list[dict[str, Any]],
    ats_scorer: ATSScorer,
    errors: list[str],
) -> tuple[ATSResult | None, int | None]:
    try:
        best_resume_name, ats_result = await ats_scorer.pick_best_resume(job.job_description, resume_versions)
        resume_version_id = next(
            (resume["id"] for resume in resume_versions if resume["name"] == best_resume_name),
            None,
        )
        return ats_result, resume_version_id
    except Exception as exc:
        errors.append(f"ats:{job.id}:{exc}")
        return None, None


def save_job(
    db_session: Session,
    job: NormalizedJob,
    ats_result: ATSResult | None,
    resume_version_id: int | None,
) -> None:
    existing = db_session.get(Job, job.id)
    if existing:
        return
    db_session.add(
        Job(
            id=job.id,
            title=job.title,
            company=job.company,
            location=job.location,
            job_description=job.job_description,
            url=job.url,
            platform=job.platform,
            date_posted=job.date_posted,
            easy_apply=job.easy_apply,
            scraped_at=job.scraped_at,
            status=job.status,
            resume_version_id=resume_version_id,
            ats_score=float(ats_result.score) if ats_result else None,
            notes=render_ats_notes(ats_result),
        )
    )


def render_ats_notes(ats_result: ATSResult | None) -> str | None:
    if ats_result is None:
        return None
    return str(
        {
            "recommendation": ats_result.recommendation,
            "matched_keywords": ats_result.matched_keywords,
            "missing_keywords": ats_result.missing_keywords,
            "tailoring_suggestions": ats_result.tailoring_suggestions,
        }
    )


def active_resume_versions(db_session: Session) -> list[dict[str, Any]]:
    versions = list(db_session.scalars(select(ResumeVersion).where(ResumeVersion.is_active.is_(True))))
    resumes: list[dict[str, Any]] = []
    for version in versions:
        resume_text = read_resume_text(version.file_path)
        if not resume_text.strip():
            continue
        resume = asdict(
            ResumePayload(
                id=version.id,
                name=version.name,
                resume_name=version.name,
                resume_text=resume_text,
            )
        )
        resumes.append(resume)
    return resumes


@dataclass(frozen=True)
class ResumePayload:
    id: int
    name: str
    resume_name: str
    resume_text: str


def read_resume_text(file_path: str) -> str:
    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


async def run_scrape() -> dict[str, Any]:
    with SessionLocal() as db_session:
        return await run_scrape_cycle(db_session)


def main() -> None:
    result = asyncio.run(run_scrape())
    print(result)


if __name__ == "__main__":
    main()
