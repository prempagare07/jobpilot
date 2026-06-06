from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from playwright.async_api import ElementHandle, Frame, Page

from backend.agents.qa_engine import QAEngine
from backend.services.apply_common import AuditLog

FillTarget = Page | Frame


@dataclass(frozen=True)
class FilledFormResult:
    fields_filled: int
    questions_needing_human: list[str] = field(default_factory=list)
    success: bool = True


class FormFiller:
    def __init__(self) -> None:
        self.audit_log: AuditLog | None = None

    async def fill_field(self, page: FillTarget, field: ElementHandle, value: str, _label: str | None = None) -> None:
        field_type = await self._field_type(field)
        normalized_value = str(value).strip()
        if not normalized_value and field_type not in {"checkbox", "radio"}:
            return

        # React Select / combobox — overlay div intercepts normal clicks
        if await self._is_react_select(field):
            await self._fill_react_select(page, field, normalized_value)
            if self.audit_log and _label:
                self.audit_log.fill(_label, normalized_value, "combobox")
            return

        if field_type in {"text", "email", "tel", "url", "number", "search", "textarea"}:
            await field.scroll_into_view_if_needed()
            try:
                await field.click(timeout=3000)
            except Exception:
                await field.evaluate("el => el.focus()")
            # Use fill() as the primary strategy — it's atomic and doesn't care about DOM stability.
            # Fall back to character-by-character type() only if fill() leaves the field empty
            # (some React inputs ignore programmatic fill events).
            await field.fill(normalized_value)
            actual = await field.evaluate("el => el.value")
            if not actual:
                for character in normalized_value:
                    await field.type(character, delay=random.uniform(30, 80))
        elif field_type == "select":
            await self._select_option(field, normalized_value)
        elif field_type in {"radio", "checkbox"}:
            await self._click_choice(page, field, normalized_value, field_type)

        await self._dispatch_events(page, field)
        if self.audit_log and _label:
            self.audit_log.fill(_label, normalized_value, field_type)

    async def detect_and_fill_form(
        self,
        page: FillTarget,
        profile: dict,
        qa_engine: QAEngine,
    ) -> FilledFormResult:
        fields_filled = 0
        questions_needing_human: list[str] = []
        context = {"profile": profile, "job": profile.get("job", {})}

        _RERENDER_TRIGGERS = {"select", "combobox"}
        requery_needed = False

        # Radio groups that have already been handled (by name attribute) to avoid double-processing.
        handled_radio_names: set[str] = set()

        form_fields = list(await page.query_selector_all("input:visible, select:visible, textarea:visible"))
        i = 0
        while i < len(form_fields):
            if requery_needed:
                new_fields = list(await page.query_selector_all("input:visible, select:visible, textarea:visible"))
                form_fields = new_fields
                requery_needed = False

            form_field = form_fields[i]
            i += 1
            label_text = ""
            try:
                if await self._should_skip_field(form_field):
                    continue

                field_type = await self._field_type(form_field)

                # --- Radio button groups ---
                # Treat all radios with the same name as ONE question.  Extract the parent
                # group label (not the individual option label) and click the right choice.
                if field_type == "radio":
                    radio_name = await form_field.get_attribute("name") or ""
                    if radio_name in handled_radio_names:
                        continue
                    handled_radio_names.add(radio_name)

                    group_label = await self._radio_group_label(form_field, page)
                    if not group_label:
                        continue

                    value = self.value_from_profile(group_label, profile)
                    if value is None and is_optional_sensitive_label(group_label):
                        if self.audit_log:
                            self.audit_log.skip(group_label, "optional/sensitive — skipped")
                        continue
                    if value is None:
                        answer = await qa_engine.answer(group_label, context=context)
                        if answer.source == "needs_human":
                            questions_needing_human.append(group_label)
                            if self.audit_log:
                                self.audit_log.skip(group_label, "needs human answer")
                            continue
                        value = answer.answer

                    if value and str(value).strip():
                        await self._click_choice(page, form_field, str(value), "radio")
                        fields_filled += 1
                        if self.audit_log:
                            self.audit_log.fill(group_label, str(value)[:80], "radio-group")
                    continue

                label_text = await self.extract_label_text(form_field)
                if not label_text:
                    continue

                value = self.value_from_profile(label_text, profile)
                if value is None and is_optional_sensitive_label(label_text):
                    if self.audit_log:
                        self.audit_log.skip(label_text, "optional/sensitive — skipped")
                    continue
                if value is None:
                    answer = await qa_engine.answer(label_text, context=context)
                    if answer.source == "needs_human":
                        questions_needing_human.append(label_text)
                        if self.audit_log:
                            self.audit_log.skip(label_text, "needs human answer")
                        continue
                    value = answer.answer

                if value is None or str(value).strip() == "":
                    if self.audit_log:
                        self.audit_log.skip(label_text, "no value available")
                    continue

                await self.fill_field(page, form_field, str(value), _label=label_text)
                fields_filled += 1

                if field_type in _RERENDER_TRIGGERS or await self._is_react_select(form_field):
                    await page.wait_for_timeout(800)
                    requery_needed = True

            except Exception as exc:
                err_str = str(exc)
                if self.audit_log:
                    self.audit_log.skip(label_text or "unknown", f"DOM detached or element error: {err_str.splitlines()[0]}")
                continue

        upload_count = await self.upload_files(page, profile)
        fields_filled += upload_count
        return FilledFormResult(
            fields_filled=fields_filled,
            questions_needing_human=questions_needing_human,
            success=not questions_needing_human,
        )

    async def has_cover_letter_field(self, page: FillTarget) -> bool:
        """Return True if the page has a visible file input for a cover letter."""
        file_inputs = await page.query_selector_all("input[type=file]")
        total = len(file_inputs)
        for index, file_input in enumerate(file_inputs):
            kind, _label = await self._file_input_kind(file_input, index, total)
            if kind == "cover":
                return True
        return False

    async def upload_files(self, page: FillTarget, profile: dict) -> int:
        resume_path = profile.get("resume_path") or profile.get("resume_file_path")
        cover_letter_path = profile.get("cover_letter_path")
        uploaded = 0
        file_inputs = await page.query_selector_all("input[type=file]")
        total_inputs = len(file_inputs)
        resume_uploaded = False

        for index, file_input in enumerate(file_inputs):
            if await self._already_uploaded(file_input):
                continue

            kind, label = await self._file_input_kind(file_input, index, total_inputs)
            if kind == "cover":
                path = cover_letter_path
            elif kind == "resume":
                if resume_uploaded:
                    continue
                path = resume_path
            elif total_inputs == 1 and resume_path:
                kind = "resume"
                path = resume_path
            else:
                if self.audit_log:
                    self.audit_log.skip(label or "file upload", "unknown file-upload purpose")
                continue

            if not path:
                continue
            await file_input.set_input_files(str(path))
            await self._mark_uploaded(file_input, kind)
            if kind == "resume":
                resume_uploaded = True
            uploaded += 1
            if self.audit_log:
                file_name = path.rsplit("/", 1)[-1] if "/" in path else path
                self.audit_log.upload(label or f"{kind} upload", file_name)
            await self._dispatch_events(page, file_input)
            # Some ATSes (Ashby, Greenhouse) parse the resume server-side after upload.
            # Give the page a moment to show the parsing indicator, then wait for it to disappear.
            await page.wait_for_timeout(1500)
            # Wait a fixed 3 seconds for the ATS to start the upload/parse, then
            # up to 20 more seconds watching for text-based parsing indicators.
            # We do NOT block on spinners — some ATS spinners never fully disappear.
            await page.wait_for_timeout(3000)
            parsing_detected = await page.evaluate("""
                () => {
                    const t = (document.body ? document.body.innerText : '').toLowerCase();
                    return t.includes('parsing resume')
                        || t.includes('processing resume')
                        || t.includes('autofilling');
                }
            """)
            if parsing_detected and self.audit_log:
                self.audit_log.info("Resume is being parsed by the ATS — waiting for parsing to complete…")
                try:
                    await page.wait_for_function(
                        """() => {
                            const t = (document.body ? document.body.innerText : '').toLowerCase();
                            return !t.includes('parsing resume')
                                && !t.includes('processing resume')
                                && !t.includes('autofilling');
                        }""",
                        timeout=20000,
                    )
                    if self.audit_log:
                        self.audit_log.info("Resume parsing complete — continuing form fill.")
                except Exception:
                    if self.audit_log:
                        self.audit_log.info("Resume parsing wait timed out — continuing anyway.")
        return uploaded

    async def _already_uploaded(self, file_input: ElementHandle) -> bool:
        try:
            return bool(await file_input.evaluate("el => el.dataset.jobpilotUploaded === 'true'"))
        except Exception:
            return False

    async def _mark_uploaded(self, file_input: ElementHandle, kind: str) -> None:
        try:
            await file_input.evaluate(
                """(el, kind) => {
                    el.dataset.jobpilotUploaded = 'true';
                    el.dataset.jobpilotUploadKind = kind;
                }""",
                kind,
            )
        except Exception:
            pass

    async def _file_input_kind(self, file_input: ElementHandle, index: int, total_inputs: int) -> tuple[str, str]:
        label = await self.extract_label_text(file_input)
        lower_label = normalize_label(label)

        attrs: list[str] = []
        for attr in ("name", "id", "aria-label", "placeholder", "title", "accept"):
            value = await file_input.get_attribute(attr)
            if value:
                attrs.append(value)
        attr_text = normalize_label(" ".join(attrs))

        cover_terms = ("cover letter", "cover_letter", "coverletter", "motivation letter")
        resume_terms = ("resume", "cv", "curriculum vitae", "resume/cv", "resume cv")

        attr_has_cover = any(term in attr_text for term in cover_terms)
        attr_has_resume = any(term in attr_text for term in resume_terms)
        label_has_cover = any(term in lower_label for term in cover_terms)
        label_has_resume = any(term in lower_label for term in resume_terms)

        if attr_has_cover:
            return "cover", label
        if attr_has_resume:
            return "resume", label
        if label_has_cover and not label_has_resume:
            return "cover", label
        if label_has_resume and not label_has_cover:
            return "resume", label

        # Some ATS containers mention both "Resume/CV" and "Cover Letter" around
        # multiple hidden inputs. In that case, input order is usually resume first.
        if label_has_cover and label_has_resume and total_inputs > 1:
            return ("resume" if index == 0 else "cover"), label

        return "unknown", label

    async def extract_label_text(self, field: ElementHandle) -> str:
        # Priority 1: human-readable text from DOM (explicit label, aria, neighbours)
        dom_label = await field.evaluate(
            """
            (el) => {
              const parts = [];
              if (el.id) {
                const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (explicit?.innerText) parts.push(explicit.innerText.trim());
              }
              const wrapped = el.closest("label");
              if (wrapped?.innerText) parts.push(wrapped.innerText.trim());
              const aria = el.getAttribute("aria-labelledby");
              if (aria) {
                for (const id of aria.split(/\\s+/)) {
                  const node = document.getElementById(id);
                  if (node?.innerText) parts.push(node.innerText.trim());
                }
              }
              // aria-label is always human-readable
              const ariaLabel = el.getAttribute("aria-label");
              if (ariaLabel) parts.push(ariaLabel.trim());
              let previous = el.previousElementSibling;
              let hops = 0;
              while (previous && hops < 2) {
                if (previous.innerText) parts.push(previous.innerText.trim());
                previous = previous.previousElementSibling;
                hops += 1;
              }
              const parent = el.parentElement;
              if (parent?.innerText) parts.push(parent.innerText.trim());
              // Use only the longest unique part to avoid duplication
              const seen = new Set();
              const unique = [];
              for (const p of parts) {
                const k = p.toLowerCase();
                if (!seen.has(k)) { seen.add(k); unique.push(p); }
              }
              return unique[0] || "";
            }
            """
        )
        if dom_label:
            return self._clean_label(dom_label)

        # Priority 2: placeholder or title (still human-readable hints)
        for attr in ("placeholder", "title"):
            value = await field.get_attribute(attr)
            if value and value.strip():
                return self._clean_label(value)

        # Priority 3: name attribute — clean it up before using (strip brackets/underscores)
        name = await field.get_attribute("name")
        if name:
            cleaned = re.sub(r"[\[\]]+", " ", name)
            cleaned = re.sub(r"[_]+", " ", cleaned).strip()
            return self._clean_label(cleaned)

        return ""

    def value_from_profile(self, label_text: str, profile: dict) -> str | None:
        label = normalize_label(label_text)
        full_name = str(profile.get("full_name") or "").strip()
        # Prefer explicit first/last name fields if stored; fall back to splitting full_name.
        explicit_first = str(profile.get("first_name") or "").strip()
        explicit_last = str(profile.get("last_name") or "").strip()
        name_parts = full_name.split()
        derived_first = explicit_first or (name_parts[0] if name_parts else "")
        derived_last = explicit_last or (name_parts[-1] if len(name_parts) > 1 else "")
        eeo = profile.get("eeo_json") or profile.get("eeo") or {}
        if not isinstance(eeo, dict):
            eeo = {}
        preferences = profile.get("application_preferences_json") or {}
        if not isinstance(preferences, dict):
            preferences = {}

        if any(token in label for token in ("first name", "given name")):
            return derived_first or None
        if any(token in label for token in ("last name", "surname", "family name")):
            return derived_last or None
        if re.search(r"\b(full )?name\b", label):
            return full_name or None
        if "email" in label:
            return profile.get("email")
        if "phone" in label or "mobile" in label:
            return profile.get("phone")
        if "linkedin" in label:
            return profile.get("linkedin_url")
        if "github" in label:
            return profile.get("github_url")
        if "portfolio" in label:
            return profile.get("portfolio_url") or profile.get("github_url")
        if "website" in label:
            return profile.get("portfolio_url")
        if "street" in label or "street address" in label or "home address" in label:
            return profile.get("address_street") or profile.get("location")
        if "city" in label and "state" not in label:
            return profile.get("address_city") or _city_from_location(profile.get("location"))
        if "state" in label or "province" in label:
            return profile.get("address_state") or _state_from_location(profile.get("location"))
        if "zip" in label or "postal" in label or "postcode" in label:
            return profile.get("address_zip")
        if "country" in label:
            return profile.get("address_country") or "United States"
        if "location" in label or "address" in label:
            return profile.get("address_city") or profile.get("location")
        if "years of experience" in label or "years experience" in label:
            years = profile.get("years_experience")
            return str(years) if years is not None else None
        if "legally authorized" in label or "authorized to work" in label or "eligible to work" in label:
            work_auth = str(profile.get("work_authorization") or "").lower()
            return "Yes" if work_auth else "Yes"  # default Yes; user can override via QA memory
        if "comfortable commuting" in label or "willing to commute" in label or "able to commute" in label:
            return "Yes"
        if "comfortable relocating" in label or "willing to relocate" in label:
            return "Yes" if profile.get("willing_to_relocate") else "No"
        if "sponsor" in label or "visa" in label or "sponsorship" in label:
            requires_sponsorship = preferences.get("requires_sponsorship")
            if isinstance(requires_sponsorship, bool):
                return "Yes" if requires_sponsorship else "No"
            work_auth = str(profile.get("work_authorization") or "").lower()
            if work_auth in {"us citizen", "gc"}:
                return "No"
            if work_auth in {"h1b", "opt", "cpt"}:
                return "Yes"
        if "start date" in label or "available to start" in label or "availability" in label:
            return preferences.get("earliest_start_date") or preferences.get("notice_period")
        if "location type" in label or "work type" in label or "work location type" in label:
            remote_pref = (preferences.get("remote_preference") or "").lower()
            if "remote" in remote_pref:
                return "Remote"
            if "hybrid" in remote_pref:
                return "Hybrid"
            return "On-site"
        if "remote" in label or "hybrid" in label or "onsite" in label or "work arrangement" in label:
            return preferences.get("remote_preference")
        if "employment type" in label or "full time" in label or "full-time" in label or "job type" in label:
            employment_types = preferences.get("employment_types")
            if isinstance(employment_types, list) and employment_types:
                return str(employment_types[0])
        if "department" in label or "team" in label:
            # Best-effort: infer from target roles or leave for QA engine
            target_roles = profile.get("target_roles_json") or []
            if target_roles:
                role = str(target_roles[0]).lower()
                if any(t in role for t in ("engineer", "developer", "software", "data", "ml", "ai")):
                    return "Engineering"
                if any(t in role for t in ("product", "pm", "manager")):
                    return "Product"
                if any(t in role for t in ("design", "ux", "ui")):
                    return "Design"
        if "gender" in label:
            return profile.get("gender") or eeo.get("gender")
        if "race" in label or "ethnicity" in label:
            return profile.get("race_ethnicity") or eeo.get("race_ethnicity")
        if "veteran" in label:
            return profile.get("veteran_status") or eeo.get("veteran_status")
        if "disability" in label:
            return profile.get("disability_status") or eeo.get("disability_status")
        return None

    async def _field_type(self, field: ElementHandle) -> str:
        tag_name = (await field.evaluate("(el) => el.tagName.toLowerCase()")).lower()
        if tag_name == "textarea":
            return "textarea"
        if tag_name == "select":
            return "select"
        input_type = (await field.get_attribute("type") or "text").lower()
        return input_type

    async def _should_skip_field(self, field: ElementHandle) -> bool:
        try:
            field_type = await self._field_type(field)
            if field_type in {"hidden", "submit", "button", "image", "reset", "password", "file"}:
                return True
            disabled = await field.is_disabled(timeout=3000)
            readonly = await field.get_attribute("readonly", timeout=3000)
            return bool(disabled or readonly)
        except Exception:
            return True  # stale handle — skip

    async def _select_option(self, field: ElementHandle, value: str) -> None:
        try:
            await field.select_option(label=value)
            return
        except Exception:
            pass
        try:
            await field.select_option(value=value)
            return
        except Exception:
            pass
        option_value = await field.evaluate(
            """
            (el, wanted) => {
              const norm = (s) => String(s || "").trim().toLowerCase();
              const target = norm(wanted);
              const option = Array.from(el.options).find((opt) => (
                norm(opt.textContent).includes(target) || target.includes(norm(opt.textContent)) ||
                norm(opt.value) === target
              ));
              return option?.value || "";
            }
            """,
            value,
        )
        if option_value:
            await field.select_option(value=str(option_value))

    async def _click_choice(
        self,
        page: FillTarget,
        field: ElementHandle,
        value: str,
        field_type: str,
    ) -> None:
        truthy = normalize_label(value) in {"true", "yes", "y", "1", "checked", "agree"}
        if field_type == "checkbox":
            try:
                checked = await field.is_checked(timeout=3000)
                if truthy and not checked:
                    await field.click(timeout=3000)
                elif not truthy and checked:
                    await field.click(timeout=3000)
            except Exception:
                pass
            return

        name = await field.get_attribute("name")
        candidates = await page.query_selector_all(f'input[type="radio"][name="{name}"]') if name else [field]
        target = normalize_label(value)
        for candidate in candidates:
            try:
                label = await self.extract_label_text(candidate)
                candidate_value = await candidate.get_attribute("value") or ""
                if target in normalize_label(f"{label} {candidate_value}"):
                    await candidate.click(timeout=3000)
                    return
            except Exception:
                continue
        try:
            await field.click(timeout=3000)
        except Exception:
            pass

    async def _is_react_select(self, field: ElementHandle) -> bool:
        role = await field.get_attribute("role") or ""
        if role != "combobox":
            return False
        cls = await field.get_attribute("class") or ""
        return "select__input" in cls or "react-select" in cls

    async def _fill_react_select(self, page: FillTarget, field: ElementHandle, value: str) -> None:
        # Focus without clicking so overlay divs don't block us
        await field.evaluate("el => { el.focus(); }")
        await page.wait_for_timeout(200)
        # Clear existing text then type search term
        await field.fill("")
        await field.type(value, delay=40)
        await page.wait_for_timeout(600)
        # Pick the first listbox option that contains the value
        for selector in (
            f'[role="option"]:has-text("{value}")',
            '[role="option"]:first-child',
            '[class*="select__option"]:first-child',
        ):
            try:
                option = await page.query_selector(selector)
                if option:
                    await option.click()
                    return
            except Exception:
                continue
        # Last resort — press Enter to accept whatever is highlighted
        await field.press("Enter")

    async def _dispatch_events(self, page: FillTarget, field: ElementHandle) -> None:
        await page.evaluate(
            """
            (el) => {
              el.dispatchEvent(new Event("input", { bubbles: true }));
              el.dispatchEvent(new Event("change", { bubbles: true }));
            }
            """,
            field,
        )

    async def _radio_group_label(self, one_radio: ElementHandle, page: FillTarget) -> str:
        """
        Return the human-readable GROUP label for a radio button by walking up the DOM
        to find the fieldset legend or the nearest ancestor question label — NOT the
        individual option text ("Yes" / "No").
        """
        label = await one_radio.evaluate(
            """
            (el) => {
              // Walk up the DOM looking for a legend, or an explicit label that precedes
              // the radio group container, or an aria-labelledby referent.
              const ariaLbl = el.getAttribute("aria-labelledby");
              if (ariaLbl) {
                for (const id of ariaLbl.split(/\\s+/)) {
                  const node = document.getElementById(id);
                  if (node?.innerText) return node.innerText.trim();
                }
              }
              // Fieldset > legend
              const fieldset = el.closest("fieldset");
              if (fieldset) {
                const legend = fieldset.querySelector("legend");
                if (legend?.innerText) return legend.innerText.trim();
              }
              // Walk up ancestors — stop at the nearest div/section/li that contains a
              // visible label-like element (not just the radio wrapper).
              let ancestor = el.parentElement;
              let hops = 0;
              while (ancestor && hops < 6) {
                // Look for a direct <label> child that is NOT wrapping a radio/checkbox
                for (const lbl of ancestor.querySelectorAll("label")) {
                  const inp = lbl.querySelector("input[type=radio],input[type=checkbox]");
                  if (!inp && lbl.innerText) return lbl.innerText.trim();
                }
                // Or a <p>/<span>/<div> that looks like a question (contains "?")
                for (const node of ancestor.querySelectorAll("p,span,div,h2,h3,h4")) {
                  if (node.innerText?.includes("?")) return node.innerText.trim();
                }
                ancestor = ancestor.parentElement;
                hops++;
              }
              return "";
            }
            """
        )
        return self._clean_label(label or "")

    def _clean_label(self, label: str) -> str:
        return re.sub(r"\s+", " ", label).strip(" *:\n\t")


def _city_from_location(location: str | None) -> str | None:
    """Best-effort: extract city from 'City, State' or 'City, State ZIP' string."""
    if not location:
        return None
    return location.split(",")[0].strip() or None


def _state_from_location(location: str | None) -> str | None:
    """Best-effort: extract state from 'City, State' or 'City, State ZIP' string."""
    if not location:
        return None
    parts = location.split(",")
    if len(parts) < 2:
        return None
    return parts[1].strip().split()[0] or None


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def is_optional_sensitive_label(value: str) -> bool:
    label = normalize_label(value)
    markers = (
        "voluntary",
        "self identification",
        "gender",
        "race",
        "ethnicity",
        "veteran",
        "disability",
        "eeo",
        "equal employment",
    )
    return any(marker in label for marker in markers)
