from __future__ import annotations

import json
import traceback
from urllib.parse import urlparse

from playwright.async_api import Frame, Page

from backend.agents.qa_engine import QAEngine
from backend.config import settings
from backend.services.apply_common import AuditLog, ApplyResult, close_browser, new_stealth_page, screenshots_path, visible_text
from backend.services.form_filler import FormFiller
from backend.services.greenhouse_api import GreenhouseApiService, parse_greenhouse_url
from backend.services.linkedin_apply import dedupe, safe_file_name


class GenericApplyService:
    def __init__(self, form_filler: FormFiller | None = None) -> None:
        self.form_filler = form_filler or FormFiller()

    async def apply(
        self,
        url: str,
        profile: dict,
        resume_path: str,
        cover_letter_path: str | None,
        qa_engine: QAEngine,
        audit_log: AuditLog | None = None,
    ) -> ApplyResult:
        log = audit_log or AuditLog()
        self.form_filler.audit_log = log
        playwright, context, page = await new_stealth_page(headless=settings.apply_browser_headless)
        try:
            log.navigate(url)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            # Dismiss cookie/consent popups immediately after page load
            await self._dismiss_cookie_consent(page, log)
            source = await page.content()
            ats_type = self.detect_ats_type(page.url, source)
            log.info(f"Detected ATS: {ats_type}")

            if await self._requires_account_or_login(page):
                msg = "This application requires account creation or sign-in before the form can be filled."
                log.error(msg)
                screenshot_path = await self._screenshot(page, page.url, "needs_human")
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    reason="needs_human",
                    ats_platform=ats_type,
                    questions_needing_human=[msg],
                    error="Account creation/sign-in requires human approval.",
                    url=page.url,
                )

            if ats_type == "workday":
                msg = "Workday application detected; manual review required."
                log.error(msg)
                screenshot_path = await self._screenshot(page, page.url, "needs_human")
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    reason="needs_human",
                    ats_platform="workday",
                    questions_needing_human=[msg],
                    error="Workday is marked complex and needs human handling.",
                    url=page.url,
                )

            # Only pass the cover letter path if the form has a visible cover letter upload field.
            # Greenhouse embeds are special: the upload field often lives inside the iframe, so
            # keep the path until the Greenhouse frame itself decides whether to upload it.
            has_cl_field = await self.form_filler.has_cover_letter_field(page)
            if not has_cl_field:
                if ats_type == "greenhouse":
                    log.info("No cover letter field on host page — deferring detection to Greenhouse iframe")
                else:
                    log.info("No cover letter upload field detected — skipping cover letter PDF upload")

            profile_context = {
                **profile,
                "resume_path": resume_path,
                "cover_letter_path": cover_letter_path if (has_cl_field or ats_type == "greenhouse") else None,
                "job": {"url": url},
            }
            questions_encountered: list[str] = []
            questions_needing_human: list[str] = []

            if ats_type == "greenhouse":
                # Use the Greenhouse Job Board API only to pre-seed QA memory with questions.
                # Do NOT use the API to submit — the POST endpoint requires auth on many boards
                # and returns 401. Always use Playwright for the actual form fill + submit.
                gh_ids = parse_greenhouse_url(page.url) or parse_greenhouse_url(url)
                if gh_ids:
                    board_token, job_id = gh_ids
                    log.info(f"Pre-seeding QA memory from Greenhouse API (board={board_token}, job={job_id})")
                    gh_api = GreenhouseApiService()
                    try:
                        await gh_api.seed_qa_memory(
                            board_token=board_token,
                            job_id=job_id,
                            profile=profile_context,
                            qa_engine=qa_engine,
                            log=log,
                        )
                    except Exception as exc:
                        log.info(f"Greenhouse QA pre-seed failed (non-fatal): {exc}")
                    finally:
                        await gh_api.close()

                # Always use Playwright to fill + submit
                if "greenhouse.io" not in page.url:
                    log.info("Greenhouse embed detected on company page — using iframe strategy")
                return await self._apply_greenhouse(
                    page=page,
                    url=url,
                    profile_context=profile_context,
                    qa_engine=qa_engine,
                    questions_encountered=questions_encountered,
                    questions_needing_human=questions_needing_human,
                    log=log,
                )

            if ats_type == "ashby":
                return await self._apply_ashby(
                    page=page,
                    url=url,
                    profile_context=profile_context,
                    qa_engine=qa_engine,
                    log=log,
                )

            filled_specific = await self._fill_known_ats(page, ats_type, profile_context)

            # Ashby uses custom React dropdowns that aren't real <select>/<input> elements.
            if ats_type == "ashby":
                await self._fill_ashby_dropdowns(page, profile_context, log)

            questions_encountered.extend(await self._visible_questions(page))
            filled = await self.form_filler.detect_and_fill_form(page, profile_context, qa_engine)
            questions_needing_human.extend(filled.questions_needing_human)

            for frame in self._application_frames(page):
                questions_encountered.extend(await self._visible_questions(frame))
                frame_result = await self.form_filler.detect_and_fill_form(frame, profile_context, qa_engine)
                questions_needing_human.extend(frame_result.questions_needing_human)

            if questions_needing_human:
                log.info(
                    "Form has unanswered questions — stored in Q&A memory. "
                    "Answer them in the Q&A tab and re-trigger this application."
                )
                screenshot_path = await self._screenshot(page, page.url, "needs_human")
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    reason="needs_human",
                    ats_platform=ats_type,
                    questions_needing_human=dedupe(questions_needing_human),
                    error="Missing answers for: " + "; ".join(dedupe(questions_needing_human)[:3]),
                    url=page.url,
                )

            # Auto-accept any consent/terms checkboxes before taking screenshot or submitting
            await self._auto_accept_consent(page, log)
            for frame in self._application_frames(page):
                await self._auto_accept_consent_frame(frame, log)

            screenshot_path = await self._screenshot(page, page.url, "preflight")
            if settings.apply_require_human_review:
                return await self._wait_for_manual_review(
                    page=page,
                    screenshot_path=screenshot_path,
                    questions_encountered=questions_encountered,
                    ats_platform=ats_type,
                    log=log,
                )

            log.submit(page.url)
            submitted = await self._submit(page)
            if not submitted:
                err = f"No submit button found after filling {filled.fields_filled + filled_specific} fields."
                log.error(err)
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    questions_encountered=dedupe(questions_encountered),
                    reason="stalled",
                    ats_platform=ats_type,
                    error=err,
                    url=page.url,
                )

            # Check for inline validation errors before declaring success — some forms stay
            # on the same page and show field errors when required fields are missing.
            validation_errors = await self._validation_errors(page)
            if validation_errors:
                screenshot_path = await self._safe_screenshot(page, url, "validation_error")
                err = "Form has validation errors — required fields not filled: " + "; ".join(validation_errors)
                log.error(err)
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    questions_encountered=dedupe(questions_encountered),
                    questions_needing_human=validation_errors,
                    reason="needs_human",
                    ats_platform=ats_type,
                    error=err,
                    url=page.url,
                )

            success = await self._confirm_success(page)
            if success:
                log.info("Application submitted successfully.")
                return ApplyResult(
                    success=True,
                    screenshot_path=screenshot_path,
                    questions_encountered=dedupe(questions_encountered),
                    ats_platform=ats_type,
                    url=page.url,
                )

            # CAPTCHA must stay human-in-the-loop. We pause in the visible browser and let
            # the user solve it, edit the form, and submit.
            captcha_type = await self._detect_captcha_type(page)
            if captcha_type:
                return await self._wait_for_captcha_review(
                    page=page,
                    screenshot_path=screenshot_path,
                    questions_encountered=questions_encountered,
                    ats_platform=ats_type,
                    captcha_type=captcha_type,
                    log=log,
                )

            log.error("Submit clicked but confirmation page not detected.")
            return ApplyResult(
                success=False,
                screenshot_path=screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                ats_platform=ats_type,
                error="Submit clicked, but confirmation was not detected.",
                url=page.url,
            )
        except Exception as exc:
            full_error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            log.error(full_error)
            screenshot_path = await self._safe_screenshot(page, url, "error")
            return ApplyResult(success=False, screenshot_path=screenshot_path, error=full_error, url=page.url, ats_platform="unknown")
        finally:
            await close_browser(playwright, context)

    async def _apply_greenhouse_in_iframe(
        self,
        page: Page,
        url: str,
        profile_context: dict,
        qa_engine: QAEngine,
        questions_encountered: list[str],
        questions_needing_human: list[str],
        log: AuditLog,
    ) -> ApplyResult:
        """
        Handle Greenhouse when it's embedded as an iframe on a company page.
        The main window stays on the company page; we interact entirely via the frame.
        """
        # Wait for the Greenhouse iframe to load
        await page.wait_for_timeout(2000)

        gh_frame = None
        for attempt in range(10):
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                if "greenhouse.io" in frame.url or "grnh.se" in frame.url:
                    gh_frame = frame
                    break
            if gh_frame:
                break
            await page.wait_for_timeout(500)

        if not gh_frame:
            err = "Greenhouse iframe not found on company page after 5s"
            log.error(err)
            screenshot = await self._safe_screenshot(page, url, "no_iframe")
            return ApplyResult(success=False, screenshot_path=screenshot,
                               reason="failed", ats_platform="greenhouse",
                               error=err, url=page.url)

        log.info(f"Greenhouse iframe found: {gh_frame.url[:100]}")

        # Dismiss any consent popup inside the frame
        await self._dismiss_cookie_consent_in_frame(gh_frame, log)

        # List visible buttons in the frame for diagnostics
        frame_btns = await gh_frame.evaluate("""
            () => Array.from(document.querySelectorAll('a, button'))
                .filter(e => e.offsetParent !== null)
                .map(e => e.innerText.trim())
                .filter(t => t && t.length < 60)
                .slice(0, 15)
        """)
        log.info(f"Greenhouse iframe buttons: {frame_btns}")

        # Click "Apply for this job" inside the frame
        on_form = await self._is_greenhouse_application_form_frame(gh_frame)
        log.info(f"Application form in iframe: {on_form}")

        if not on_form:
            for apply_sel in (
                'a:has-text("Apply for this job")',
                'button:has-text("Apply for this job")',
                '.application-button',
                'a:has-text("Apply Now")',
                'a:has-text("Apply")',
            ):
                try:
                    btn = gh_frame.locator(apply_sel).first
                    if await btn.count() and await btn.is_visible(timeout=2000):
                        log.info(f"Clicking '{apply_sel}' in iframe")
                        await btn.click(timeout=5000)
                        await page.wait_for_timeout(2500)
                        await self._dismiss_cookie_consent_in_frame(gh_frame, log)
                        log.info(f"Frame URL after Apply click: {gh_frame.url[:100]}")
                        break
                except Exception as e:
                    log.info(f"Frame selector '{apply_sel}' failed: {str(e)[:60]}")

            on_form = await self._is_greenhouse_application_form_frame(gh_frame)
            log.info(f"Application form in iframe after Apply click: {on_form}")

        # Check for cover letter field IN THE IFRAME (including hidden file inputs)
        has_cl_in_frame = await self._greenhouse_has_cover_letter_field(gh_frame)
        if has_cl_in_frame:
            log.info("Cover letter field found in Greenhouse iframe — will upload PDF")
        else:
            log.info("No cover letter field in Greenhouse iframe — skipping CL upload")
            profile_context = {**profile_context, "cover_letter_path": None}

        # Fill the form within the frame
        log.info("Filling Greenhouse application form in iframe")

        # Dismiss iframe consent before filling
        await self._dismiss_cookie_consent_in_frame(gh_frame, log)

        # Known Greenhouse field selectors — include country/location
        country_val = (
            profile_context.get("address_country")
            or self.form_filler.value_from_profile("country", profile_context)
            or "United States"
        )
        gh_fields: dict[str, str | None] = {
            "#first_name":                       self.form_filler.value_from_profile("first name", profile_context),
            "#last_name":                        self.form_filler.value_from_profile("last name", profile_context),
            "#email":                            self.form_filler.value_from_profile("email", profile_context),
            "#phone":                            self.form_filler.value_from_profile("phone", profile_context),
            "#job_application_location":         self.form_filler.value_from_profile("location", profile_context),
            "input[name*='linkedin']":           self.form_filler.value_from_profile("linkedin", profile_context),
            "input[name*='website'], input[name*='portfolio']": self.form_filler.value_from_profile("website", profile_context),
        }
        for selector, value in gh_fields.items():
            if not value:
                continue
            try:
                el = await gh_frame.query_selector(selector)
                if el:
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    if tag == "select":
                        await el.select_option(label=str(value))
                    else:
                        await el.fill(str(value))
                    log.fill(selector, str(value), "greenhouse-known-field")
            except Exception:
                continue

        # Handle Country dropdown — Greenhouse uses a <select id="job_application_country">
        # or a React-select combobox
        await self._fill_greenhouse_country(gh_frame, country_val, log)

        # Upload cover letter via hidden file input (Greenhouse hides the input behind the Attach button)
        if profile_context.get("cover_letter_path"):
            await self._greenhouse_upload_hidden_file(
                gh_frame, profile_context["cover_letter_path"],
                label_keyword="cover", log=log
            )

        # Wait for any React re-render after country selection before running general filler
        await gh_frame.evaluate("() => new Promise(r => setTimeout(r, 500))")

        # Let the general form filler handle remaining visible fields (also uploads resume)
        self.form_filler.audit_log = log
        filled = await self.form_filler.detect_and_fill_form(gh_frame, profile_context, qa_engine)
        questions_needing_human.extend(filled.questions_needing_human)
        questions_encountered.extend(await self._visible_questions(gh_frame))
        log.info(f"Fields filled: {filled.fields_filled}, needs_human: {len(filled.questions_needing_human)}")

        # Fill LinkedIn and Website AFTER the resume upload + autofill re-render.
        # Greenhouse's autofill (triggered by resume upload) re-renders the form and
        # wipes optional fields like LinkedIn/Website. Wait for any parsing indicator
        # to clear, then use React-aware setters so the values stick.
        await gh_frame.wait_for_timeout(3000)
        try:
            await gh_frame.wait_for_function(
                """() => {
                    const t = (document.body ? document.body.innerText : '').toLowerCase();
                    return !t.includes('parsing') && !t.includes('processing') && !t.includes('autofilling');
                }""",
                timeout=15000,
            )
        except Exception:
            pass

        linkedin_url = profile_context.get("linkedin_url") or ""
        website_url = profile_context.get("portfolio_url") or profile_context.get("github_url") or ""

        async def _react_fill(frame, selector: str, value: str, label: str) -> bool:
            """Fill a React-controlled input using the native value setter so onChange fires."""
            try:
                el = await frame.query_selector(selector)
                if not el:
                    return False
                await el.evaluate("""(el, v) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
                        'value'
                    )?.set;
                    if (setter) setter.call(el, v); else el.value = v;
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
                }""", value)
                log.fill(label, value, "greenhouse-react-fill")
                return True
            except Exception:
                return False

        if linkedin_url:
            filled_li = await _react_fill(
                gh_frame,
                'input[name*="linkedin" i], input[id*="linkedin" i], input[placeholder*="linkedin" i]',
                linkedin_url, "LinkedIn Profile",
            )
            if not filled_li:
                await self._greenhouse_fill_text_by_label(gh_frame, ("linkedin",), linkedin_url, "LinkedIn Profile", log)

        if website_url:
            filled_ws = await _react_fill(
                gh_frame,
                'input[name*="website" i], input[id*="website" i], input[placeholder*="website" i], input[name*="portfolio" i], input[id*="portfolio" i]',
                website_url, "Website",
            )
            if not filled_ws:
                await self._greenhouse_fill_text_by_label(gh_frame, ("website", "portfolio"), website_url, "Website", log)

        # Discover required fields with no value — log them so user can add to profile
        unfilled_required = await gh_frame.evaluate("""
            () => {
                const empties = [];
                for (const el of document.querySelectorAll(
                    'input[required]:not([type="file"]):not([type="hidden"]), select[required], textarea[required]'
                )) {
                    const val = el.value || '';
                    if (!val.trim()) {
                        const label = el.labels?.[0]?.innerText?.trim() ||
                                      el.getAttribute('aria-label') ||
                                      el.getAttribute('placeholder') ||
                                      el.name || el.id || 'unknown';
                        empties.push(label.replace(/\\s+/g,' ').trim().slice(0,50));
                    }
                }
                return [...new Set(empties)].slice(0, 10);
            }
        """)
        if unfilled_required:
            log.info(f"⚠️ Required fields still empty before submit: {unfilled_required} — add these to your Profile")

        # Auto-accept consent checkboxes in the frame
        await self._auto_accept_consent_in_frame(gh_frame, log)

        # Brief pause so React can commit the LinkedIn/Website onChange values to its
        # internal state before we click Submit — without this, the last-filled fields
        # can be empty in the submitted payload even though they appear filled on screen.
        await gh_frame.wait_for_timeout(1500)

        # Log frame content snapshot before submit (helps debug validation errors)
        frame_text_snapshot = await gh_frame.evaluate(
            "() => (document.body?.innerText || '').slice(0, 400)"
        )
        log.info(f"Frame content before submit: {frame_text_snapshot[:200]}")

        screenshot_path = await self._safe_screenshot(page, url, "preflight")

        # Submit within the frame
        submitted = False
        for submit_sel in (
            'button:has-text("Submit application")', 'button:has-text("Submit Application")',
            'button[type="submit"]', 'input[type="submit"]', 'button:has-text("Submit")',
        ):
            try:
                loc = gh_frame.locator(submit_sel).last
                if await loc.count() and await loc.is_enabled():
                    log.submit(gh_frame.url)
                    await loc.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    await loc.click(timeout=5000)
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            log.error("No submit button found in Greenhouse iframe")
            return ApplyResult(
                success=False, screenshot_path=screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                reason="stalled", ats_platform="greenhouse",
                error="No submit button found in Greenhouse iframe",
                url=page.url,
            )

        # Wait for frame to respond
        await page.wait_for_timeout(2500)
        post_text = await gh_frame.evaluate(
            "() => (document.body?.innerText || '').toLowerCase().slice(0, 600)"
        )
        log.info(f"Frame content after submit: {post_text[:300]}")

        # --- Check for Greenhouse email verification code challenge ---
        has_code_field = await gh_frame.evaluate("""
            () => {
                const t = (document.body?.innerText || '').toLowerCase();
                const hasCodeText = t.includes('verification code') || t.includes('security code') ||
                                    t.includes('confirm you') || t.includes('sent to');
                const hasCodeInput = !!document.querySelector(
                    'input[name*="code"], input[placeholder*="code"], [class*="security-code"] input, ' +
                    '[class*="verification"] input'
                );
                return hasCodeText || hasCodeInput;
            }
        """)

        if has_code_field:
            log.info("Greenhouse email verification code detected — reading from email inbox…")
            email_addr = profile_context.get("email") or ""
            code = await self._read_greenhouse_verification_code(email_addr, log)
            if code:
                log.info(f"Verification code obtained: {code}")
                filled_code = await self._fill_greenhouse_verification_code(gh_frame, code, log)
                if filled_code:
                    # Re-click submit after entering the code
                    for submit_sel in (
                        'button:has-text("Submit application")', 'button[type="submit"]',
                        'button:has-text("Submit")',
                    ):
                        try:
                            loc = gh_frame.locator(submit_sel).last
                            if await loc.count() and await loc.is_enabled():
                                log.submit(gh_frame.url)
                                await loc.click(timeout=5000)
                                # Wait longer after code verification — Greenhouse may redirect
                                # back to the job listing in the embed instead of showing
                                # a traditional thank-you page.
                                await page.wait_for_timeout(8000)
                                break
                        except Exception:
                            continue
            else:
                message = (
                    "Could not retrieve the Greenhouse verification code from email. "
                    "Check Gmail OAuth/IMAP settings or enter the code manually in the visible browser."
                )
                log.error(message)
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    questions_encountered=dedupe(questions_encountered),
                    questions_needing_human=[message],
                    reason="needs_human",
                    ats_platform="greenhouse",
                    error=message,
                    url=page.url,
                )

        # --- Check success / validation errors ---
        success = await self._confirm_success_in_frame(gh_frame)
        if not success:
            success = await self._confirm_success(page)
        if not success:
            success = await self._confirm_success_greenhouse_embed(gh_frame, log)

        if success:
            log.info("Application submitted successfully via iframe ✓")
        else:
            captcha_type = await self._detect_captcha_type(page)
            if captcha_type:
                return await self._wait_for_captcha_review(
                    page=page,
                    screenshot_path=screenshot_path,
                    questions_encountered=questions_encountered,
                    ats_platform="greenhouse",
                    captcha_type=captcha_type,
                    log=log,
                )

            errors = await gh_frame.evaluate("""
                () => Array.from(document.querySelectorAll(
                    '[class*="error"]:not(:empty), [aria-invalid="true"], .field_with_errors'
                )).map(e => e.innerText.trim()).filter(t => t).slice(0, 5)
            """)
            if errors:
                log.error(f"Form validation errors: {errors}")
            else:
                log.error("Submit clicked but no confirmation and no validation errors detected")

        return ApplyResult(
            success=success, screenshot_path=screenshot_path,
            questions_encountered=dedupe(questions_encountered),
            ats_platform="greenhouse", url=page.url,
            error=None if success else f"Submit in iframe: confirmation not detected. Frame text: {post_text[:200]}",
        )

    async def _greenhouse_has_cover_letter_field(self, frame: Any) -> bool:
        """Check if the Greenhouse iframe has a cover letter upload section (including hidden inputs)."""
        try:
            return bool(await frame.evaluate("""
                () => {
                    const text = (document.body?.innerText || '').toLowerCase();
                    return text.includes('cover letter');
                }
            """))
        except Exception:
            return False

    async def _greenhouse_upload_hidden_file(self, frame: Any, file_path: str, label_keyword: str, log: AuditLog) -> None:
        """
        Upload a file to a Greenhouse hidden <input type=file> by finding the input
        near a section whose label text contains label_keyword.
        """
        try:
            # Mark the exact target input via JS so we can query it with Playwright.
            # Greenhouse often renders hidden file inputs inside broad containers that
            # contain both "Resume/CV" and "Cover Letter"; picking the first file input
            # from that broad container uploads the cover letter into the resume slot.
            found = await frame.evaluate("""
                (keyword) => {
                    const norm = (value) => String(value || '')
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
                    const coverTokens = ['cover letter', 'coverletter', 'cover'];
                    const resumeTokens = ['resume', 'cv', 'curriculum vitae'];
                    const hasAny = (text, tokens) => tokens.some((token) => text.includes(token));
                    const visibleText = (el) => norm(el && (el.innerText || el.textContent || ''));
                    const attrText = (input) => norm([
                        input.name, input.id, input.getAttribute('aria-label'),
                        input.getAttribute('placeholder'), input.getAttribute('title'),
                    ].filter(Boolean).join(' '));
                    const explicitLabelText = (input) => {
                        const parts = [];
                        if (input.id) {
                            for (const label of document.querySelectorAll(`label[for="${CSS.escape(input.id)}"]`)) {
                                parts.push(label.innerText || label.textContent || '');
                            }
                        }
                        const wrapped = input.closest('label');
                        if (wrapped) parts.push(wrapped.innerText || wrapped.textContent || '');
                        const labelledBy = input.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            for (const id of labelledBy.split(/\s+/)) {
                                const el = document.getElementById(id);
                                if (el) parts.push(el.innerText || el.textContent || '');
                            }
                        }
                        return norm(parts.join(' '));
                    };
                    const ancestorTexts = (input) => {
                        const texts = [];
                        let el = input.parentElement;
                        for (let depth = 0; el && depth < 6; depth += 1, el = el.parentElement) {
                            const text = visibleText(el);
                            if (text) texts.push({ depth, text, length: text.length });
                        }
                        return texts;
                    };
                    const scoreInput = (input, index) => {
                        let score = 0;
                        const direct = norm(`${attrText(input)} ${explicitLabelText(input)}`);
                        if (hasAny(direct, coverTokens)) score += 120;
                        if (hasAny(direct, resumeTokens)) score -= 120;

                        for (const item of ancestorTexts(input)) {
                            const text = item.text;
                            const hasCover = hasAny(text, coverTokens);
                            const hasResume = hasAny(text, resumeTokens);
                            const close = item.length <= 220;
                            if (hasCover && !hasResume) score += close ? 70 - item.depth * 8 : 15;
                            if (hasResume && !hasCover) score -= close ? 70 - item.depth * 8 : 15;
                            // A broad ancestor with both labels is not useful evidence.
                        }

                        return { input, index, score, direct };
                    };

                    const scored = fileInputs
                        .map(scoreInput)
                        .filter((item) => item.score > 0)
                        .sort((a, b) => b.score - a.score);
                    const winner = scored[0];
                    if (winner) {
                        winner.input.setAttribute('data-cl-target', 'true');
                        return { index: winner.index, score: winner.score, direct: winner.direct };
                    }

                    if (norm(keyword).includes('cover') && fileInputs.length === 2) {
                        fileInputs[1].setAttribute('data-cl-target', 'true');
                        return { index: 1, score: 1, fallback: true };
                    }
                    return null;
                }
            """, label_keyword)

            if found:
                log.info(f"Greenhouse cover-letter upload target: {found}")
                cl_input = await frame.query_selector('input[type="file"][data-cl-target]')
                if cl_input:
                    await cl_input.set_input_files(file_path)
                    await cl_input.evaluate(
                        """el => {
                            el.dataset.jobpilotUploaded = 'true';
                            el.dataset.jobpilotUploadKind = 'cover';
                        }"""
                    )
                    import os
                    log.upload(f"{label_keyword} file", os.path.basename(file_path))
                    # Clean up marker
                    await frame.evaluate("() => document.querySelectorAll('[data-cl-target]').forEach(e => e.removeAttribute('data-cl-target'))")
                    return

            # Fallback: find ALL file inputs and upload to the one we haven't used for resume
            all_inputs = [
                input_el
                for input_el in await frame.query_selector_all('input[type="file"]')
                if not await input_el.evaluate("el => el.dataset.jobpilotUploaded === 'true'")
            ]
            if len(all_inputs) > 1:
                # Second file input is typically cover letter
                await all_inputs[1].set_input_files(file_path)
                await all_inputs[1].evaluate(
                    """el => {
                        el.dataset.jobpilotUploaded = 'true';
                        el.dataset.jobpilotUploadKind = 'cover';
                    }"""
                )
                import os
                log.upload(f"{label_keyword} file (input[1])", os.path.basename(file_path))
        except Exception as exc:
            log.info(f"Cover letter upload failed: {str(exc)[:80]}")

    async def _greenhouse_fill_text_by_label(
        self,
        frame: Any,
        keywords: tuple[str, ...],
        value: str,
        field_label: str,
        log: AuditLog,
    ) -> bool:
        """Fill Greenhouse optional text fields whose IDs/names do not mention the label."""
        try:
            filled = await frame.evaluate(
                """
                ([keywords, value]) => {
                    const norm = (input) => String(input || '')
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const wanted = keywords.map(norm);
                    const matches = (text) => wanted.some((keyword) => keyword && norm(text).includes(keyword));
                    const fieldSelector = 'input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]):not([type="submit"]), textarea';
                    const visible = (el) => !!(el && (el.offsetParent !== null || el.getClientRects().length));
                    const empty = (field) => !String(field.value || '').trim();
                    const setValue = (field) => {
                        const proto = field.tagName.toLowerCase() === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        setter?.call(field, value);
                        field.value = value;
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    };
                    const contextFor = (field) => {
                        const parts = [
                            field.name, field.id, field.getAttribute('aria-label'),
                            field.getAttribute('placeholder'), field.getAttribute('title'),
                        ];
                        if (field.id) {
                            for (const label of document.querySelectorAll(`label[for="${CSS.escape(field.id)}"]`)) {
                                parts.push(label.innerText || label.textContent || '');
                            }
                        }
                        const wrapped = field.closest('label');
                        if (wrapped) parts.push(wrapped.innerText || wrapped.textContent || '');
                        let prev = field.previousElementSibling;
                        for (let hops = 0; prev && hops < 3; hops += 1, prev = prev.previousElementSibling) {
                            parts.push(prev.innerText || prev.textContent || '');
                        }
                        let parent = field.parentElement;
                        for (let depth = 0; parent && depth < 4; depth += 1, parent = parent.parentElement) {
                            const text = parent.innerText || parent.textContent || '';
                            const fieldCount = parent.querySelectorAll(fieldSelector).length;
                            if (text.length < 260 && fieldCount <= 1) parts.push(text);
                        }
                        return parts.filter(Boolean).join(' ');
                    };

                    for (const field of Array.from(document.querySelectorAll(fieldSelector))) {
                        if (!visible(field) || field.disabled || field.readOnly) continue;
                        if (!empty(field)) continue;
                        if (matches(contextFor(field))) {
                            setValue(field);
                            return { method: 'field-context', name: field.name || field.id || field.placeholder || '' };
                        }
                    }

                    const labels = Array.from(document.querySelectorAll('label, span, div, p'))
                        .filter((el) => {
                            if (!visible(el)) return false;
                            const text = el.innerText || el.textContent || '';
                            if (norm(text).length > 90) return false;
                            return matches(text);
                        })
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                    for (const label of labels) {
                        let container = label;
                        for (let depth = 0; container && depth < 5; depth += 1, container = container.parentElement) {
                            const fields = Array.from(container.querySelectorAll(fieldSelector))
                                .filter((field) => visible(field) && !field.disabled && !field.readOnly && empty(field))
                                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                            if (!fields.length) continue;
                            const labelTop = label.getBoundingClientRect().top;
                            const chosen = fields.find((field) => field.getBoundingClientRect().top >= labelTop - 8) || fields[0];
                            setValue(chosen);
                            return { method: 'label-container', name: chosen.name || chosen.id || chosen.placeholder || '' };
                        }
                    }
                    return null;
                }
                """,
                [list(keywords), value],
            )
            if filled:
                log.fill(field_label, value, f"greenhouse-label-{filled.get('method', 'fill')}")
                return True
        except Exception as exc:
            log.info(f"Greenhouse {field_label} fill failed: {str(exc)[:80]}")
        return False

    async def _dismiss_cookie_consent_in_frame(self, frame: Any, log: AuditLog) -> None:
        """Dismiss cookie consent inside a Playwright frame."""
        for sel in ("#onetrust-accept-btn-handler", 'button:has-text("Accept")',
                    'button:has-text("Accept all")', 'button:has-text("I agree")',
                    '[data-qa="consent-modal-accept"]'):
            try:
                btn = frame.locator(sel).first
                if await btn.count() and await btn.is_visible(timeout=800):
                    await btn.click(timeout=2000)
                    log.info(f"Dismissed iframe consent: {sel}")
                    return
            except Exception:
                continue

    async def _auto_accept_consent_in_frame(self, frame: Any, log: AuditLog) -> None:
        consent_keywords = ("consent", "terms", "privacy", "agree", "gdpr",
                            "authorize", "process", "allow", "acknowledge", "accept")
        try:
            cbs = await frame.query_selector_all('input[type="checkbox"]:visible')
            for cb in cbs:
                try:
                    if await cb.is_checked():
                        continue
                    label = (await self.form_filler.extract_label_text(cb)).lower()
                    if any(k in label for k in consent_keywords):
                        await cb.check(timeout=3000)
                        log.fill(label[:60], "checked", "consent-checkbox")
                except Exception:
                    continue
        except Exception:
            pass

    async def _is_greenhouse_application_form_frame(self, frame: Any) -> bool:
        try:
            return bool(await frame.evaluate("""
                () => !!(document.querySelector('#first_name, #last_name, #email') ||
                         document.querySelector('#application_form, form#new_application') ||
                         document.querySelector('input[name*="first_name"], input[name*="email"]'))
            """))
        except Exception:
            return False

    async def _fill_greenhouse_country(self, frame: Any, country: str, log: AuditLog) -> None:
        """
        Fill the Country field in a Greenhouse new-board form.
        The new Greenhouse uses a React custom dropdown — not a native <select>.
        We use a DOM-walker + click-type-select approach.
        """
        # First scroll to the form so React renders all components
        await frame.evaluate("""
            () => {
                const form = document.querySelector('#first_name, form, .application-form');
                if (form) form.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        """)
        await frame.wait_for_timeout(800)

        # Inspect what the Country field actually is
        field_info = await frame.evaluate("""
            () => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t === 'Country' || t === 'Country*') {
                        let el = node.parentElement;
                        for (let i = 0; i < 6; i++) {
                            const ctrl = el.querySelector(
                                'select, [role="combobox"], [role="listbox"], button:not([type="submit"]), input:not([type="hidden"])'
                            );
                            if (ctrl && ctrl.offsetParent !== null) {
                                return {
                                    tag: ctrl.tagName.toLowerCase(),
                                    role: ctrl.getAttribute('role'),
                                    cls: ctrl.className.slice(0, 80),
                                    id: ctrl.id,
                                    name: ctrl.name,
                                    type: ctrl.type
                                };
                            }
                            el = el.parentElement;
                            if (!el) break;
                        }
                        return { found: false, parent_html: node.parentElement?.outerHTML?.slice(0, 200) };
                    }
                }
                return { not_found: true };
            }
        """)
        log.info(f"Country field element: {field_info}")

        # Strategy 1: native <select> (old Greenhouse format)
        filled = await frame.evaluate("""
            (country) => {
                const sel = document.querySelector(
                    'select[name="job_application[country]"], #job_application_country, ' +
                    'select[name*="country"], select[id*="country"]'
                );
                if (!sel) return null;
                const lower = country.toLowerCase();
                const match = Array.from(sel.options).find(o =>
                    o.value === 'US' || o.text.toLowerCase().includes('united states') ||
                    o.value.toLowerCase() === lower || o.text.toLowerCase() === lower
                );
                if (!match) return 'no_match';
                const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
                setter?.set?.call(sel, match.value);
                sel.value = match.value;
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                return 'select:' + match.value;
            }
        """, country)

        if filled and not filled.startswith("no"):
            log.fill("country", filled, "greenhouse-country-select")
            return

        # Strategy 2: DOM-walker click → open dropdown → type → click option
        clicked = await frame.evaluate("""
            () => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t === 'Country' || t === 'Country*') {
                        let container = node.parentElement;
                        for (let i = 0; i < 6; i++) {
                            const ctrl = container.querySelector(
                                '[role="combobox"], [role="button"], button:not([type="submit"])'
                            );
                            if (ctrl && ctrl.offsetParent !== null) {
                                ctrl.click();
                                return ctrl.tagName + ':' + ctrl.className.slice(0, 40);
                            }
                            container = container.parentElement;
                            if (!container) break;
                        }
                    }
                }
                return null;
            }
        """)
        log.info(f"Country dropdown click: {clicked}")

        # Strategy 2b: use the known React Select input directly (id="country", class="select__input")
        # This is the reliable path — focus via JS, fill, pick option from listbox
        country_input = frame.locator('#country.select__input, input[id="country"][role="combobox"], input[role="combobox"][class*="select__input"]').first
        if await country_input.count():
            try:
                # Focus without clicking to avoid overlay intercept
                await country_input.evaluate("el => el.focus()")
                await frame.wait_for_timeout(200)
                await country_input.fill("")
                await country_input.fill("United States")
                await frame.wait_for_timeout(600)
                # Pick from dropdown
                for opt_sel in (
                    '[role="option"]:has-text("United States")',
                    '.select__option:has-text("United States")',
                    '[class*="option"]:has-text("United States")',
                    '[role="option"]',  # first available option
                ):
                    opt = frame.locator(opt_sel).first
                    if await opt.count():
                        await opt.click(timeout=2000)
                        log.fill("country", "United States", "react-select-combobox")
                        return
                # Press Enter to accept highlighted option
                await country_input.press("Enter")
                log.fill("country", "United States (Enter)", "react-select-enter")
                return
            except Exception as e:
                log.info(f"React Select country fill failed: {str(e)[:80]}")

        if clicked:
            await frame.wait_for_timeout(600)

        # Strategy 3: find a hidden input and set value directly
        set_hidden = await frame.evaluate("""
            () => {
                const hidden = document.querySelector('input[name="job_application[country]"]');
                if (hidden) {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                    setter?.set?.call(hidden, 'US');
                    hidden.value = 'US';
                    hidden.dispatchEvent(new Event('change', { bubbles: true }));
                    return 'hidden_input_set';
                }
                return null;
            }
        """)
        if set_hidden:
            log.fill("country", "US (hidden input)", "greenhouse-country-hidden")

        # Always also handle phone country code select
        await frame.evaluate("""
            () => {
                for (const sel of document.querySelectorAll(
                    'select[name*="phone_country_code"], select.phone-country-code, select[id*="country_code"], select[id*="dial_code"]'
                )) {
                    const us = Array.from(sel.options).find(o =>
                        o.value === 'US' || o.value === '+1' || o.text.includes('United States') || o.text.startsWith('+1')
                    );
                    if (us) {
                        sel.value = us.value;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            }
        """)

    async def _read_greenhouse_verification_code(self, email_addr: str, log: AuditLog, timeout_s: int = 120) -> str | None:
        """
        Read Greenhouse's email verification code.
        Tries three methods in order:
          1. Gmail API OAuth2  (works for Google Workspace / ASU accounts — needs data/gmail_token.pickle)
          2. IMAP + App Password  (personal Gmail with App Password)
          3. Falls back to None — verification code will be missed
        """
        import asyncio as _asyncio
        import re
        import time

        log.info("Waiting for Greenhouse verification email…")
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            # --- Method 1: Gmail API OAuth2 ---
            code = await _asyncio.get_event_loop().run_in_executor(None, self._gmail_api_read_code)
            if code:
                log.info(f"Verification code read via Gmail API: {code}")
                return code

            # --- Method 2: IMAP + App Password ---
            code = await _asyncio.get_event_loop().run_in_executor(None, self._imap_read_code)
            if code:
                log.info(f"Verification code read via IMAP: {code}")
                return code

            log.info("Code not found yet — retrying in 5s…")
            await _asyncio.sleep(5)

        log.error("Verification code not found after timeout. Add Gmail OAuth2 or App Password to .env")

        return None

    def _extract_code_from_body(self, body: str) -> str | None:
        import re

        text = self._email_body_to_text(body)

        # Greenhouse sends mixed-case 8-character alphanumeric codes, e.g. k4dlPL31.
        # Do not accept arbitrary 8-character strings. Only accept a candidate that
        # appears next to explicit code language in the email.
        code_markers = (
            "verification code",
            "security code",
            "confirmation code",
            "one-time code",
            "one time code",
            "temporary code",
        )
        has_code_marker = any(re.search(re.escape(marker), text, flags=re.IGNORECASE) for marker in code_markers)

        candidate_pattern = re.compile(r"\b([A-Za-z0-9]{8}|\d{6,8})\b")
        for marker in code_markers:
            for match in re.finditer(re.escape(marker), text, flags=re.IGNORECASE):
                after = text[match.end():match.end() + 220]
                for candidate_match in candidate_pattern.finditer(after):
                    candidate = candidate_match.group(1)
                    prefix = after[:candidate_match.start()]
                    if self._looks_like_greenhouse_code(candidate) and self._code_context_allows_candidate(prefix):
                        return candidate

                before = text[max(0, match.start() - 140):match.start()]
                before_candidates = [
                    candidate_match.group(1)
                    for candidate_match in candidate_pattern.finditer(before)
                    if (
                        self._looks_like_greenhouse_code(candidate_match.group(1))
                        and self._code_context_allows_candidate(before[candidate_match.end():])
                    )
                ]
                if before_candidates:
                    return before_candidates[-1]

        explicit_patterns = (
            r"(?is)\b(?:enter|use|copy|paste|provide)\s+(?:this\s+)?(?:verification\s+|security\s+|one[- ]time\s+)?code\b[^A-Za-z0-9]{0,80}([A-Za-z0-9]{8}|\d{6,8})\b",
            r"(?is)\b(?:verification|security|one[- ]time)\s+code\s*(?:is|:|=|-)\s*([A-Za-z0-9]{8}|\d{6,8})\b",
            r"(?is)\b([A-Za-z0-9]{8}|\d{6,8})\b[^.!?]{0,80}\b(?:as|for)\s+(?:your\s+)?(?:verification|security|one[- ]time)\s+code\b",
        )
        if has_code_marker:
            explicit_patterns = (
                *explicit_patterns,
                r"(?is)\byour\s+code\s*(?:is|:|=|-)\s*([A-Za-z0-9]{8}|\d{6,8})\b",
            )
        for pattern in explicit_patterns:
            m = re.search(pattern, text)
            if m and self._looks_like_greenhouse_code(m.group(1)):
                return m.group(1)

        return None

    def _code_context_allows_candidate(self, context: str) -> bool:
        import re

        # A colon (with optional whitespace) immediately before the candidate strongly
        # signals "here is the value" — skip the word-based check entirely.
        if re.match(r"^\s*$", context) or re.search(r":\s*$", context.rstrip()):
            return True

        words = re.findall(r"[A-Za-z]+", context.lower())
        if not words:
            return True
        allowed_words = {
            "a", "an", "are", "below", "code", "continue", "copy", "enter",
            "field", "following", "for", "here", "in", "into", "is", "on",
            "one", "paste", "please", "requested", "the", "this", "time",
            "to", "use", "was", "you", "your",
        }
        blocked_words = {
            "account",
            "expires",
            "greenhouse",
            "minutes",
            "posting",
            "request",
            "session",
            "token",
            "url",
        }
        if any(word in blocked_words for word in words):
            return False
        return all(word in allowed_words for word in words)

    def _looks_like_greenhouse_code(self, candidate: str) -> bool:
        if not candidate.isalnum():
            return False
        if candidate.isdigit():
            return 6 <= len(candidate) <= 8
        if len(candidate) != 8:
            return False
        lowered = candidate.lower()
        if lowered in {"security", "password", "applying", "greenhou", "confirm", "received", "continue"}:
            return False
        # Accept pure-alpha 8-char codes (e.g. wMyYXcVu) and mixed alphanumeric codes.
        # Reject pure-digit 8-char strings here (they're caught by the isdigit branch above).
        has_letter = any(ch.isalpha() for ch in candidate)
        return has_letter

    def _email_body_to_text(self, body: str, content_type: str = "") -> str:
        import html
        import re

        if "html" in content_type.lower() or "<html" in body.lower() or "<body" in body.lower():
            body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
            body = re.sub(r"(?s)<[^>]+>", " ", body)
            body = html.unescape(body)
        return re.sub(r"\s+", " ", body).strip()

    def _gmail_payload_to_text(self, payload: dict) -> str:
        import base64

        chunks: list[str] = []

        def decode_part(part: dict) -> None:
            mime_type = (part.get("mimeType") or "").lower()
            data = (part.get("body") or {}).get("data") or ""
            if data and (mime_type.startswith("text/plain") or mime_type.startswith("text/html")):
                padding = "=" * (-len(data) % 4)
                decoded = base64.urlsafe_b64decode(data + padding).decode(errors="ignore")
                chunks.append(self._email_body_to_text(decoded, mime_type))
            for child in part.get("parts") or []:
                decode_part(child)

        decode_part(payload)
        return "\n".join(chunk for chunk in chunks if chunk)

    def _email_message_to_text(self, msg: object) -> str:
        chunks: list[str] = []

        if getattr(msg, "is_multipart", lambda: False)():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = (part.get_content_disposition() or "").lower()
                if disposition == "attachment" or content_type not in {"text/plain", "text/html"}:
                    continue
                payload = part.get_payload(decode=True)
                if payload:
                    chunks.append(self._email_body_to_text(payload.decode(errors="ignore"), content_type))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                chunks.append(self._email_body_to_text(payload.decode(errors="ignore"), msg.get_content_type()))

        return "\n".join(chunk for chunk in chunks if chunk)

    def _gmail_api_read_code(self) -> str | None:
        """Read verification code via Gmail API (OAuth2 — works for Google Workspace)."""
        try:
            import pickle
            from pathlib import Path as _Path
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            from backend.config import PROJECT_ROOT, settings
            token_path = _Path(getattr(settings, "gmail_oauth_token_path", "data/gmail_token.pickle"))
            if not token_path.is_absolute():
                token_path = PROJECT_ROOT / token_path
            if not token_path.exists():
                return None

            with open(token_path, "rb") as f:
                creds = pickle.load(f)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "wb") as f:
                    pickle.dump(creds, f)

            service = build("gmail", "v1", credentials=creds)
            queries = [
                'from:no-reply@us.greenhouse-mail.io newer_than:1d',
                'from:us.greenhouse-mail.io newer_than:1d',
                'from:greenhouse-mail.io newer_than:1d',
                'from:greenhouse.io newer_than:1d',
                'subject:verification newer_than:1d',
                'subject:"security code" newer_than:1d',
            ]
            seen_ids: set[str] = set()
            for query in queries:
                results = service.users().messages().list(
                    userId="me",
                    q=query,
                    maxResults=10,
                ).execute()

                for msg_meta in results.get("messages", []):
                    msg_id = msg_meta.get("id")
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)
                    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
                    payload = msg.get("payload", {})
                    body = self._gmail_payload_to_text(payload)
                    if msg.get("snippet"):
                        body += "\n" + msg["snippet"]
                    code = self._extract_code_from_body(body)
                    if code:
                        return code
        except ImportError:
            pass  # google-api-python-client not installed
        except Exception:
            pass
        return None

    def _imap_read_code(self) -> str | None:
        """Read verification code via IMAP (requires App Password for Gmail)."""
        try:
            import email as _email
            import imaplib
            from datetime import datetime, timedelta
            from backend.config import settings

            imap_host = (
                getattr(settings, "imap_host", "") or
                settings.smtp_host.replace("smtp.", "imap.").replace("mail.", "imap.") or
                "imap.gmail.com"
            )
            if not settings.smtp_user or not settings.smtp_password:
                return None

            with imaplib.IMAP4_SSL(imap_host) as mail:
                mail.login(settings.smtp_user, settings.smtp_password)
                mail.select("INBOX")
                since = (datetime.utcnow() - timedelta(days=1)).strftime("%d-%b-%Y")
                sender_queries = (
                    'no-reply@us.greenhouse-mail.io',
                    'us.greenhouse-mail.io',
                    'greenhouse-mail.io',
                    'greenhouse.io',
                )
                queries = (
                    [f'UNSEEN FROM "{sender}"' for sender in sender_queries]
                    + [f'FROM "{sender}" SINCE {since}' for sender in sender_queries]
                    + [
                        'UNSEEN SUBJECT "verification"',
                        'UNSEEN SUBJECT "security code"',
                        f'SUBJECT "verification" SINCE {since}',
                        f'SUBJECT "security code" SINCE {since}',
                    ]
                )
                seen_ids: set[bytes] = set()
                for query in queries:
                    _, msgs = mail.search(None, query)
                    for msg_id in reversed((msgs[0] or b"").split()[-5:]):
                        if msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)
                        _, data = mail.fetch(msg_id, "(RFC822)")
                        msg = _email.message_from_bytes(data[0][1])
                        body = self._email_message_to_text(msg)
                        code = self._extract_code_from_body(body)
                        if code:
                            return code
        except Exception:
            pass
        return None

    async def _fill_greenhouse_verification_code(self, frame: Any, code: str, log: AuditLog) -> bool:
        """Fill Greenhouse's 8-character email verification code boxes."""
        try:
            # Find individual character input boxes
            code_inputs = await frame.query_selector_all(
                '[class*="security-code"] input, [class*="verification"] input, '
                'input[name*="code"], input[autocomplete="one-time-code"]'
            )
            if code_inputs:
                for i, char in enumerate(code[:len(code_inputs)]):
                    await code_inputs[i].fill(char)
                log.fill("security_code", code, "greenhouse-verification")
                return True

            # Fallback: single code input field
            single = frame.locator('input[name*="code"], input[placeholder*="code"]').first
            if await single.count():
                await single.fill(code)
                log.fill("security_code", code, "greenhouse-verification-single")
                return True
        except Exception as exc:
            log.error(f"Security code fill failed: {str(exc)[:80]}")
        return False

    async def _confirm_success_in_frame(self, frame: Any, timeout_s: int = 10) -> bool:
        """Wait up to timeout_s for a success message to appear in the frame."""
        phrases = json.dumps([
            "application submitted", "thank you for applying",
            "successfully submitted", "application received",
            "we received your application", "your application has been",
            "thank you for completing", "application complete",
            "thank you for your application",
            "we have received your application",
            "your application is complete",
            "application has been received",
        ])
        try:
            await frame.wait_for_function(
                f"() => {{ const t=(document.body?.innerText||'').toLowerCase(); return {phrases}.some(p=>t.includes(p)); }}",
                timeout=timeout_s * 1000,
            )
            return True
        except Exception:
            return False

    async def _confirm_success_greenhouse_embed(self, frame: Any, log: AuditLog) -> bool:
        """
        Detect success for Greenhouse embeds that redirect back to the job listing
        after submission instead of showing a traditional thank-you page.

        Success signals:
        1. The application form fields (#first_name, #email, etc.) are no longer in the DOM
           AND 'apply' button is visible again — embed went back to listing = submitted.
        2. The frame URL changed away from the /embed/job_app path.
        3. A brief success/thank-you flash that may have already passed.
        """
        try:
            form_gone = await frame.evaluate("""
                () => {
                    const hasForm = !!(
                        document.querySelector('#first_name, #last_name, #email') ||
                        document.querySelector('#application_form, form#new_application') ||
                        document.querySelector('input[name*="first_name"], input[name*="email"]')
                    );
                    const hasApplyButton = !!(
                        document.querySelector('button, a')
                        && Array.from(document.querySelectorAll('button, a'))
                            .some(el => /^apply$/i.test((el.innerText || '').trim()))
                    );
                    // No form + Apply button re-appeared = listing view = submitted
                    return !hasForm && hasApplyButton;
                }
            """)
            if form_gone:
                log.info("Greenhouse embed returned to job listing (form gone + Apply button visible) — treating as success.")
                return True
        except Exception:
            pass
        return False

    async def _apply_greenhouse(
        self,
        page: Page,
        url: str,
        profile_context: dict,
        qa_engine: QAEngine,
        questions_encountered: list[str],
        questions_needing_human: list[str],
        log: AuditLog | None = None,
    ) -> ApplyResult:
        _log = log or AuditLog()

        # --- Step 0: dismiss cookie popup on the host page FIRST ---
        await self._dismiss_cookie_consent(page, _log)
        _log.info(f"Host page URL: {page.url}")

        # --- Step 1: check if the Greenhouse form redirected the main window back to the
        # company page. If so, we must stay on the company page and work within the
        # Greenhouse iframe — navigating the main window to greenhouse.io will just redirect
        # back (Greenhouse embeds often block direct navigation). ---

        if "greenhouse.io" not in page.url:
            _log.info("Main window is on company page — working within Greenhouse iframe")
            return await self._apply_greenhouse_in_iframe(
                page=page, url=url, profile_context=profile_context,
                qa_engine=qa_engine, questions_encountered=questions_encountered,
                questions_needing_human=questions_needing_human, log=_log,
            )

        # --- Step 2 (direct greenhouse.io URL): dismiss popup and check form ---
        await self._dismiss_cookie_consent(page, _log)
        on_form = await self._is_greenhouse_application_form(page)
        _log.info(f"On direct Greenhouse page. Application form present: {on_form}")

        if not on_form:
            # Might be on the job-listing page — try clicking Apply
            for apply_sel in (
                'a:has-text("Apply for this job")',
                'button:has-text("Apply for this job")',
                '.application-button',
            ):
                try:
                    btn = page.locator(apply_sel).first
                    if await btn.count() and await btn.is_visible(timeout=2000):
                        _log.info(f"Clicking '{apply_sel}'")
                        await btn.click(timeout=5000)
                        await page.wait_for_timeout(2000)
                        await self._dismiss_cookie_consent(page, _log)
                        break
                except Exception:
                    continue

            if not await self._is_greenhouse_application_form(page):
                # Append /application to the current Greenhouse URL
                base = page.url.rstrip("/")
                if "/jobs/" in base and not base.endswith("/application"):
                    app_url = base + "/application"
                    _log.info(f"Navigating to {app_url}")
                    await page.goto(app_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(1500)
                    await self._dismiss_cookie_consent(page, _log)

        MAX_PAGES = 10
        for step in range(MAX_PAGES):
            _log.info(f"Greenhouse form — page {step + 1}")

            # Give React time to mount and render all form fields
            await page.wait_for_timeout(2000)

            # Auto-accept consent / terms checkboxes before filling so they never block submit
            await self._auto_accept_consent(page, _log)

            # Fill using targeted Greenhouse selectors (text, select, checkbox) + JS fallback
            await self._fill_greenhouse_fields(page, profile_context)
            questions_encountered.extend(await self._visible_questions(page))

            # Additionally scan all visible inputs/selects — catches any field not in the
            # known-selector map (textarea, dynamic fields, etc.)
            filled = await self.form_filler.detect_and_fill_form(page, profile_context, qa_engine)
            questions_needing_human.extend(filled.questions_needing_human)

            # Also fill any embedded Greenhouse/ATS frames
            for frame in self._application_frames(page):
                await self._auto_accept_consent_frame(frame, _log)
                questions_encountered.extend(await self._visible_questions(frame))
                frame_result = await self.form_filler.detect_and_fill_form(frame, profile_context, qa_engine)
                questions_needing_human.extend(frame_result.questions_needing_human)

            if questions_needing_human:
                screenshot_path = await self._safe_screenshot(page, url, "needs_human")
                _log.info(
                    "Greenhouse form has unanswered questions — stored in Q&A memory. "
                    "Answer them in the Q&A tab and re-trigger this application."
                )
                return ApplyResult(
                    success=False,
                    screenshot_path=screenshot_path,
                    reason="needs_human",
                    ats_platform="greenhouse",
                    questions_needing_human=dedupe(questions_needing_human),
                    error="Missing answers for: " + "; ".join(dedupe(questions_needing_human)[:3]),
                    url=page.url,
                )

            # Try submit first — if found we're on the last page
            for submit_selector in (
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Submit application")',
                'button:has-text("Submit Application")',
                'button:has-text("Submit")',
            ):
                locator = page.locator(submit_selector).last
                try:
                    if await locator.count() and await locator.is_enabled():
                        screenshot_path = await self._safe_screenshot(page, url, "preflight")
                        if settings.apply_require_human_review:
                            return await self._wait_for_manual_review(
                                page=page,
                                screenshot_path=screenshot_path,
                                questions_encountered=questions_encountered,
                                ats_platform="greenhouse",
                                log=_log,
                            )
                        _log.submit(page.url)
                        await locator.click()
                        await page.wait_for_timeout(2500)
                        success = await self._confirm_success(page)
                        if success:
                            _log.info("Application submitted successfully.")
                        else:
                            _log.error("Submit clicked but confirmation page not detected.")
                        return ApplyResult(
                            success=success,
                            screenshot_path=screenshot_path,
                            questions_encountered=dedupe(questions_encountered),
                            ats_platform="greenhouse",
                            error=None if success else "Submit clicked but confirmation not detected.",
                            url=page.url,
                        )
                except Exception:
                    continue

            # No submit found on main page — check inside Greenhouse frames too
            for frame in self._application_frames(page):
                for submit_sel in (
                    'button[type="submit"]', 'input[type="submit"]',
                    'button:has-text("Submit application")', 'button:has-text("Submit Application")',
                    'button:has-text("Submit")',
                ):
                    try:
                        loc = frame.locator(submit_sel).last
                        if await loc.count() and await loc.is_enabled():
                            screenshot_path = await self._safe_screenshot(page, url, "preflight")
                            _log.submit(page.url)
                            await loc.click()
                            await page.wait_for_timeout(3000)
                            success = await self._confirm_success(page)
                            if success:
                                _log.info("Application submitted successfully (frame submit).")
                            else:
                                _log.error("Frame submit clicked but confirmation not detected.")
                            return ApplyResult(
                                success=success, screenshot_path=screenshot_path,
                                questions_encountered=dedupe(questions_encountered),
                                ats_platform="greenhouse",
                                error=None if success else "Frame submit clicked but confirmation not detected.",
                                url=page.url,
                            )
                    except Exception:
                        continue

            # Look for Next/Continue on page AND in frames
            advanced = False
            next_selectors = (
                'button:has-text("Next")', 'button:has-text("Continue")',
                'button:has-text("Next step")', 'a:has-text("Next")',
            )
            # Check main page first
            for next_selector in next_selectors:
                locator = page.locator(next_selector).last
                try:
                    if await locator.count() and await locator.is_enabled():
                        _log.step(f"clicked '{next_selector.split(chr(34))[1]}'", page_number=step + 1)
                        await locator.click()
                        await page.wait_for_timeout(1500)
                        advanced = True
                        break
                except Exception:
                    continue

            # Then check inside frames
            if not advanced:
                for frame in self._application_frames(page):
                    for next_selector in next_selectors:
                        try:
                            loc = frame.locator(next_selector).last
                            if await loc.count() and await loc.is_enabled():
                                _log.step(f"frame: clicked '{next_selector.split(chr(34))[1]}'", page_number=step + 1)
                                await loc.click()
                                await page.wait_for_timeout(1500)
                                advanced = True
                                break
                        except Exception:
                            continue
                    if advanced:
                        break

            if not advanced:
                screenshot_path = await self._safe_screenshot(page, url, "stalled")
                return ApplyResult(
                    success=False, screenshot_path=screenshot_path,
                    questions_encountered=dedupe(questions_encountered),
                    reason="stalled", ats_platform="greenhouse",
                    error=f"Greenhouse form stalled on step {step + 1}: no Next or Submit button found on page or in frames.",
                    url=page.url,
                )

        screenshot_path = await self._safe_screenshot(page, url, "stalled")
        return ApplyResult(
            success=False,
            screenshot_path=screenshot_path,
            questions_encountered=dedupe(questions_encountered),
            reason="stalled",
            ats_platform="greenhouse",
            error=f"Greenhouse form exceeded {MAX_PAGES} steps without submitting.",
            url=page.url,
        )

    # -------------------------------------------------------------------
    # Human-in-the-loop CAPTCHA handling
    # -------------------------------------------------------------------

    async def _detect_captcha_type(self, page: Page) -> str | None:
        """Return the CAPTCHA type present on the page, or None."""
        frame_urls = " ".join(frame.url.lower() for frame in page.frames)
        if "recaptcha" in frame_urls:
            return "reCAPTCHA"
        if "hcaptcha.com" in frame_urls:
            return "hCaptcha"
        if "challenges.cloudflare.com" in frame_urls or "turnstile" in frame_urls:
            return "Cloudflare Turnstile"

        return await page.evaluate("""
            () => {
                if (document.querySelector('.g-recaptcha[data-sitekey], iframe[src*="recaptcha"]'))
                    return 'reCAPTCHA';
                if (document.querySelector('.h-captcha[data-sitekey], iframe[src*="hcaptcha.com"]'))
                    return 'hCaptcha';
                if (document.querySelector('.cf-turnstile[data-sitekey]'))
                    return 'Cloudflare Turnstile';
                return null;
            }
        """)

    async def _auto_solve_captcha(self, page: Page, captcha_type: str, log: AuditLog) -> bool:
        """
        CAPTCHA solving is intentionally human-in-the-loop.

        The application flow can pause with a visible browser and resume after the
        user completes the challenge, but it must not attempt automatic CAPTCHA
        bypasses or third-party solver handoff.
        """
        log.info(f"{captcha_type} detected — automatic CAPTCHA solving is disabled; waiting for human completion.")
        return False

    async def _patchright_captcha_retry(
        self,
        url: str,
        profile_context: dict,
        qa_engine: QAEngine,
        log: AuditLog,
        ats_platform: str,
    ) -> ApplyResult:
        """
        Deprecated safety stub.

        Older code retried with patched browsers or solver services. We keep this
        method only so any accidental call becomes a human checkpoint instead.
        """
        log.set_status("waiting_captcha")
        message = (
            "CAPTCHA detected. Automatic bypass is disabled; solve it in the visible browser, "
            "then use Resume Application if the task timed out."
        )
        log.error(message)
        return ApplyResult(
            success=False,
            questions_needing_human=[message],
            reason="needs_human",
            ats_platform=ats_platform,
            error=message,
            url=url,
        )

    async def _wait_for_success(self, page: Page, timeout_s: int = 30) -> bool:
        """Monitor DOM for any known success message."""
        phrases = json.dumps([
            "your application was successfully submitted", "application was successfully submitted",
            "thanks for applying", "thank you for applying", "your application has been received",
            "we received your application", "application submitted", "application received",
            "successfully submitted", "application complete", "you've applied",
        ])
        try:
            await page.wait_for_function(
                f"() => {{ const t=(document.body?.innerText||'').toLowerCase(); return {phrases}.some(p=>t.includes(p)); }}",
                timeout=timeout_s * 1000,
            )
            return True
        except Exception:
            return False

    async def _wait_for_human_submit(self, page: Page, timeout_s: int = 600) -> bool:
        """Monitor DOM for any known success message (used by _wait_for_manual_review)."""
        return await self._wait_for_success(page, timeout_s=timeout_s)

    async def upload_files_to_page(self, page: Page, profile: dict) -> None:
        await self.form_filler.upload_files(page, profile)

    # -------------------------------------------------------------------

    # GraphQL mutation extracted from Ashby's browser JS (confirmed via console interception).
    _ASHBY_SUBMIT_MUTATION = (
        "mutation ApiSubmitSingleApplicationFormAction("
        "$organizationHostedJobsPageName:String!,$jobPostingId:String!,"
        "$formRenderIdentifier:String!,$formDefinitionIdentifier:String,"
        "$actionIdentifier:String!,$recaptchaToken:String!,"
        "$sourceAttributionCode:String,$viewedAutomatedProcessingLegalNoticeRuleId:String,"
        "$deviceFingerprint:String,$applicationRequestId:String){"
        "submitApplicationFormAction:submitSingleApplicationFormAction("
        "organizationHostedJobsPageName:$organizationHostedJobsPageName,"
        "jobPostingId:$jobPostingId,formRenderIdentifier:$formRenderIdentifier,"
        "formDefinitionIdentifier:$formDefinitionIdentifier,"
        "actionIdentifier:$actionIdentifier,recaptchaToken:$recaptchaToken,"
        "sourceAttributionCode:$sourceAttributionCode,"
        "viewedAutomatedProcessingLegalNoticeRuleId:$viewedAutomatedProcessingLegalNoticeRuleId,"
        "deviceFingerprint:$deviceFingerprint,applicationRequestId:$applicationRequestId){"
        "applicationFormResult{...on FormRender{id errorMessages formErrors{message fieldEntryId __typename}"
        "sourceFormDefinitionId __typename}...on FormSubmitSuccess{_ __typename}__typename}"
        "messages{blockMessageForCandidateHtml __typename}__typename}}"
    )

    async def _apply_ashby(
        self,
        page: Page,
        url: str,
        profile_context: dict,
        qa_engine: QAEngine,
        log: AuditLog,
    ) -> ApplyResult:
        """
        Ashby handler:
        1. Try headless — fast, no window.
        2. reCAPTCHA score too low → human-in-the-loop: open visible browser,
           pre-fill form, ask user to click Submit Application.
           We monitor for success and report back automatically.
        """
        result = await self._ashby_attempt(
            page=page, url=url, profile_context=profile_context,
            qa_engine=qa_engine, log=log, headless=True,
        )

        if result.error and "RECAPTCHA_SCORE_BELOW_THRESHOLD" in result.error:
            message = (
                "Ashby reCAPTCHA score check requires human submission. "
                "Open the visible browser flow and submit after completing the challenge."
            )
            log.set_status("waiting_captcha")
            log.error(message)
            return ApplyResult(
                success=False,
                screenshot_path=result.screenshot_path,
                questions_needing_human=[message],
                reason="needs_human",
                ats_platform="ashby",
                error=message,
                url=result.url or url,
            )

        return result

    async def _ashby_attempt(
        self,
        page: Page,
        url: str,
        profile_context: dict,
        qa_engine: QAEngine,
        log: AuditLog,
        headless: bool = True,
    ) -> ApplyResult:
        """Single Ashby apply attempt on an already-navigated page."""
        # Capture form-render identifiers from the GraphQL response
        form_render: dict = {}

        async def _on_response(resp: Any) -> None:
            try:
                if "non-user-graphql" not in resp.url:
                    return
                body = await resp.json()
                data = body.get("data") or {}
                for val in data.values():
                    if isinstance(val, dict) and "formControls" in val and val.get("id"):
                        form_render["id"] = val["id"]
                        form_render["sourceFormDefinitionId"] = val.get("sourceFormDefinitionId")
                        controls = val.get("formControls") or []
                        if controls:
                            form_render["actionIdentifier"] = controls[0].get("identifier")
                        break
            except Exception:
                pass

        page.on("response", _on_response)
        # Give the page a moment so the form-render response can be captured
        await page.wait_for_timeout(1500)

        # Parse URL components
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        org_name = path_parts[0] if path_parts else ""
        job_posting_id = path_parts[1] if len(path_parts) > 1 else ""
        source_code = next(
            (p.split("=", 1)[1] for p in (parsed.query or "").split("&") if p.startswith("utm_source=")),
            None,
        )

        # Fill the form
        await self._fill_known_ats(page, "ashby", profile_context)
        await self._fill_ashby_dropdowns(page, profile_context, log)

        has_cl = await self.form_filler.has_cover_letter_field(page)
        ctx = {**profile_context, "cover_letter_path": profile_context.get("cover_letter_path") if has_cl else None}
        if not has_cl:
            log.info("No cover letter upload field detected — skipping cover letter PDF upload")

        filled = await self.form_filler.detect_and_fill_form(page, ctx, qa_engine)

        # Ashby autofills Name/Email/etc. from the uploaded resume via an async API call.
        # Wait for the autofill to settle, then do a second fill pass to catch newly
        # populated fields and fill any that autofill didn't cover.
        had_upload = any(e.get("event") == "upload" for e in (log.entries or []))
        if had_upload:
            log.info("Waiting for Ashby resume autofill to settle…")
            await page.wait_for_timeout(5000)
            log.info("Ashby autofill settled — running second fill pass (no re-upload).")
            # Mark ALL file inputs as already uploaded so second pass skips them
            await page.evaluate("""
                () => document.querySelectorAll('input[type="file"]').forEach(el => {
                    el.dataset.jobpilotUploaded = 'true';
                })
            """)
            # Re-run known-field fill in case autofill left some empty that we can supply from profile
            await self._fill_known_ats(page, "ashby", profile_context)
            await self._fill_ashby_dropdowns(page, profile_context, log)
            filled2 = await self.form_filler.detect_and_fill_form(page, ctx, qa_engine)
            # Use whichever pass has fewer unanswered questions
            if len(filled2.questions_needing_human) < len(filled.questions_needing_human):
                filled = filled2
            else:
                from dataclasses import replace as _dc_replace
                combined = dedupe(filled.questions_needing_human + filled2.questions_needing_human)
                filled = _dc_replace(filled, questions_needing_human=combined)

        if filled.questions_needing_human:
            screenshot_path = await self._safe_screenshot(page, url, "needs_human")
            log.info(
                "Ashby form has unanswered questions — stored in Q&A memory. "
                "Answer them in the Q&A tab and re-trigger this application."
            )
            return ApplyResult(
                success=False,
                screenshot_path=screenshot_path,
                reason="needs_human",
                ats_platform="ashby",
                questions_needing_human=dedupe(filled.questions_needing_human),
                error="Missing answers for: " + "; ".join(dedupe(filled.questions_needing_human)[:3]),
                url=page.url,
            )

        screenshot_path = await self._safe_screenshot(page, url, "preflight")
        if settings.apply_require_human_review:
            return await self._wait_for_manual_review(
                page=page,
                screenshot_path=screenshot_path,
                questions_encountered=[],
                ats_platform="ashby",
                log=log,
            )

        log.submit(page.url)

        if not form_render.get("id"):
            err = "Form render identifiers not captured — cannot submit via Ashby API"
            log.error(err)
            return ApplyResult(success=False, screenshot_path=screenshot_path, reason="failed",
                               ats_platform="ashby", error=err, url=page.url)

        mode = "visible" if not headless else "headless"
        log.info(f"Submitting via Ashby GraphQL API [{mode}] (formRender={form_render['id'][:8]}…)")

        # Build the JS payload using only the token available in the real page context.
        # No third-party CAPTCHA solvers or bypass flows are used.
        token_js = """
            await (async () => {
                try {
                    await new Promise(r => window.grecaptcha.ready(r));
                    const cfg = (window.___grecaptcha_cfg || {}).clients || {};
                    let siteKey = '';
                    const findKey = (obj, d) => {
                        if (d > 6 || siteKey) return;
                        for (const v of Object.values(obj || {})) {
                            if (typeof v === 'string' && /^6L[A-Za-z0-9_-]{38}/.test(v)) { siteKey = v; return; }
                            if (typeof v === 'object' && v !== null) findKey(v, d + 1);
                        }
                    };
                    findKey(cfg, 0);
                    return siteKey ? await window.grecaptcha.execute(siteKey, {action: 'submit_application'}) : '';
                } catch(e) { return ''; }
            })()
        """

        api_result = await page.evaluate(
            f"""
            async ([orgName, jobPostingId, formRenderIdentifier, formDefinitionIdentifier,
                    actionIdentifier, sourceCode, mutation]) => {{
                const recaptchaToken = {token_js};
                let deviceFingerprint = null;
                try {{ deviceFingerprint = window.__ashbyDeviceFingerprint || window.__df || null; }} catch(e) {{}}
                const resp = await fetch(
                    '/api/non-user-graphql?op=ApiSubmitSingleApplicationFormAction',
                    {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            operationName: 'ApiSubmitSingleApplicationFormAction',
                            variables: {{
                                organizationHostedJobsPageName: orgName,
                                jobPostingId: jobPostingId,
                                formRenderIdentifier: formRenderIdentifier,
                                formDefinitionIdentifier: formDefinitionIdentifier,
                                actionIdentifier: actionIdentifier,
                                recaptchaToken: recaptchaToken,
                                sourceAttributionCode: sourceCode,
                                deviceFingerprint: deviceFingerprint,
                                applicationRequestId: null,
                                viewedAutomatedProcessingLegalNoticeRuleId: null,
                            }},
                            query: mutation,
                        }}),
                    }}
                );
                return await resp.json();
            }}
            """,
            [org_name, job_posting_id, form_render["id"], form_render.get("sourceFormDefinitionId"),
             form_render.get("actionIdentifier"), source_code, self._ASHBY_SUBMIT_MUTATION],
        )

        return self._parse_ashby_api_result(api_result, screenshot_path, page.url, log)

    async def _wait_for_manual_review(
        self,
        page: Page,
        screenshot_path: str | None,
        questions_encountered: list[str],
        ats_platform: str,
        log: AuditLog,
        questions_needing_human: list[str] | None = None,
    ) -> ApplyResult:
        timeout_s = settings.application_review_timeout_seconds
        log.set_status("waiting_review")
        log.info(
            "Browser is pre-filled and paused. Review or edit the application in the visible browser, "
            f"then click Submit within {timeout_s} seconds."
        )
        success = await self._wait_for_human_submit(page, timeout_s=timeout_s)
        post_submit_screenshot = await self._safe_screenshot(page, page.url, "post_review")
        if success:
            log.submit(page.url)
            log.info("Human submitted the application manually — confirmed via success page detection.")
            log.set_status("applied")
            return ApplyResult(
                success=True,
                screenshot_path=post_submit_screenshot or screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                ats_platform=ats_platform,
                url=page.url,
            )
        message = "Timed out waiting for manual review submit. Use Resume Application to try again."
        log.error(message)
        return ApplyResult(
            success=False,
            screenshot_path=post_submit_screenshot or screenshot_path,
            questions_encountered=dedupe(questions_encountered),
            questions_needing_human=questions_needing_human or [message],
            reason="needs_human",
            ats_platform=ats_platform,
            error=message,
            url=page.url,
        )

    async def _wait_for_captcha_review(
        self,
        page: Page,
        screenshot_path: str | None,
        questions_encountered: list[str],
        ats_platform: str,
        captcha_type: str,
        log: AuditLog,
    ) -> ApplyResult:
        timeout_s = settings.application_review_timeout_seconds
        log.set_status("waiting_captcha")
        if settings.apply_browser_headless:
            log.info("CAPTCHA appeared while browser is headless; set APPLY_BROWSER_HEADLESS=false for interactive solving.")
        log.info(
            f"{captcha_type} detected. Solve it in the visible browser, make any edits needed, "
            f"then submit within {timeout_s} seconds."
        )
        success = await self._wait_for_human_submit(page, timeout_s=timeout_s)
        post_submit_screenshot = await self._safe_screenshot(page, page.url, "post_captcha")
        if success:
            log.info("Human CAPTCHA completion detected; application submitted successfully.")
            log.set_status("applied")
            return ApplyResult(
                success=True,
                screenshot_path=post_submit_screenshot or screenshot_path,
                questions_encountered=dedupe(questions_encountered),
                ats_platform=ats_platform,
                url=page.url,
            )

        message = (
            f"{captcha_type} needs human completion. The task timed out before a submission "
            "confirmation was detected."
        )
        log.error(message)
        return ApplyResult(
            success=False,
            screenshot_path=post_submit_screenshot or screenshot_path,
            questions_encountered=dedupe(questions_encountered),
            questions_needing_human=[message],
            reason="needs_human",
            ats_platform=ats_platform,
            error=message,
            url=page.url,
        )

    def _parse_ashby_api_result(
        self, api_result: dict, screenshot_path: str | None, current_url: str, log: AuditLog
    ) -> ApplyResult:
        """Interpret the GraphQL response from Ashby's submit endpoint."""
        # Top-level GraphQL errors (e.g. RECAPTCHA_SCORE_BELOW_THRESHOLD)
        gql_errors = api_result.get("errors") or []
        for err in gql_errors:
            ext = err.get("extensions") or {}
            error_type = ext.get("ashbyErrorType", "")
            message = err.get("message", str(err))
            log.error(f"Ashby API error [{error_type}]: {message}")
            return ApplyResult(
                success=False,
                screenshot_path=screenshot_path,
                reason="failed",
                ats_platform="ashby",
                error=f"[{error_type}] {message}",
                url=current_url,
            )

        action = (api_result.get("data") or {}).get("submitApplicationFormAction") or {}
        form_result = action.get("applicationFormResult") or {}
        typename = form_result.get("__typename", "")

        if typename == "FormSubmitSuccess":
            log.info("Ashby API: FormSubmitSuccess — application submitted.")
            return ApplyResult(success=True, screenshot_path=screenshot_path, ats_platform="ashby", url=current_url)

        if typename == "FormRender":
            errors = form_result.get("errorMessages") or []
            form_errors = [e.get("message", "") for e in (form_result.get("formErrors") or [])]
            all_errors = dedupe([e for e in errors + form_errors if e])
            err_str = "; ".join(all_errors) or "Form returned validation errors"
            log.error(f"Ashby form errors: {err_str}")
            return ApplyResult(
                success=False, screenshot_path=screenshot_path,
                questions_needing_human=all_errors, reason="needs_human",
                ats_platform="ashby", error=err_str, url=current_url,
            )

        messages = action.get("messages") or []
        block = next((m.get("blockMessageForCandidateHtml") for m in messages if m.get("blockMessageForCandidateHtml")), None)
        if block:
            log.error(f"Ashby blocked submission: {block}")
            return ApplyResult(success=False, screenshot_path=screenshot_path, reason="failed",
                               ats_platform="ashby", error=f"Blocked: {block}", url=current_url)

        raw = json.dumps(api_result)[:500]
        log.error(f"Ashby API unexpected response: {raw}")
        return ApplyResult(success=False, screenshot_path=screenshot_path, reason="failed",
                           ats_platform="ashby", error=f"Unexpected Ashby API response: {raw}", url=current_url)

    async def _fill_ashby_dropdowns(self, page: Page, profile: dict, log: AuditLog) -> None:
        """Fill Ashby's custom React fields that are invisible to query_selector_all."""
        preferences = profile.get("application_preferences_json") or {}

        field_values: dict[str, str] = {}

        location = profile.get("location")
        if location:
            field_values["Location"] = location

        remote_pref = (preferences.get("remote_preference") or "").lower()
        if "remote" in remote_pref:
            field_values["Location Type"] = "Remote"
        elif "hybrid" in remote_pref:
            field_values["Location Type"] = "Hybrid"
        else:
            field_values["Location Type"] = "On-site"

        emp_types = preferences.get("employment_types") or []
        if emp_types:
            raw = str(emp_types[0]).lower()
            if "part" in raw:
                field_values["Employment Type"] = "Part time"
            elif "contract" in raw or "contractor" in raw:
                field_values["Employment Type"] = "Contract"
            elif "intern" in raw:
                field_values["Employment Type"] = "Internship"
            else:
                field_values["Employment Type"] = "Full time"

        for label_text, value in field_values.items():
            filled = await self._fill_ashby_field(page, label_text, value, log)
            if not filled:
                log.skip(label_text, f"Ashby field '{label_text}' not found or not interactive — tried all strategies")

    async def _fill_ashby_field(self, page: Page, label_text: str, value: str, log: AuditLog) -> bool:
        """Try every strategy to fill one Ashby custom field."""

        # Strategy 1: Direct pill/radio button — Ashby renders Employment Type and Location Type
        # as visible clickable buttons, not a dropdown. Just click the button with the right text.
        for selector in (
            f'button:has-text("{value}")',
            f'[role="radio"]:has-text("{value}")',
            f'[role="tab"]:has-text("{value}")',
        ):
            try:
                btn = page.locator(selector).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000)
                    log.fill(label_text, value, "ashby-pill")
                    return True
            except Exception:
                continue

        # Strategy 2: JS DOM walker — find a text node matching the label, walk up to find
        # the field container, then click the first interactive child inside it.
        clicked = await page.evaluate(
            """([labelText]) => {
                function findLabelNode(text) {
                    const iter = document.createNodeIterator(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    while ((node = iter.nextNode())) {
                        if (node.textContent.trim() === text) return node.parentElement;
                    }
                    return null;
                }
                const labelEl = findLabelNode(labelText);
                if (!labelEl) return false;

                // Walk up at most 6 levels to find a container that has an interactive child
                let container = labelEl;
                for (let i = 0; i < 6; i++) {
                    container = container.parentElement;
                    if (!container) break;
                    const sel = '[role="combobox"],[role="listbox"],select,button:not([type="submit"]),[tabindex="0"]';
                    const interactive = Array.from(container.querySelectorAll(sel))
                        .find(el => el !== labelEl && el.offsetParent !== null);
                    if (interactive) { interactive.click(); return true; }
                }
                return false;
            }""",
            [label_text],
        )

        if clicked:
            await page.wait_for_timeout(500)
            # Pick option from any open listbox/popover
            for opt_selector in (
                f'[role="option"]:has-text("{value}")',
                f'li:has-text("{value}")',
                f'[data-value="{value}"]',
            ):
                try:
                    option = page.locator(opt_selector).first
                    if await option.count():
                        await option.click(timeout=2000)
                        log.fill(label_text, value, "ashby-dropdown")
                        return True
                except Exception:
                    continue
            await page.keyboard.press("Escape")

        # Strategy 3: get_by_role combobox named after the label
        try:
            trigger = page.get_by_role("combobox", name=label_text).first
            if await trigger.count():
                await trigger.click(timeout=2000)
                await page.wait_for_timeout(400)
                option = page.locator(f'[role="option"]:has-text("{value}")').first
                if await option.count():
                    await option.click(timeout=2000)
                    log.fill(label_text, value, "ashby-dropdown")
                    return True
                await page.keyboard.press("Escape")
        except Exception:
            pass

        # Strategy 4: Location autocomplete — type and pick from suggestions
        if label_text.lower() == "location":
            try:
                inp = page.locator('input[placeholder*="location" i], input[aria-label*="location" i]').first
                if not await inp.count():
                    inp = page.get_by_placeholder("City, State or Zip").first
                if await inp.count():
                    await inp.click(timeout=2000)
                    await inp.fill(value)
                    await page.wait_for_timeout(800)
                    suggestion = page.locator(f'[role="option"]:has-text("{value.split(",")[0]}")').first
                    if await suggestion.count():
                        await suggestion.click(timeout=2000)
                        log.fill(label_text, value, "ashby-location-autocomplete")
                        return True
            except Exception:
                pass

        return False

    async def _validation_errors(self, page: Page) -> list[str]:
        """Return visible validation error messages after a submit attempt."""
        await page.wait_for_timeout(800)
        errors: list[str] = []
        selectors = (
            '[aria-invalid="true"]',
            '[class*="error"]:visible',
            '[class*="invalid"]:visible',
            '[role="alert"]:visible',
        )
        seen: set[str] = set()
        for selector in selectors:
            for element in await page.query_selector_all(selector):
                try:
                    label = (await element.inner_text()).strip()
                    if label and label not in seen and len(label) < 200:
                        seen.add(label)
                        errors.append(label)
                except Exception:
                    continue
        return errors[:10]  # cap at 10

    async def _resolve_greenhouse_url(self, page: Page, original_url: str, log: AuditLog) -> str | None:
        """
        When a company embeds Greenhouse (via ?gh_jid= or an iframe), resolve the
        canonical application URL: https://job-boards.greenhouse.io/{company}/jobs/{id}
        """
        from urllib.parse import urlparse, parse_qs, urlencode

        def embed_to_direct(embed_src: str) -> str | None:
            """
            Convert Greenhouse embed URL to direct application URL.
            Input:  https://job-boards.greenhouse.io/embed/job_app?for=avathon&token=4655249005&...
            Output: https://job-boards.greenhouse.io/avathon/jobs/4655249005
            """
            p = urlparse(embed_src)
            qs = parse_qs(p.query)
            company = (qs.get("for") or [None])[0]
            token = (qs.get("token") or [None])[0]
            if company and token:
                # Use same host (job-boards.greenhouse.io or boards.greenhouse.io)
                host = p.netloc or "job-boards.greenhouse.io"
                return f"https://{host}/{company}/jobs/{token}"
            return None

        # Strategy 1: live Greenhouse iframe already loaded
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if "greenhouse.io" in frame.url or "grnh.se" in frame.url:
                if "embed/job_app" in frame.url:
                    direct = embed_to_direct(frame.url)
                    if direct:
                        log.info(f"Found Greenhouse embed frame, using direct URL: {direct[:80]}")
                        return direct
                # Already a direct frame URL (e.g. /avathon/jobs/...)
                if "/jobs/" in frame.url:
                    log.info(f"Found Greenhouse frame URL: {frame.url[:80]}")
                    return frame.url

        # Strategy 2: iframe src in DOM (not yet loaded)
        iframe_src = await page.evaluate("""
            () => {
                const iframes = Array.from(document.querySelectorAll('iframe'));
                const gh = iframes.find(f => f.src && (
                    f.src.includes('greenhouse.io') || f.src.includes('grnh.se')
                ));
                return gh ? gh.src : null;
            }
        """)
        if iframe_src:
            if "embed/job_app" in iframe_src:
                direct = embed_to_direct(iframe_src)
                if direct:
                    log.info(f"Resolved from iframe DOM src: {direct[:80]}")
                    return direct
            if "/jobs/" in iframe_src:
                log.info(f"Greenhouse iframe direct URL: {iframe_src[:80]}")
                return iframe_src

        # Strategy 3: build from gh_jid URL param + company slug from any Greenhouse link
        parsed = urlparse(original_url)
        qs = parse_qs(parsed.query)
        gh_jid = (qs.get("gh_jid") or [None])[0]
        if gh_jid:
            company_slug = await page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll(
                        'a[href*="greenhouse.io"], a[href*="grnh.se"], a[href*="job-boards.greenhouse"]'
                    ));
                    for (const l of links) {
                        const m = l.href.match(/greenhouse\\.io\\/([^/?#]+)\\/jobs/);
                        if (m) return m[1];
                    }
                    return null;
                }
            """)
            if company_slug:
                direct = f"https://job-boards.greenhouse.io/{company_slug}/jobs/{gh_jid}"
                log.info(f"Constructed from gh_jid + page links: {direct}")
                return direct

        return None

    async def _dismiss_cookie_consent(self, page: Page, log: AuditLog) -> None:
        """
        Dismiss cookie/GDPR consent banners.
        Uses JS evaluation so it works even when element-level visibility checks fail.
        """
        # JS-first: find and click any accept/agree button inside a consent overlay
        dismissed = await page.evaluate("""
            () => {
                // Common cookie consent accept selectors
                const patterns = [
                    '#onetrust-accept-btn-handler',
                    '#accept-recommended-btn-handler',
                    '.onetrust-accept-btn-handler',
                    '[data-qa="consent-modal-accept"]',
                    '.cc-accept',
                    '.cc-btn.cc-allow',
                    '#CybotCookiebotDialogBodyButtonAccept',
                ];
                for (const sel of patterns) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) { el.click(); return sel; }
                }
                // Text-based search in visible overlay buttons
                const texts = ['Accept all', 'Accept All', 'Accept Cookies', 'Accept cookies',
                               'Accept', 'I Accept', 'I agree', 'Agree', 'Got it', 'Allow all',
                               'Allow All', 'Allow', 'OK', 'Consent'];
                const btns = Array.from(document.querySelectorAll('button, a[role="button"]'));
                for (const text of texts) {
                    const btn = btns.find(b =>
                        b.offsetParent !== null &&
                        b.innerText.trim().toLowerCase() === text.toLowerCase()
                    );
                    if (btn) { btn.click(); return `text:${text}`; }
                }
                return null;
            }
        """)
        if dismissed:
            log.info(f"Dismissed consent popup via JS: {dismissed}")
            await page.wait_for_timeout(800)
            return

        # Playwright fallback for shadow DOM or slow-loading banners
        for sel in (
            "#onetrust-accept-btn-handler", "#accept-recommended-btn-handler",
            '[data-qa="consent-modal-accept"]', '.consent-modal button',
            'button:has-text("Accept all")', 'button:has-text("Accept All")',
            'button:has-text("Accept Cookies")', 'button:has-text("Accept")',
            'button:has-text("Allow all")', 'button:has-text("I agree")',
            'button:has-text("Got it")', 'button:has-text("OK")',
        ):
            try:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_visible(timeout=800):
                    await btn.click(timeout=3000)
                    log.info(f"Dismissed consent popup via Playwright: {sel}")
                    await page.wait_for_timeout(600)
                    return
            except Exception:
                continue

    async def _is_greenhouse_application_form(self, page: Page) -> bool:
        """Return True if the current page contains the Greenhouse application form fields."""
        try:
            # The application form has an #application div or known field IDs
            found = await page.evaluate("""
                () => !!(
                    document.querySelector('#application_form, #application, form#new_application') ||
                    document.querySelector('#first_name, #last_name, #email') ||
                    document.querySelector('input[name*="first_name"], input[name*="email"]')
                )
            """)
            return bool(found)
        except Exception:
            return False

    async def _auto_accept_consent(self, page: Page, log: AuditLog) -> None:
        """Auto-tick any unchecked consent/terms/privacy checkboxes on a page."""
        consent_keywords = (
            "consent", "terms", "privacy", "agree", "gdpr", "authorize",
            "process", "allow", "acknowledge", "accept",
        )
        try:
            checkboxes = await page.query_selector_all('input[type="checkbox"]:visible')
            for cb in checkboxes:
                try:
                    if await cb.is_checked():
                        continue
                    label_text = (await self.form_filler.extract_label_text(cb)).lower()
                    if any(kw in label_text for kw in consent_keywords):
                        await cb.check(timeout=3000)
                        log.fill(label_text[:60], "checked", "consent-checkbox")
                except Exception:
                    continue
        except Exception:
            pass

    async def _auto_accept_consent_frame(self, frame: Any, log: AuditLog) -> None:
        """Same as _auto_accept_consent but operates on a frame."""
        consent_keywords = (
            "consent", "terms", "privacy", "agree", "gdpr", "authorize",
            "process", "allow", "acknowledge", "accept",
        )
        try:
            checkboxes = await frame.query_selector_all('input[type="checkbox"]:visible')
            for cb in checkboxes:
                try:
                    if await cb.is_checked():
                        continue
                    label_text = (await self.form_filler.extract_label_text(cb)).lower()
                    if any(kw in label_text for kw in consent_keywords):
                        await cb.check(timeout=3000)
                        log.fill(label_text[:60], "checked", "consent-checkbox")
                except Exception:
                    continue
        except Exception:
            pass

    def detect_ats_type(self, url: str, page_source: str) -> str:
        haystack = f"{url}\n{page_source[:8000]}".lower()

        # Workday — check before generic "workday" substring to avoid false positives
        if "myworkdayjobs.com" in haystack or "wd1.myworkdayjobs" in haystack:
            return "workday"

        # Greenhouse — URL params (company-embedded), direct domain, or page source
        if (
            "gh_jid=" in url  # company page embedding Greenhouse (e.g. avathon.com?gh_jid=...)
            or "greenhouse.io" in haystack
            or "boards.greenhouse.io" in haystack
            or "grnh.se" in haystack          # Greenhouse short links
            or '"greenhouse"' in page_source  # Greenhouse JS bundle marker
        ):
            return "greenhouse"

        # Lever — URL params or domain
        if "lever.co" in haystack or "jobs.lever.co" in haystack or "lever_source" in url:
            return "lever"

        if "bamboohr" in haystack:
            return "bamboohr"

        if "ashbyhq.com" in haystack or '"ashby"' in page_source.lower():
            return "ashby"

        if "smartrecruiters.com" in haystack or "smartapply" in haystack:
            return "smartrecruiters"

        if "icims.com" in haystack:
            return "icims"

        return "generic"

    async def _requires_account_or_login(self, page: Page) -> bool:
        text = (await visible_text(page)).lower()
        form_fields = await page.query_selector_all("input:visible, select:visible, textarea:visible")
        password_fields = await page.query_selector_all('input[type="password"]:visible')
        account_markers = (
            "create an account",
            "create account",
            "sign in to apply",
            "log in to apply",
            "login to apply",
            "register to apply",
        )
        if password_fields and any(marker in text for marker in ("sign in", "log in", "create account", "password")):
            return True
        return len(form_fields) < 3 and any(marker in text for marker in account_markers)

    async def _fill_known_ats(self, page: Page, ats_type: str, profile: dict) -> int:
        if ats_type == "greenhouse":
            return await self._fill_greenhouse_fields(page, profile)

        selector_maps = {
            "ashby": {
                # Ashby uses _systemfield_ prefixed IDs for standard fields
                'input[data-testid="name-input"], input[name*="name" i][type="text"]:not([name*="last" i]):not([name*="first" i])': profile.get("full_name"),
                'input[data-testid="firstName-input"], input[name*="first" i]': first_name(profile),
                'input[data-testid="lastName-input"], input[name*="last" i]': last_name(profile),
                'input[data-testid="email-input"], input[name*="email" i], input[type="email"]': profile.get("email"),
                'input[data-testid="phone-input"], input[name*="phone" i], input[type="tel"]': profile.get("phone"),
                'input[data-testid="linkedin-input"], input[name*="linkedin" i]': profile.get("linkedin_url"),
                'input[data-testid="website-input"], input[name*="website" i], input[name*="portfolio" i]': profile.get("portfolio_url") or profile.get("github_url"),
            },
            "lever": {
                'input[name="name"]': profile.get("full_name"),
                'input[name="email"]': profile.get("email"),
                'input[name="phone"]': profile.get("phone"),
                'input[name="urls[LinkedIn]"]': profile.get("linkedin_url"),
                'input[name="urls[GitHub]"]': profile.get("github_url"),
                'input[name="urls[Portfolio]"]': profile.get("portfolio_url"),
            },
        }
        selector_map = selector_maps.get(ats_type, {})
        fields_filled = 0
        for selector, value in selector_map.items():
            if not value:
                continue
            element = await page.query_selector(selector)
            if not element:
                continue
            await self.form_filler.fill_field(page, element, str(value))
            fields_filled += 1
        return fields_filled

    async def _fill_greenhouse_fields(self, page: Page, profile: dict) -> int:
        """
        Fill Greenhouse form fields.
        Uses JavaScript to handle both native <select> elements and the custom Greenhouse
        dropdown components (which are styled on top of native selects or are React-managed).
        Logs what elements exist so selector issues are always visible in the audit trail.
        """
        log = self.form_filler.audit_log
        prefs = profile.get("application_preferences_json") or {}
        if not isinstance(prefs, dict):
            prefs = {}

        filled = 0

        # Build profile values needed for JS fill
        fn = first_name(profile) or ""
        ln = last_name(profile) or ""
        addr_parts = [
            profile.get("address_street"),
            profile.get("address_city") or (profile.get("location") or "").split(",")[0].strip(),
            profile.get("address_state") or (profile.get("location") or "").split(",")[-1].strip(),
            profile.get("address_zip"),
            profile.get("address_country") or "United States",
        ]
        full_address = ", ".join(p for p in addr_parts if p) or profile.get("location") or ""
        requires_sponsorship = prefs.get("requires_sponsorship", False)
        work_auth = (profile.get("work_authorization") or "").lower()
        salary_min = profile.get("salary_min")
        salary_max = profile.get("salary_max")
        salary_text = (
            f"${int(salary_min):,} - ${int(salary_max):,}" if salary_min and salary_max
            else (f"${int(salary_min):,}+" if salary_min else "Open to discussing compensation")
        )

        # --- Step 1: Dump ALL form elements so we can see what's on the page ---
        elements_dump = await page.evaluate("""
            () => {
                const els = document.querySelectorAll('input, select, textarea, [role="combobox"], [role="listbox"]');
                return Array.from(els).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    name: el.getAttribute('name') || '',
                    id: el.getAttribute('id') || '',
                    type: el.getAttribute('type') || '',
                    role: el.getAttribute('role') || '',
                    cls: (el.className || '').substring(0, 60),
                    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                }));
            }
        """)
        if log:
            summary = [f"{e['tag']}[{e['name'] or e['id'] or e['role'] or e['cls']}]"
                       for e in elements_dump if e['name'] or e['id']]
            log.info(f"DOM elements ({len(elements_dump)} total): {', '.join(summary[:25])}")

        # --- Step 2: Fill text/email/tel inputs via React-safe native setter ---
        text_map = {
            "job_application[first_name]": fn,
            "job_application[last_name]": ln,
            "job_application[email]": profile.get("email") or "",
            "job_application[phone]": profile.get("phone") or "",
            "job_application[location]": profile.get("location") or "",
            "job_application[linkedin_profile_url]": profile.get("linkedin_url") or "",
            "job_application[website]": profile.get("portfolio_url") or profile.get("github_url") or "",
        }
        js_text_fills = await page.evaluate("""
            (fieldMap) => {
                const results = [];
                const inputProto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                const textareaProto = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
                for (const [name, value] of Object.entries(fieldMap)) {
                    if (!value) continue;
                    const el = document.querySelector(`[name="${name}"]`);
                    if (!el) continue;
                    const setter = el.tagName === 'TEXTAREA' ? textareaProto.set : inputProto.set;
                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    results.push(name + ' = ' + value.substring(0, 40));
                }
                return results;
            }
        """, text_map)
        for item in js_text_fills:
            if log:
                log.fill(item.split(" = ")[0], item.split(" = ", 1)[-1], "greenhouse-js")
            filled += 1

        # --- Step 3: Fill textareas (mailing address, salary expectations, etc.) ---
        textarea_map = {
            "job_application[body]": full_address,  # sometimes used for address
        }
        # Try to find and fill any textarea visible on the page
        for ta_name, ta_val in textarea_map.items():
            if not ta_val:
                continue
            try:
                el = await page.query_selector(f'textarea[name="{ta_name}"]')
                if el:
                    await self.form_filler.fill_field(page, el, ta_val)
                    if log:
                        log.fill(ta_name, ta_val[:80], "greenhouse-textarea")
                    filled += 1
            except Exception:
                pass

        # Fill any OTHER textareas by their associated label
        all_textareas = await page.query_selector_all("textarea")
        for ta in all_textareas:
            try:
                ta_name = await ta.get_attribute("name") or ""
                if ta_name in textarea_map:
                    continue
                label_text = await self.form_filler.extract_label_text(ta)
                if not label_text:
                    continue
                value = self.form_filler.value_from_profile(label_text, profile)
                if value:
                    await self.form_filler.fill_field(page, ta, str(value))
                    if log:
                        log.fill(label_text, str(value)[:80], "greenhouse-textarea")
                    filled += 1
            except Exception:
                pass

        # --- Step 4: Fill ALL selects via JavaScript ---
        # This handles both visible and hidden native <select> elements, including
        # those managed by React / jQuery Chosen / Select2.
        select_results = await page.evaluate("""
            (params) => {
                const {workAuth, requiresSponsorship, location, fullAddress, salaryText} = params;
                const results = [];

                function setSelect(sel, targetText) {
                    // Try to find option matching targetText (case-insensitive substring)
                    const needle = targetText.toLowerCase();
                    let bestOpt = null;
                    for (const opt of sel.options) {
                        const t = opt.text.toLowerCase();
                        if (t === needle || t.includes(needle) || needle.includes(t.split(' ')[0])) {
                            bestOpt = opt;
                            break;
                        }
                    }
                    if (!bestOpt && sel.options.length > 0) {
                        // Try first matching word
                        const firstWord = needle.split(' ')[0];
                        for (const opt of sel.options) {
                            if (opt.text.toLowerCase().includes(firstWord)) {
                                bestOpt = opt;
                                break;
                            }
                        }
                    }
                    if (!bestOpt) return null;
                    // Use native React-compatible setter
                    try {
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            HTMLSelectElement.prototype, 'value').set;
                        nativeSetter.call(sel, bestOpt.value);
                    } catch(e) {
                        sel.value = bestOpt.value;
                    }
                    sel.dispatchEvent(new Event('input', {bubbles: true}));
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    return bestOpt.text;
                }

                function getLabel(el) {
                    // Get the human-readable label for a form element
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (lbl && lbl.innerText) return lbl.innerText.trim().toLowerCase();
                    }
                    const fieldset = el.closest('fieldset');
                    if (fieldset) {
                        const leg = fieldset.querySelector('legend');
                        if (leg) return leg.innerText.trim().toLowerCase();
                    }
                    // Walk up ancestors looking for a label-like element
                    let ancestor = el.parentElement;
                    for (let h = 0; h < 5 && ancestor; h++, ancestor = ancestor.parentElement) {
                        const label = ancestor.querySelector('label:not(:has(input)):not(:has(select))');
                        if (label && label.innerText) return label.innerText.trim().toLowerCase();
                    }
                    return (el.getAttribute('name') || el.id || '').toLowerCase();
                }

                for (const sel of document.querySelectorAll('select')) {
                    const lbl = getLabel(sel);
                    const name = (sel.name || '').toLowerCase();
                    let targetText = null;

                    if (name.includes('country') || lbl.includes('country')) {
                        targetText = 'United States';
                    } else if (lbl.includes('how did you') || lbl.includes('hear about') || lbl.includes('find this job') || name.includes('how_did')) {
                        targetText = 'linkedin';
                    } else if (lbl.includes('authorized') || lbl.includes('eligible to work') || lbl.includes('legally authorized')) {
                        targetText = 'yes';
                    } else if (lbl.includes('sponsor') || lbl.includes('visa')) {
                        targetText = requiresSponsorship ? 'yes' : 'no';
                    } else if (lbl.includes('commut') || lbl.includes('relocat') || lbl.includes('in person') || lbl.includes('on-site') || lbl.includes('onsite')) {
                        targetText = 'yes';
                    } else if (lbl.includes('background check') || lbl.includes('drug test') || lbl.includes('drug screen')) {
                        targetText = 'yes';
                    } else if (lbl.includes('gender') || lbl.includes('racial') || lbl.includes('ethnicity') || lbl.includes('ethnic') || lbl.includes('sexual orientation') || lbl.includes('transgender')) {
                        // EEO — prefer "decline" options
                        targetText = 'decline';
                        if (!setSelect(sel, targetText)) {
                            setSelect(sel, 'prefer not');
                        }
                        results.push(lbl + ' = (eeo decline)');
                        continue;
                    } else if (lbl.includes('veteran')) {
                        targetText = 'not a veteran';
                        if (!setSelect(sel, targetText)) setSelect(sel, 'no');
                        results.push(lbl + ' = (veteran no)');
                        continue;
                    } else if (lbl.includes('disability')) {
                        targetText = 'no';
                    }

                    if (targetText) {
                        const chosen = setSelect(sel, targetText);
                        results.push(lbl + ' = ' + (chosen || '(not found: ' + targetText + ')'));
                    } else if (sel.options.length > 0) {
                        results.push('SKIPPED: ' + lbl + ' (' + sel.options.length + ' options, name=' + (sel.name || '') + ')');
                    }
                }
                return results;
            }
        """, {
            "workAuth": work_auth,
            "requiresSponsorship": bool(requires_sponsorship),
            "location": profile.get("location") or "",
            "fullAddress": full_address,
            "salaryText": salary_text,
        })

        for item in select_results:
            if log:
                if item.startswith("SKIPPED:"):
                    log.info(f"Select not matched: {item[8:].strip()}")
                else:
                    log.fill(item.split(" = ")[0], item.split(" = ", 1)[-1], "greenhouse-select-js")
                    filled += 1

        # --- Step 5: Salary / compensation text input ---
        for sal_sel in (
            'input[name*="salary"], input[name*="compensation"], input[name*="rate"], input[id*="salary"]',
            'textarea[name*="salary"], textarea[id*="salary"]',
        ):
            try:
                el = await page.query_selector(sal_sel)
                if el:
                    await self.form_filler.fill_field(page, el, salary_text)
                    if log:
                        log.fill("Salary", salary_text, "greenhouse-known")
                    filled += 1
                    break
            except Exception:
                pass

        # --- Step 5b: Fill text/textarea fields by label using JavaScript ---
        # This catches any text fields with question-style labels the name-based approach misses
        # (e.g. "What is your full mailing address?", salary expectations, etc.)
        label_fill_results = await page.evaluate("""
            (params) => {
                const {fullAddress, salaryText, linkedIn} = params;
                const results = [];

                function getLabel(el) {
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (lbl && lbl.innerText) return lbl.innerText.trim().toLowerCase();
                    }
                    let ancestor = el.parentElement;
                    for (let h = 0; h < 5 && ancestor; h++, ancestor = ancestor.parentElement) {
                        const label = ancestor.querySelector('label');
                        if (label && !label.contains(el) && label.innerText)
                            return label.innerText.trim().toLowerCase();
                        // Or a paragraph/span/div as question text
                        for (const node of ancestor.querySelectorAll('p,span,div,legend')) {
                            if (node.innerText && node.innerText.includes('?') && !node.contains(el))
                                return node.innerText.trim().toLowerCase();
                        }
                    }
                    return (el.name || el.id || '').toLowerCase();
                }

                function setVal(el, value) {
                    try {
                        const proto = el.tagName === 'TEXTAREA'
                            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                        setter.call(el, value);
                    } catch(e) {
                        el.value = value;
                    }
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }

                const els = document.querySelectorAll(
                    'input[type="text"], input[type="url"], input:not([type]), textarea'
                );
                for (const el of els) {
                    if (el.value && el.value.trim()) continue;  // already filled
                    const lbl = getLabel(el);
                    if (!lbl) continue;

                    let value = null;
                    if (lbl.includes('mailing address') || lbl.includes('full address') || lbl.includes('street address')) {
                        value = fullAddress;
                    } else if (lbl.includes('salary') || lbl.includes('compensation') || lbl.includes('hourly rate') || lbl.includes('pay rate') || lbl.includes('total comp')) {
                        value = salaryText;
                    } else if (lbl.includes('linkedin') && !lbl.includes('github')) {
                        value = linkedIn;
                    }

                    if (value) {
                        setVal(el, value);
                        results.push(lbl.substring(0, 50) + ' = ' + value.substring(0, 40));
                    }
                }
                return results;
            }
        """, {
            "fullAddress": full_address,
            "salaryText": salary_text,
            "linkedIn": profile.get("linkedin_url") or "",
        })
        for item in label_fill_results:
            if log:
                log.fill(item.split(" = ")[0], item.split(" = ", 1)[-1], "greenhouse-label-js")
            filled += 1

        # --- Step 6: Consent checkboxes — check them all ---
        cb_results = await page.evaluate("""
            () => {
                const results = [];
                for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                    if (!cb.checked) {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                        results.push(cb.name || cb.id || 'checkbox');
                    }
                }
                return results;
            }
        """)
        for cb_name in cb_results:
            if log:
                log.fill(f"Checkbox: {cb_name}", "checked", "greenhouse-consent")
            filled += 1

        return filled

    def _application_frames(self, page: Page) -> list[Frame]:
        frames: list[Frame] = []
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            url = frame.url.lower()
            # Known ATS embed domains
            if any(token in url for token in (
                "greenhouse.io", "grnh.se",           # Greenhouse
                "lever.co",                            # Lever
                "workday", "myworkdayjobs",            # Workday
                "bamboohr",                            # BambooHR
                "ashbyhq.com",                         # Ashby
                "smartrecruiters.com",                 # SmartRecruiters
                "icims.com",                           # iCIMS
            )):
                frames.append(frame)
        return frames

    async def _visible_questions(self, page_or_frame) -> list[str]:
        questions: list[str] = []
        for selector in ("label:visible", "legend:visible", "h2:visible", "h3:visible"):
            for element in await page_or_frame.query_selector_all(selector):
                try:
                    text = (await element.inner_text()).strip()
                    if text:
                        questions.append(text)
                except Exception:
                    continue
        return questions

    async def _submit(self, page: Page) -> bool:
        # Give React a moment to process the last upload/fill and enable the submit button.
        await page.wait_for_timeout(2000)

        # Blur the currently focused element — this triggers any pending React validation
        # that gates whether the submit button is truly submittable.
        try:
            await page.evaluate("document.activeElement && document.activeElement.blur()")
        except Exception:
            pass
        await page.wait_for_timeout(500)

        submit_selectors = (
            'button:has-text("Submit application")',   # Ashby exact text first
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Apply now")',
            'button:has-text("Apply")',
            'button:has-text("Send application")',
        )

        for selector in submit_selectors:
            locator = page.locator(selector).last
            try:
                if not await locator.count():
                    continue
                # Scroll the button into view so click coordinates land correctly.
                await locator.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)

                # Check aria-disabled as well — React often uses this instead of HTML disabled.
                aria_disabled = await locator.get_attribute("aria-disabled")
                if aria_disabled == "true":
                    continue

                if not await locator.is_enabled():
                    continue

                # Primary: normal Playwright click.
                try:
                    await locator.click(timeout=3000)
                    return True
                except Exception:
                    pass

                # Fallback: force click bypasses overlay/pointer-event intercepts.
                try:
                    await locator.click(force=True, timeout=3000)
                    return True
                except Exception:
                    pass

                # Last resort: dispatch the click directly via JavaScript.
                clicked = await locator.evaluate("el => { el.click(); return true; }")
                if clicked:
                    return True

            except Exception:
                continue
        return False


    async def _confirm_success(self, page: Page) -> bool:
        success_phrases = [
            # Ashby — exact banner text observed in production
            "your application was successfully submitted",
            "application was successfully submitted",
            # Ashby variants
            "thanks for applying",
            "your application was submitted",
            "we've received your application",
            "application received",
            # Greenhouse / Lever
            "application submitted",
            "your application has been submitted",
            "your application has been received",
            "thank you for your application",   # Greenhouse embed post-redirect (e.g. Avathon)
            "we have received your application",
            "we received your application",
            # Generic
            "thank you for applying",
            "successfully submitted",
            "application complete",
            "you've applied",
            "successfully applied",
            "your application is complete",
            "application has been received",
        ]
        url_tokens = ["/confirmation", "/submitted", "/success", "/applied", "/thank"]

        phrases_json = json.dumps(success_phrases)
        url_tokens_json = json.dumps(url_tokens)

        # Single wait_for_function checks ALL phrases + URL at once — no sequential 500ms loops.
        # Waits up to 20 s — Greenhouse embeds can redirect the main page after code verification.
        try:
            await page.wait_for_function(
                f"""() => {{
                    const text = (document.body ? document.body.innerText : '').toLowerCase();
                    const url  = window.location.href.toLowerCase();
                    const phrases = {phrases_json};
                    const urlTokens = {url_tokens_json};
                    return phrases.some(p => text.includes(p)) ||
                           urlTokens.some(t => url.includes(t));
                }}""",
                timeout=20000,
            )
            return True
        except Exception:
            pass

        return False

    async def _screenshot(self, page: Page, url: str, suffix: str) -> str:
        parsed = urlparse(url)
        stem = safe_file_name(parsed.netloc or "generic_application")
        path = screenshots_path(f"{stem}_{suffix}.png")
        await page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def _safe_screenshot(self, page: Page, url: str, suffix: str) -> str | None:
        try:
            return await self._screenshot(page, url, suffix)
        except Exception:
            return None


def first_name(profile: dict) -> str | None:
    parts = str(profile.get("full_name") or "").split()
    return parts[0] if parts else None


def last_name(profile: dict) -> str | None:
    parts = str(profile.get("full_name") or "").split()
    return parts[-1] if parts else None
