from __future__ import annotations

from backend.agents.qa_engine import QAEngine
from backend.config import settings
from backend.services.apply_common import AuditLog, ApplyResult, close_browser, new_stealth_page, screenshots_path, visible_text
from backend.services.form_filler import FormFiller
from backend.services.linkedin_apply import dedupe, safe_file_name


class IndeedApplyService:
    def __init__(self, form_filler: FormFiller | None = None) -> None:
        self.form_filler = form_filler or FormFiller()

    async def apply(
        self,
        job: dict,
        profile: dict,
        resume_path: str,
        qa_engine: QAEngine,
        audit_log: AuditLog | None = None,
    ) -> ApplyResult:
        log = audit_log or AuditLog()
        self.form_filler.audit_log = log
        playwright, context, page = await new_stealth_page(headless=settings.apply_browser_headless)
        try:
            log.navigate(job["url"])
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)

            if await self._is_external_redirect(page):
                external_url = await self._follow_external_apply(page) or page.url
                log.info(f"Indeed redirected to external application site: {external_url}")
                return ApplyResult(
                    success=False,
                    reason="external_redirect",
                    error="Indeed redirected to an external application site.",
                    url=external_url,
                )

            profile_context = {**profile, "resume_path": resume_path, "job": job}
            questions_encountered: list[str] = []
            questions_needing_human: list[str] = []

            for _ in range(8):
                log.info("Indeed form step")
                questions_encountered.extend(await self._visible_labels(page))
                filled = await self.form_filler.detect_and_fill_form(page, profile_context, qa_engine)
                questions_needing_human.extend(filled.questions_needing_human)
                if questions_needing_human:
                    screenshot_path = await self._screenshot(page, job, "needs_human")
                    return ApplyResult(
                        success=False,
                        screenshot_path=screenshot_path,
                        questions_encountered=dedupe(questions_encountered),
                        questions_needing_human=dedupe(questions_needing_human),
                        reason="needs_human",
                        error="Indeed application needs human answers before submit.",
                        url=page.url,
                    )

                submit = page.get_by_role("button", name="Submit").last
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
                    success = await self._confirm_success(page)
                    return ApplyResult(
                        success=success,
                        screenshot_path=screenshot_path,
                        questions_encountered=dedupe(questions_encountered),
                        error=None if success else "Submit clicked, but confirmation was not detected.",
                        url=page.url,
                    )

                if not await self._click_continue(page):
                    break

            screenshot_path = await self._screenshot(page, job, "stalled")
            return ApplyResult(
                success=False,
                screenshot_path=screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                reason="stalled",
                error="Indeed application did not expose a submit or continue step.",
                url=page.url,
            )
        except Exception as exc:
            screenshot_path = await self._safe_screenshot(page, job, "error")
            log.error(str(exc))
            return ApplyResult(success=False, screenshot_path=screenshot_path, error=str(exc), url=page.url)
        finally:
            await close_browser(playwright, context)

    async def _is_external_redirect(self, page) -> bool:
        url = page.url.lower()
        if "indeed.com" not in url:
            return True
        text = (await visible_text(page)).lower()
        external_markers = (
            "apply on company site",
            "continue to application",
            "you are being redirected",
            "apply on employer site",
        )
        return any(marker in text for marker in external_markers)

    async def _follow_external_apply(self, page) -> str | None:
        selectors = (
            'a:has-text("Apply on company site")',
            'button:has-text("Apply on company site")',
            'a:has-text("Apply on employer site")',
            'button:has-text("Apply on employer site")',
            'a:has-text("Continue to application")',
            'button:has-text("Continue to application")',
            'a:has-text("Apply now")',
            'button:has-text("Apply now")',
        )
        for selector in selectors:
            locator = page.locator(selector).last
            try:
                if await locator.count() and await locator.is_enabled():
                    old_url = page.url
                    log_message = f"Clicking Indeed external apply control: {selector}"
                    await locator.click()
                    await page.wait_for_timeout(2500)
                    if page.url != old_url:
                        return page.url
            except Exception:
                continue
        return page.url

    async def _click_continue(self, page) -> bool:
        for label in ("Continue", "Next", "Review", "Apply now"):
            button = page.get_by_role("button", name=label).last
            try:
                if await button.count() and await button.is_enabled():
                    await button.click()
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                continue
        return False

    async def _confirm_success(self, page) -> bool:
        await page.wait_for_timeout(2000)
        text = (await visible_text(page)).lower()
        return "application submitted" in text or "your application has been submitted" in text

    async def _visible_labels(self, page) -> list[str]:
        labels: list[str] = []
        for selector in ("label:visible", "legend:visible", "[role=group]:visible"):
            for element in await page.query_selector_all(selector):
                try:
                    text = (await element.inner_text()).strip()
                    if text:
                        labels.append(text)
                except Exception:
                    continue
        return labels

    async def _wait_for_manual_review(
        self,
        page,
        screenshot_path: str | None,
        questions_encountered: list[str],
        log: AuditLog,
    ) -> ApplyResult:
        timeout_s = settings.application_review_timeout_seconds
        log.set_status("waiting_review")
        log.info(
            "Indeed form is pre-filled. Review or edit the visible browser, "
            f"then click Submit within {timeout_s} seconds."
        )
        success = await self._wait_for_human_submit(page, timeout_s)
        post_submit_screenshot = await self._safe_screenshot(page, {"id": "indeed_application"}, "post_review")
        if success:
            log.submit(page.url)
            log.info("Human submitted the application manually — confirmed via success page detection.")
            log.set_status("applied")
            return ApplyResult(
                success=True,
                screenshot_path=post_submit_screenshot or screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                ats_platform="indeed",
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
            ats_platform="indeed",
            error=message,
            url=page.url,
        )

    async def _wait_for_human_submit(self, page, timeout_s: int) -> bool:
        try:
            await page.wait_for_function(
                """() => {
                    const text = (document.body ? document.body.innerText : '').toLowerCase();
                    return [
                        'application submitted',
                        'your application has been submitted',
                        'thank you for applying',
                        'application received',
                        'successfully submitted'
                    ].some((phrase) => text.includes(phrase));
                }""",
                timeout=timeout_s * 1000,
            )
            return True
        except Exception:
            return False

    async def _screenshot(self, page, job: dict, suffix: str) -> str:
        job_id = str(job.get("id") or "indeed_application")
        path = screenshots_path(f"{safe_file_name(job_id)}_{suffix}.png")
        await page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def _safe_screenshot(self, page, job: dict, suffix: str) -> str | None:
        try:
            return await self._screenshot(page, job, suffix)
        except Exception:
            return None
