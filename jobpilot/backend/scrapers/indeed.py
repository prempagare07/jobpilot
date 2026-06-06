from __future__ import annotations

from backend.scrapers.base import BaseScraper, RawJob


class IndeedScraper(BaseScraper):
    platform = "indeed"

    async def scrape(self, query: str, location: str = "United States", max_jobs: int = 25) -> list[RawJob]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            self.log_request("jobspy not installed - run: pip install python-jobspy")
            return []

        self.log_request(f"https://indeed.com [jobspy] query={query}")
        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=query,
                location=location,
                results_wanted=max_jobs,
                hours_old=72,
                country_indeed="USA",
            )
        except Exception as exc:
            print(f"[indeed] jobspy error: {exc}")
            return []

        jobs: list[RawJob] = []
        for _, row in df.iterrows():
            title = str(row.get("title") or "")
            company = str(row.get("company") or "Unknown")
            if not title:
                continue
            jobs.append(
                RawJob(
                    title=self.clean_text(title),
                    company=self.clean_text(company),
                    location=self.clean_text(str(row.get("location") or location)),
                    description=self.clean_text(str(row.get("description") or "")),
                    url=str(row.get("job_url") or ""),
                    platform=self.platform,
                    date_posted=str(row.get("date_posted") or ""),
                    easy_apply=bool(row.get("is_remote", False)),
                )
            )
        return jobs[:max_jobs]
