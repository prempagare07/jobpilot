from __future__ import annotations

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.agents.qa_engine import QAEngine
from backend.config import PROJECT_ROOT, settings
from backend.services.apply_common import (
    AuditLog,
    ApplyResult,
    close_browser,
    load_cookies,
    new_stealth_page,
    save_cookies,
    screenshots_path,
    visible_text,
)
from backend.services.form_filler import FormFiller


class LinkedInApplyService:
    def __init__(self, form_filler: FormFiller | None = None) -> None:
        self.form_filler = form_filler or FormFiller()
        self.cookie_path = PROJECT_ROOT / "data" / "linkedin_cookies.json"

    async def apply(
        self,
        job: dict,
        profile: dict,
        resume_path: str,
        cover_letter: str,
        qa_engine: QAEngine,
        audit_log: AuditLog | None = None,
    ) -> ApplyResult:
        log = audit_log or AuditLog()
        self.form_filler.audit_log = log
        playwright, context, page = await new_stealth_page(headless=settings.apply_browser_headless)
        try:
            await load_cookies(context, self.cookie_path)
            log.navigate(job["url"])
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)

            if await self._session_expired(page):
                log.info("LinkedIn session expired; logging in with configured credentials")
                await self._login(page)
                await save_cookies(context, self.cookie_path)
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)

            easy_apply = page.locator('button[aria-label*="Easy Apply"]').first
            await easy_apply.click(timeout=10000)
            log.step("Clicked LinkedIn Easy Apply")

            profile_context = {
                **profile,
                "resume_path": resume_path,
                "cover_letter_text": cover_letter,
                "job": job,
            }
            questions_encountered: list[str] = []
            questions_needing_human: list[str] = []
            screenshot_path: str | None = None

            for _ in range(12):
                await page.wait_for_timeout(1000)
                modal = page.locator('[role="dialog"], .jobs-easy-apply-modal').first
                step_text = await self._current_step_text(page)
                if step_text:
                    questions_encountered.append(step_text)
                    log.info(f"LinkedIn step: {step_text}")

                if self._is_eeo_step(step_text) and not self._has_eeo_data(profile_context):
                    if await self._click_next(page):
                        continue

                filled = await self.form_filler.detect_and_fill_form(
                    page,
                    profile_context,
                    qa_engine,
                )
                questions_needing_human.extend(filled.questions_needing_human)
                await self._fill_cover_letter_text(page, cover_letter)

                if questions_needing_human:
                    screenshot_path = await self._screenshot(page, job, "needs_human")
                    return ApplyResult(
                        success=False,
                        screenshot_path=screenshot_path,
                        questions_encountered=dedupe(questions_encountered),
                        questions_needing_human=dedupe(questions_needing_human),
                        error="Application needs human answers before submit.",
                        reason="needs_human",
                        url=page.url,
                    )

                submit = modal.locator('button[aria-label="Submit application"]').first
                if await submit.count() and await submit.is_enabled():
                    screenshot_path = await self._screenshot(page, job, "preflight")
                    if settings.apply_require_human_review:
                        return await self._wait_for_manual_review(
                            page=page,
                            screenshot_path=screenshot_path,
                            questions_encountered=questions_encountered,
                            log=log,
                        )
                    log.submit(page.url)
                    await submit.click()
                    submitted = await self._confirm_success(page)
                    return ApplyResult(
                        success=submitted,
                        screenshot_path=screenshot_path,
                        questions_encountered=dedupe(questions_encountered),
                        error=None if submitted else "Submit clicked, but confirmation was not detected.",
                        url=page.url,
                    )

                if await self._click_next(page):
                    continue

                screenshot_path = await self._screenshot(page, job, "stalled")
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    questions_encountered=dedupe(questions_encountered),
                    error="Could not find a Next/Review/Submit action in LinkedIn Easy Apply.",
                    reason="stalled",
                    url=page.url,
                )

            screenshot_path = await self._screenshot(page, job, "max_steps")
            return ApplyResult(
                success=False,
                screenshot_path=screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                error="LinkedIn Easy Apply exceeded the step limit.",
                reason="max_steps",
                url=page.url,
            )
        except Exception as exc:
            screenshot_path = await self._safe_screenshot(page, job, "error")
            log.error(str(exc))
            return ApplyResult(success=False, screenshot_path=screenshot_path, error=str(exc), url=page.url)
        finally:
            await close_browser(playwright, context)

    async def _session_expired(self, page: Page) -> bool:
        url = page.url.lower()
        if "login" in url or "checkpoint" in url:
            return True
        text = (await visible_text(page)).lower()
        return "sign in" in text and "easy apply" not in text

    async def _login(self, page: Page) -> None:
        if not settings.linkedin_email or not settings.linkedin_password:
            raise RuntimeError("LinkedIn session expired and LINKEDIN_EMAIL/LINKEDIN_PASSWORD are not set.")

        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)
        await page.fill("#username", settings.linkedin_email)
        await page.fill("#password", settings.linkedin_password)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=45000)
        if await self._session_expired(page):
            raise RuntimeError("LinkedIn login did not complete. Manual checkpoint or MFA may be required.")

    async def _current_step_text(self, page: Page) -> str:
        for selector in ('[role="dialog"] h3', ".jobs-easy-apply-modal h3", '[role="dialog"] h2'):
            try:
                text = await page.locator(selector).first.inner_text(timeout=1000)
                if text.strip():
                    return text.strip()
            except Exception:
                continue
        return ""

    async def _fill_cover_letter_text(self, page: Page, cover_letter: str) -> None:
        if not cover_letter.strip():
            return
        labels = ("cover letter", "additional information", "message to hiring")
        textareas = await page.query_selector_all("textarea:visible")
        for textarea in textareas:
            label = await self.form_filler.extract_label_text(textarea)
            if any(token in label.lower() for token in labels):
                await self.form_filler.fill_field(page, textarea, cover_letter)

    async def _click_next(self, page: Page) -> bool:
        labels = ("Next", "Review", "Continue")
        for label in labels:
            button = page.get_by_role("button", name=label).last
            try:
                if await button.count() and await button.is_enabled():
                    await button.click()
                    return True
            except Exception:
                continue
        return False

    async def _confirm_success(self, page: Page) -> bool:
        try:
            await page.get_by_text("Application submitted", exact=False).wait_for(timeout=15000)
            return True
        except PlaywrightTimeoutError:
            text = (await visible_text(page)).lower()
            return "application submitted" in text or "your application was sent" in text

    async def _wait_for_manual_review(
        self,
        page: Page,
        screenshot_path: str | None,
        questions_encountered: list[str],
        log: AuditLog,
    ) -> ApplyResult:
        timeout_s = settings.application_review_timeout_seconds
        log.set_status("waiting_review")
        log.info(
            "LinkedIn form is pre-filled. Review or edit the visible browser, "
            f"then click Submit application within {timeout_s} seconds."
        )
        success = await self._confirm_success_after_human_submit(page, timeout_s)
        post_submit_screenshot = await self._safe_screenshot(page, {"id": "linkedin_application"}, "post_review")
        if success:
            log.submit(page.url)
            log.info("Human submitted the application manually — confirmed via success page detection.")
            log.set_status("applied")
            return ApplyResult(
                success=True,
                screenshot_path=post_submit_screenshot or screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                ats_platform="linkedin",
                url=page.url,
            )
        message = "Timed out waiting for manual review submit. Use Resume Application to try again."
        log.error(message)
        return ApplyResult(
            success=False,
            screenshot_path=post_submit_screenshot or screenshot_path,
            questions_encountered=dedupe(questions_encountered),
            questions_needing_human=[message],
            reason="needs_human",
            ats_platform="linkedin",
            error=message,
            url=page.url,
        )

    async def _confirm_success_after_human_submit(self, page: Page, timeout_s: int) -> bool:
        try:
            await page.get_by_text("Application submitted", exact=False).wait_for(timeout=timeout_s * 1000)
            return True
        except PlaywrightTimeoutError:
            text = (await visible_text(page)).lower()
            return "application submitted" in text or "your application was sent" in text

    def _is_eeo_step(self, step_text: str) -> bool:
        normalized = step_text.lower()
        return any(token in normalized for token in ("voluntary", "self-identification", "self identification", "eeo"))

    def _has_eeo_data(self, profile: dict) -> bool:
        eeo = profile.get("eeo_json") or profile.get("eeo") or {}
        if isinstance(eeo, dict) and any(eeo.values()):
            return True
        return any(
            profile.get(key)
            for key in ("gender", "race_ethnicity", "veteran_status", "disability_status")
        )

    async def _screenshot(self, page: Page, job: dict, suffix: str) -> str:
        job_id = str(job.get("id") or "linkedin_application")
        path = screenshots_path(f"{safe_file_name(job_id)}_{suffix}.png")
        await page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def _safe_screenshot(self, page: Page, job: dict, suffix: str) -> str | None:
        try:
            return await self._screenshot(page, job, suffix)
        except Exception:
            return None


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def safe_file_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
