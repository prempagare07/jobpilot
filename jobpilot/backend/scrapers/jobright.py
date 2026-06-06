from __future__ import annotations

import os

import httpx

from backend.scrapers.base import BaseScraper, RawJob, retry


class TokenExpiredError(RuntimeError):
    pass


class JobrightScraper(BaseScraper):
    platform = "jobright"
    BASE_URL = "https://jobright.ai"
    JOBS_ENDPOINT = "/swan/recommend/list/jobs"

    # Track refresh attempts to avoid infinite loop
    _refresh_attempted: bool = False

    def _get_credentials(self) -> tuple[str, str]:
        """Always reads fresh from env so post-refresh values are picked up."""
        try:
            from backend.config import get_settings
            s = get_settings()
            session_id = s.jobright_session_id or s.jobright_session_token or ""
            cookie = s.jobright_cookie or ""
        except Exception:
            session_id = ""
            cookie = ""

        # os.environ is updated by refresh_session() in the same process
        if not session_id:
            session_id = os.environ.get("JOBRIGHT_SESSION_ID", "")
        if not cookie:
            cookie = os.environ.get("JOBRIGHT_COOKIE", "")

        return session_id, cookie

    def _auth_headers(self) -> dict:
        session_id, cookie = self._get_credentials()
        if session_id and "SESSION_ID" not in cookie:
            cookie = f"SESSION_ID={session_id}; {cookie}".strip("; ")
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cookie": cookie,
            "referer": "https://jobright.ai/jobs/recommend",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            "x-client-type": "web",
        }

    async def _try_auto_refresh(self) -> bool:
        """Attempt one session refresh via Playwright login."""
        if self._refresh_attempted:
            print("[jobright] Already attempted refresh this session — skipping")
            return False
        self._refresh_attempted = True
        print("[jobright] Attempting auto-refresh via Playwright login...")
        try:
            from backend.scrapers.jobright_auth import refresh_session_headless_only
            success = await refresh_session_headless_only()
            if success:
                print("[jobright] Auto-refresh succeeded — retrying scrape")
            return success
        except Exception as e:
            print(f"[jobright] Auto-refresh failed: {e}")
            return False

    @retry(max_attempts=1, backoff=3.0)
    async def scrape(
        self, query: str, location: str = "United States", max_jobs: int = 25
    ) -> list[RawJob]:
        session_id, cookie = self._get_credentials()

        if not session_id and not cookie:
            print("[jobright] No credentials — attempting auto-refresh first...")
            refreshed = await self._try_auto_refresh()
            if not refreshed:
                print("[jobright] Skipping — set JOBRIGHT_EMAIL + JOBRIGHT_PASSWORD in .env")
                return []
            session_id, cookie = self._get_credentials()

        jobs: list[RawJob] = []
        page_size = min(10, max_jobs)
        position = 0

        while len(jobs) < max_jobs:
            url = (
                f"{self.BASE_URL}{self.JOBS_ENDPOINT}"
                f"?refresh=false&sortCondition=0"
                f"&position={position}&count={page_size}&syncRerank=false"
            )
            self.log_request(url)

            async with httpx.AsyncClient(
                headers=self._auth_headers(),
                timeout=30.0,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)

            # Session expired — auto-refresh and retry once
            if response.status_code == 401:
                print("[jobright] Got 401 — session expired")
                refreshed = await self._try_auto_refresh()
                if refreshed:
                    # Retry the same request with fresh credentials
                    async with httpx.AsyncClient(
                        headers=self._auth_headers(),
                        timeout=30.0,
                        follow_redirects=True,
                    ) as client:
                        response = await client.get(url)
                    if response.status_code == 401:
                        print("[jobright] Still 401 after refresh — credentials may be wrong")
                        break
                else:
                    print("[jobright] Could not refresh — stopping scrape")
                    break

            if response.status_code == 403:
                print("[jobright] 403 Forbidden — account may be flagged")
                break

            response.raise_for_status()

            try:
                data = response.json()
            except Exception:
                print(f"[jobright] JSON parse failed: {response.text[:200]}")
                break

            result = data.get("result") or {}
            raw_list = result.get("jobList") or []

            if not raw_list:
                print(f"[jobright] Empty jobList at position={position}")
                break

            for raw in raw_list:
                job = dict(raw.get("jobResult") or {})
                company_info = dict(raw.get("companyResult") or {})

                title = str(job.get("jobTitle") or job.get("jobNlpTitle") or "")
                if not title:
                    continue

                company = str(
                    company_info.get("companyName")
                    or company_info.get("name")
                    or job.get("company")
                    or "Unknown"
                )

                job_id = str(job.get("jobId") or "")
                job_url = (
                    job.get("applyLink")
                    or job.get("originalUrl")
                    or (f"https://jobright.ai/jobs/info/{job_id}" if job_id else "")
                )

                # JobRight API only returns a short summary — fetch the full JD from the source URL.
                summary = str(job.get("jobSummary") or "")
                core = job.get("coreResponsibilities")
                if isinstance(core, list):
                    summary += " " + " ".join(str(c) for c in core)

                full_jd: str | None = None
                if job_url:
                    try:
                        from backend.services.jd_fetcher import fetch_full_jd
                        full_jd = await fetch_full_jd(job_url, timeout=20.0)
                        if full_jd:
                            print(f"[jobright] fetched full JD ({len(full_jd)} chars) for {title} at {company}")
                    except Exception as exc:
                        print(f"[jobright] JD fetch failed for {job_url}: {exc}")

                description = full_jd or self.clean_text(summary)

                jobs.append(
                    RawJob(
                        title=self.clean_text(title),
                        company=self.clean_text(company),
                        location=self.clean_text(str(job.get("jobLocation") or location)),
                        description=description,
                        url=str(job_url),
                        platform=self.platform,
                        date_posted=str(job.get("publishTime") or job.get("publishTimeDesc") or ""),
                        easy_apply=bool(job.get("jobtargetEasyapply")),
                    )
                )

            position += page_size
            if position >= max_jobs * 3:
                break
            await self.wait()

        # Reset refresh flag for next call
        self._refresh_attempted = False
        return jobs[:max_jobs]
