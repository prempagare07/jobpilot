from __future__ import annotations

from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from backend.scrapers.base import BaseScraper, RawJob, retry


class MonsterScraper(BaseScraper):
    platform = "monster"

    async def scrape(self, query: str, location: str = "United States", max_jobs: int = 25) -> list[RawJob]:
        # RSS endpoint is dead (404) — go straight to HTML
        return await self._scrape_html(query=query, max_jobs=max_jobs)

    @retry(max_attempts=3, backoff=2.0)
    async def _scrape_html(self, query: str, max_jobs: int) -> list[RawJob]:
        url = (
            f"https://www.monster.com/jobs/search"
            f"?q={quote_plus(query)}&where=United+States&sort=date_desc"
        )
        self.log_request(url)
        headers = {
            **self.headers(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        jobs: list[RawJob] = []

        # Monster uses several card layouts — try all known selectors
        cards = (
            soup.select("article.job-search-resultcard")
            or soup.select("[data-testid='jobCard']")
            or soup.select("[data-testid='svx_jobCard']")
            or soup.select("div.job-cardstyle__JobCardComponent")
            or soup.select("article")
        )

        for card in cards[: max_jobs * 2]:
            title_el = (
                card.select_one("h2 a")
                or card.select_one("h3 a")
                or card.select_one("[data-testid='jobTitle']")
                or card.select_one("h2")
                or card.select_one("h3")
            )
            company_el = (
                card.select_one("[data-testid='company']")
                or card.select_one(".company")
                or card.select_one("span.name")
            )
            location_el = (
                card.select_one("[data-testid='jobLocation']")
                or card.select_one(".location")
                or card.select_one("span.location")
            )
            link_el = card.select_one("a[href]")
            date_el = card.select_one("time") or card.select_one("[data-testid='datePosted']")

            if not title_el or not link_el:
                continue

            href = str(link_el.get("href", ""))
            if href and not href.startswith("http"):
                href = f"https://www.monster.com{href}"
            if not href:
                continue

            jobs.append(
                RawJob(
                    title=self.clean_text(title_el.get_text(" ")),
                    company=self.clean_text(company_el.get_text(" ") if company_el else "Unknown"),
                    location=self.clean_text(location_el.get_text(" ") if location_el else "United States"),
                    description=self.clean_text(card.get_text(" ")),
                    url=href,
                    platform=self.platform,
                    date_posted=self.clean_text(date_el.get_text(" ") if date_el else ""),
                    easy_apply=False,
                )
            )
            if len(jobs) >= max_jobs:
                break

        return jobs
