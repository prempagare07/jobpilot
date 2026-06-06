from __future__ import annotations

import asyncio
import os
import re

import httpx

from backend.config import settings
from backend.scrapers.base import BaseScraper, RawJob


class LinkedInException(RuntimeError):
    pass


class ChallengeException(RuntimeError):
    pass


def _extract_job_id(url: str) -> str:
    match = re.search(r"(\d{10,})", url)
    return match.group(1) if match else ""


async def _detect_easy_apply(job_id: str) -> bool:
    """
    Use LinkedIn guest API to detect Easy Apply vs External.
    offsite in tracking = External apply (company career page)
    onsite in tracking  = LinkedIn Easy Apply
    """
    if not job_id:
        return False

    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://www.linkedin.com/",
    }

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=10.0, follow_redirects=True
        ) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return False

        html = r.text

        # LinkedIn embeds the apply type directly in impression/tracking IDs
        # offsite = external company apply page
        # onsite  = LinkedIn Easy Apply (stays on LinkedIn)
        if "apply-link-offsite" in html:
            return False
        if "apply-link-onsite" in html or "easy-apply" in html.lower():
            return True

        # SVG icon class is another reliable signal
        if "offsite-apply-icon" in html:
            return False
        if "easy-apply-icon" in html:
            return True

        return False  # default to external

    except Exception:
        return False


class LinkedInScraper(BaseScraper):
    platform = "linkedin"

    def __init__(self) -> None:
        if settings.linkedin_email:
            os.environ.setdefault("LINKEDIN_EMAIL", settings.linkedin_email)
        if settings.linkedin_password:
            os.environ.setdefault("LINKEDIN_PASSWORD", settings.linkedin_password)

    async def scrape(
        self, query: str, location: str = "United States", max_jobs: int = 50
    ) -> list[RawJob]:
        # Primary: jobspy
        jobs = await self._scrape_jobspy(query, location, max_jobs)
        if jobs:
            return jobs

        # Fallback: linkedin-jobs-scraper library
        print("[linkedin] jobspy returned 0 — trying library fallback")
        return await self._scrape_library(query, location, max_jobs)

    async def _scrape_jobspy(
        self, query: str, location: str, max_jobs: int
    ) -> list[RawJob]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            return []

        self.log_request(f"https://linkedin.com [jobspy] query={query}")

        try:
            df = await asyncio.to_thread(
                scrape_jobs,
                site_name=["linkedin"],
                search_term=query,
                location=location,
                results_wanted=max_jobs,
                hours_old=168,
                country_indeed="USA",
            )
        except Exception as exc:
            print(f"[linkedin] jobspy error: {exc}")
            return []

        if df is None or len(df) == 0:
            return []

        # Collect raw job data first
        raw = []
        for _, row in df.iterrows():
            title = str(row.get("title") or "")
            if not title:
                continue
            raw.append({
                "title": title,
                "company": str(row.get("company") or "Unknown"),
                "location": str(row.get("location") or location),
                "description": str(row.get("description") or ""),
                "job_url": str(row.get("job_url") or ""),
                "date_posted": str(row.get("date_posted") or ""),
            })

        if not raw:
            return []

        print(f"[linkedin] jobspy found {len(raw)} listings — detecting apply types...")

        # Detect Easy Apply concurrently with rate limiting
        semaphore = asyncio.Semaphore(3)

        async def detect(job: dict) -> RawJob:
            async with semaphore:
                await asyncio.sleep(0.3)
                job_id = _extract_job_id(job["job_url"])
                is_easy = await _detect_easy_apply(job_id)
                return RawJob(
                    title=self.clean_text(job["title"]),
                    company=self.clean_text(job["company"]),
                    location=self.clean_text(job["location"]),
                    description=self.clean_text(job["description"]),
                    url=job["job_url"],
                    platform=self.platform,
                    date_posted=job["date_posted"],
                    easy_apply=is_easy,
                )

        results = await asyncio.gather(*[detect(j) for j in raw[:max_jobs]])
        easy = sum(1 for r in results if r.easy_apply)
        ext = sum(1 for r in results if not r.easy_apply)
        print(f"[linkedin] Easy Apply: {easy} | External: {ext}")
        return list(results)

    async def _scrape_library(
        self, query: str, location: str, max_jobs: int
    ) -> list[RawJob]:
        try:
            return await asyncio.to_thread(
                self._library_sync, query, location, max_jobs
            )
        except Exception as exc:
            print(f"[linkedin] library error: {exc}")
            return []

    def _library_sync(
        self, query: str, location: str, max_jobs: int
    ) -> list[RawJob]:
        from linkedin_jobs_scraper import LinkedinScraper as LibScraper
        from linkedin_jobs_scraper.events import EventData, Events
        from linkedin_jobs_scraper.filters import TimeFilters, TypeFilters
        from linkedin_jobs_scraper.query import Query, QueryFilters, QueryOptions

        collected: list[RawJob] = []

        def on_data(data: EventData) -> None:
            if len(collected) >= max_jobs:
                return
            src = data._asdict()
            job_url = str(src.get("link") or "")
            collected.append(
                RawJob(
                    title=self.clean_text(src.get("title") or ""),
                    company=self.clean_text(src.get("company") or "Unknown"),
                    location=self.clean_text(src.get("place") or location),
                    description=self.clean_text(src.get("description") or ""),
                    url=job_url,
                    platform=self.platform,
                    date_posted=str(src.get("date_text") or ""),
                    easy_apply=False,  # will be enriched separately if needed
                )
            )

        scraper = LibScraper(slow_mo=1.5, headless=True, max_workers=1)
        scraper.on(Events.DATA, on_data)
        scraper.on(Events.ERROR, lambda e: print(f"[linkedin] {e}"))
        scraper.run([
            Query(query, options=QueryOptions(
                locations=[location],
                limit=min(max_jobs, 50),
                apply_link=True,
                skip_promoted_jobs=False,
                filters=QueryFilters(
                    time=TimeFilters.WEEK,
                    type=[TypeFilters.FULL_TIME],
                ),
            ))
        ])
        return collected[:max_jobs]
