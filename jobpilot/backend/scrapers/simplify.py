from __future__ import annotations

from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from backend.scrapers.base import BaseScraper, RawJob, retry

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False


class SimplifyScraper(BaseScraper):
    platform = "simplify"

    @retry(max_attempts=2, backoff=3.0)
    async def scrape(self, query: str, location: str = "United States", max_jobs: int = 25) -> list[RawJob]:
        url = (
            f"https://simplify.jobs/jobs"
            f"?search={quote_plus(query)}"
            f"&location={quote_plus(location)}"
            f"&remote=true"
        )
        self.log_request(url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            if HAS_STEALTH:
                await stealth_async(page)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Scroll to load more cards
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1000)

            content = await page.content()
            await browser.close()

        soup = BeautifulSoup(content, "html.parser")
        cards = soup.select('[data-testid="job-card"]')
        jobs: list[RawJob] = []

        for card in cards[:max_jobs]:
            # Title: h3 inside card
            title_el = card.select_one("h3")
            if not title_el:
                continue
            title = self.clean_text(title_el.get_text(" "))
            if not title:
                continue

            # Company: span.text-left (first one = company name)
            company_el = card.select_one("span.text-left")
            company = self.clean_text(company_el.get_text()) if company_el else "Unknown"

            # Location: p.text-left
            location_el = card.select_one("p.text-left")
            job_location = self.clean_text(location_el.get_text()) if location_el else location

            # URL: check parent/grandparent for <a href>
            url_found = ""
            node = card
            for _ in range(4):  # walk up 4 levels
                node = node.parent
                if not node:
                    break
                href = node.get("href", "")
                if href and "/jobs/" in str(href):
                    url_found = href if href.startswith("http") else f"https://simplify.jobs{href}"
                    break

            # Fallback URL: construct from search
            if not url_found:
                url_found = f"https://simplify.jobs/jobs?search={quote_plus(title)}"

            jobs.append(
                RawJob(
                    title=title,
                    company=company,
                    location=job_location,
                    description=self.clean_text(card.get_text(" ")),
                    url=url_found,
                    platform=self.platform,
                    date_posted="",
                    easy_apply=True,  # Simplify is all Easy Apply
                )
            )

        return jobs
