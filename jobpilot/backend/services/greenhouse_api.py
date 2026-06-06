from __future__ import annotations

"""
Greenhouse Job Board API client.

Submits applications directly via the public Greenhouse Job Board API
(https://boards-api.greenhouse.io) without using a browser.  No authentication
is required — the board token and job ID are extracted from the job URL.
"""

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

# Keywords that indicate a consent / attestation question that should be auto-accepted
_CONSENT_KEYWORDS = (
    "attest", "certify", "acknowledge", "authorize", "i agree", "i consent",
    "i understand", "privacy policy", "terms of", "text message", "sms consent",
    "ai-enabled", "ai enabled", "automated", "processing legalnotice",
    "data processing", "background check authorization",
)

import httpx

from backend.agents.qa_engine import QAEngine
from backend.services.apply_common import AuditLog, ApplyResult


# ---------------------------------------------------------------------------
# URL parsing helpers
# ---------------------------------------------------------------------------

def parse_greenhouse_url(url: str) -> tuple[str, str] | None:
    """
    Extract (board_token, job_id) from any Greenhouse job URL.

    Handles:
      - https://jobs.greenhouse.io/acmecorp/jobs/12345
      - https://boards.greenhouse.io/acmecorp/jobs/12345
      - https://boards.greenhouse.io/embed/job_app?for=acmecorp&token=12345
      - https://company.com/careers?gh_jid=12345&gh_src=...
        (company-embedded — board_token extracted from page source separately)
    Returns None if the URL cannot be parsed.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    # Embedded form: ?for=acmecorp&token=12345
    if "for" in qs and "token" in qs:
        return qs["for"][0], qs["token"][0]

    path_parts = [p for p in parsed.path.split("/") if p]

    # jobs.greenhouse.io/acmecorp/jobs/12345
    # boards.greenhouse.io/acmecorp/jobs/12345
    if len(path_parts) >= 3 and path_parts[-2] == "jobs":
        board_token = path_parts[-3]
        job_id = path_parts[-1]
        return board_token, job_id

    # boards.greenhouse.io/acmecorp  (just board, no job — can't apply)
    if len(path_parts) == 1 and ("greenhouse.io" in parsed.netloc):
        return None

    return None


def extract_greenhouse_board_token_from_source(page_source: str) -> str | None:
    """
    Pull the Greenhouse board token from a company page's HTML source.
    Looks for gh_jid query params and embedded script tokens.
    """
    for pattern in (
        r'greenhouse\.io/([a-zA-Z0-9_-]+)/jobs',
        r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)',
        r'"boardToken"\s*:\s*"([a-zA-Z0-9_-]+)"',
        r'gh_src=([a-zA-Z0-9_-]+)',
    ):
        m = re.search(pattern, page_source)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class GreenhouseApiService:
    """Submit Greenhouse applications via the public Job Board REST API."""

    BASE = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def seed_qa_memory(
        self,
        board_token: str,
        job_id: str,
        profile: dict,
        qa_engine: QAEngine,
        log: AuditLog,
    ) -> None:
        """
        Fetch the job's questions from the Greenhouse API and pre-populate QA memory
        so the Playwright form filler has answers ready before it touches the form.
        Does NOT submit anything.
        """
        log.info(f"Fetching Greenhouse questions for QA pre-seed (board={board_token}, job={job_id})")
        questions = await self._get_questions(board_token, job_id)
        if not questions:
            log.info("No questions returned from Greenhouse API — skipping QA pre-seed")
            return
        log.info(f"Pre-seeding QA memory from {len(questions)} question blocks")
        # Build payload just to trigger QA seeding — ignore files and needs_human result
        fake_log = AuditLog()  # discard fill events during seeding
        await self._build_payload(
            questions=questions,
            profile=profile,
            resume_path="",
            cover_letter_path=None,
            qa_engine=qa_engine,
            log=fake_log,
        )

    async def apply(
        self,
        url: str,
        board_token: str,
        job_id: str,
        profile: dict,
        resume_path: str,
        cover_letter_path: str | None,
        qa_engine: QAEngine,
        audit_log: AuditLog,
    ) -> ApplyResult:
        log = audit_log
        log.navigate(url)

        # 1. Fetch questions from the Job Board API
        log.info(f"Fetching Greenhouse job questions via API (board={board_token}, job={job_id})")
        try:
            questions = await self._get_questions(board_token, job_id)
        except Exception as exc:
            log.error(f"Failed to fetch Greenhouse job questions: {exc}")
            return ApplyResult(success=False, error=str(exc), url=url, ats_platform="greenhouse")

        if not questions:
            err = f"No questions returned from Greenhouse API for board={board_token} job={job_id}"
            log.error(err)
            return ApplyResult(success=False, error=err, url=url, ats_platform="greenhouse")

        log.info(f"Got {len(questions)} question blocks from Greenhouse API")

        # 2. Build the multipart payload
        payload, files, questions_needing_human = await self._build_payload(
            questions=questions,
            profile=profile,
            resume_path=resume_path,
            cover_letter_path=cover_letter_path,
            qa_engine=qa_engine,
            log=log,
        )

        if questions_needing_human:
            log.error("Unanswered questions: " + "; ".join(questions_needing_human))
            return ApplyResult(
                success=False,
                questions_needing_human=questions_needing_human,
                reason="needs_human",
                ats_platform="greenhouse",
                error="Missing answers: " + "; ".join(questions_needing_human),
                url=url,
            )

        # Sanity check: core identity fields must be present before submitting
        core_missing = [f for f in ("first_name", "last_name", "email") if not payload.get(f)]
        if core_missing and not files:
            err = f"Aborting submission — core fields missing from payload: {core_missing}"
            log.error(err)
            return ApplyResult(success=False, error=err, url=url, ats_platform="greenhouse")

        log.info(f"Payload has {len(payload)} text fields and {len(files)} file(s). Submitting.")

        # 3. POST the application
        log.submit(url)
        try:
            result = await self._post_application(board_token, job_id, payload, files, log)
        finally:
            for _, (_, fobj, _) in files.items():
                try:
                    fobj.close()
                except Exception:
                    pass

        return result

    # ------------------------------------------------------------------
    # Greenhouse API calls
    # ------------------------------------------------------------------

    async def _get_questions(self, board_token: str, job_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self.BASE}/{board_token}/jobs/{job_id}",
            params={"questions": "true"},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("questions") or []

    async def _post_application(
        self,
        board_token: str,
        job_id: str,
        payload: dict[str, str],
        files: dict[str, tuple],
        log: AuditLog,
    ) -> ApplyResult:
        url = f"{self.BASE}/{board_token}/jobs/{job_id}"
        try:
            # Build a unified multipart form so text fields and file fields are
            # sent in the same request body. httpx accepts:
            #   files = {"field": ("filename", bytes_or_fileobj, "content-type")}
            # We merge payload (text) into files as plain-value tuples so everything
            # travels in one multipart/form-data request.
            multipart: dict[str, Any] = {}
            for k, v in payload.items():
                multipart[k] = (None, v)
            multipart.update(files)  # file tuples already have (name, obj, content-type)
            resp = await self._client.post(url, files=multipart)
        except httpx.HTTPError as exc:
            err = f"Greenhouse API HTTP error: {exc}"
            log.error(err)
            return ApplyResult(success=False, error=err, url=url, ats_platform="greenhouse")

        if resp.status_code in (200, 201):
            log.info("Greenhouse API accepted the application (HTTP 200/201).")
            return ApplyResult(success=True, url=url, ats_platform="greenhouse")

        # Greenhouse returns 422 with JSON errors on validation failure
        if resp.status_code == 422:
            try:
                body = resp.json()
                errors = body.get("errors") or []
                messages = [e.get("message", str(e)) for e in errors] if errors else [resp.text[:300]]
            except Exception:
                messages = [resp.text[:300]]
            err = "Greenhouse validation errors: " + "; ".join(messages)
            log.error(err)
            return ApplyResult(
                success=False,
                questions_needing_human=messages,
                reason="needs_human",
                ats_platform="greenhouse",
                error=err,
                url=url,
            )

        err = f"Greenhouse API returned HTTP {resp.status_code}: {resp.text[:200]}"
        log.error(err)
        return ApplyResult(success=False, error=err, url=url, ats_platform="greenhouse")

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    async def _build_payload(
        self,
        questions: list[dict],
        profile: dict,
        resume_path: str,
        cover_letter_path: str | None,
        qa_engine: QAEngine,
        log: AuditLog,
    ) -> tuple[dict[str, str], dict[str, tuple], list[str]]:
        """
        Maps Greenhouse question fields → profile values.
        Returns (form_fields, file_fields, questions_needing_human).
        """
        payload: dict[str, str] = {}
        files: dict[str, tuple] = {}
        needs_human: list[str] = []

        for question in questions:
            label = (question.get("label") or "").strip()
            required = bool(question.get("required"))
            lbl_lower = label.lower()

            for field in question.get("fields") or []:
                field_name: str = field.get("name", "")
                field_type: str = field.get("type", "")
                values: list[dict] = field.get("values") or []

                if not field_name:
                    continue

                # --- Attachment fields (resume / cover letter) ---
                if field_type == "attachment":
                    lc = lbl_lower
                    if "resume" in lc or "cv" in lc or field_name in ("resume", "resume_text"):
                        if resume_path and Path(resume_path).exists():
                            p = Path(resume_path)
                            files[field_name] = (p.name, p.read_bytes(), "application/pdf")
                            log.upload(label, p.name)
                        elif required:
                            needs_human.append("Resume required but not provided")
                    elif "cover" in lc:
                        if cover_letter_path and Path(cover_letter_path).exists():
                            p = Path(cover_letter_path)
                            files[field_name] = (p.name, p.read_bytes(), "application/pdf")
                            log.upload(label, p.name)
                    continue

                # --- Consent / attestation — auto-accept ---
                if any(kw in lbl_lower for kw in _CONSENT_KEYWORDS):
                    if field_type in ("multi_value_single_select", "multi_value_multi_select") and values:
                        # Pick "yes" / "agree" option
                        for v in values:
                            vtext = (v.get("label") or "").lower()
                            if "yes" in vtext or "agree" in vtext or "accept" in vtext:
                                payload[field_name] = str(v.get("value", v.get("id", "1")))
                                log.fill(label, vtext, "greenhouse-api-consent")
                                break
                        else:
                            # Fallback: first option
                            v = values[0]
                            payload[field_name] = str(v.get("value", v.get("id", "1")))
                            log.fill(label, str(values[0].get("label", "auto")), "greenhouse-api-consent")
                    elif field_type in ("boolean", "checkbox"):
                        payload[field_name] = "1"
                        log.fill(label, "1 (auto-consent)", "greenhouse-api-consent")
                    else:
                        # Text consent field — fill phone number (for SMS consent) or "Yes"
                        if "text message" in lbl_lower or "sms" in lbl_lower or "phone" in lbl_lower:
                            v = profile.get("phone") or ""
                            if v:
                                payload[field_name] = v
                                log.fill(label, v, "greenhouse-api-consent")
                        else:
                            payload[field_name] = "Yes"
                            log.fill(label, "Yes (auto-consent)", "greenhouse-api-consent")
                    continue

                # --- Standard well-known fields ---
                value = self._map_standard_field(field_name, label, profile)

                is_select = field_type in ("multi_value_single_select", "multi_value_multi_select")

                # --- Multi-value / dropdown fields ---
                if is_select and values:
                    if not value:
                        value = self._pick_option(label, values, profile)

                # --- Ask QA engine (checks memory first, then AI) ---
                if not value and qa_engine is not None:
                    try:
                        qa_answer = await qa_engine.answer(label, {"profile": profile})
                        if qa_answer.source != "needs_human" and qa_answer.confidence >= 0.6:
                            raw_qa = qa_answer.answer
                            # For select fields the QA answer is human text (e.g. "Yes") — map it
                            # back to the option's numeric value so Greenhouse accepts it.
                            if is_select and values:
                                mapped = self._match_qa_to_option(raw_qa, values)
                                value = mapped if mapped is not None else raw_qa
                            else:
                                value = raw_qa
                    except Exception:
                        pass

                # Custom application-form questions (application_form[application][answers_*])
                # must always be filled — Greenhouse won't validate required=false fields
                # server-side but they're still visually required on the form.
                is_custom_answer = "answers_attributes" in field_name

                if value:
                    payload[field_name] = str(value)
                    log.fill(label, str(value)[:80], "greenhouse-api")
                elif required or is_custom_answer:
                    # Seed in QA memory so user can answer it in the Q&A tab.
                    if qa_engine is not None:
                        try:
                            await qa_engine.answer(label, {"profile": profile})
                        except Exception:
                            pass
                    needs_human.append(f"{label}")

        return payload, files, needs_human

    # ------------------------------------------------------------------
    # Field mapping helpers
    # ------------------------------------------------------------------

    _STANDARD_FIELDS: dict[str, list[str]] = {
        "first_name":  ["first_name", "firstname"],
        "last_name":   ["last_name", "lastname"],
        "email":       ["email"],
        "phone":       ["phone", "phone_number"],
        "location":    ["location", "job_application_location"],
        "linkedin":    ["linkedin_profile", "linkedin_url", "linkedin"],
        "website":     ["website", "portfolio", "github"],
        "full_name":   ["name"],
    }

    def _map_standard_field(self, field_name: str, label: str, profile: dict) -> str | None:
        fn_lower = field_name.lower()
        lbl_lower = label.lower()

        # Direct name matches
        if fn_lower in ("first_name",):
            return self._first_name(profile)
        if fn_lower in ("last_name",):
            return self._last_name(profile)
        if fn_lower in ("email",):
            return profile.get("email")
        if fn_lower in ("phone", "phone_number"):
            return profile.get("phone")
        if "linkedin" in fn_lower or "linkedin" in lbl_lower:
            return profile.get("linkedin_url")
        if "website" in fn_lower or "portfolio" in fn_lower:
            return profile.get("portfolio_url") or profile.get("github_url")
        if fn_lower == "location" or "location" in fn_lower:
            return profile.get("location")
        if fn_lower == "name" or lbl_lower == "name":
            return profile.get("full_name") or f"{self._first_name(profile) or ''} {self._last_name(profile) or ''}".strip()

        # Label-based fallbacks
        if "first name" in lbl_lower:
            return self._first_name(profile)
        if "last name" in lbl_lower:
            return self._last_name(profile)
        if "email" in lbl_lower:
            return profile.get("email")
        if "phone" in lbl_lower:
            return profile.get("phone")

        # Address / location
        if "mailing address" in lbl_lower or ("address" in lbl_lower and "email" not in lbl_lower):
            parts = [
                profile.get("address_street"),
                profile.get("address_city"),
                profile.get("address_state"),
                profile.get("address_zip"),
                profile.get("address_country") or "USA",
            ]
            combined = ", ".join(p for p in parts if p)
            return combined or profile.get("location")

        # Salary / compensation
        if any(kw in lbl_lower for kw in ("salary", "compensation", "hourly rate", "pay rate", "expected pay")):
            salary_min = profile.get("salary_min")
            salary_max = profile.get("salary_max")
            if salary_min and salary_max:
                return f"${int(salary_min):,} - ${int(salary_max):,}"
            if salary_min:
                return f"${int(salary_min):,}+"
            return None

        # University / school
        if "university" in lbl_lower or "school" in lbl_lower or "college" in lbl_lower:
            edu = (profile.get("education_json") or [{}])
            if edu:
                return edu[0].get("institution") or edu[0].get("school") or "Arizona State University"

        # Graduation year
        if "graduation" in lbl_lower or "grad year" in lbl_lower:
            edu = (profile.get("education_json") or [{}])
            if edu:
                return str(edu[0].get("end_year") or edu[0].get("graduation_year") or "")

        return None

    def _pick_option(self, label: str, values: list[dict], profile: dict) -> str | None:
        """Pick the most appropriate option value for a dropdown question."""
        lbl = label.lower()
        prefs = (profile.get("application_preferences_json") or {})

        # Work authorization
        if "authorized" in lbl or "eligible to work" in lbl or "work authorization" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "yes" in text or "authorized" in text or "eligible" in text or "citizen" in text:
                    return str(v.get("value", v.get("id", "")))

        # Commuting / in-person availability
        if "commut" in lbl or "on-site" in lbl or "onsite" in lbl or "in person" in lbl or "in-person" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "yes" in text:
                    return str(v.get("value", v.get("id", "")))

        # Relocation
        if "relocat" in lbl:
            willing = profile.get("willing_to_relocate")
            target_text = "yes" if willing else "no"
            for v in values:
                if target_text in (v.get("label") or "").lower():
                    return str(v.get("value", v.get("id", "")))

        # Background check / drug test
        if "background check" in lbl or "background screen" in lbl or "drug test" in lbl or "drug screen" in lbl:
            open_bg = (prefs.get("open_to_background_check") is not False)
            target_text = "yes" if open_bg else "no"
            for v in values:
                text = (v.get("label") or "").lower()
                if target_text in text:
                    return str(v.get("value", v.get("id", "")))

        # Sponsorship
        if "sponsor" in lbl or "visa" in lbl:
            requires = prefs.get("requires_sponsorship")
            if requires is True:
                target_text = "yes"
            else:
                target_text = "no"
            for v in values:
                text = (v.get("label") or "").lower()
                if target_text in text:
                    return str(v.get("value", v.get("id", "")))
            # fallback — pick "no"
            for v in values:
                if "no" in (v.get("label") or "").lower():
                    return str(v.get("value", v.get("id", "")))

        # Gender / pronouns (prefer "decline to state")
        if "gender" in lbl or "pronoun" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "decline" in text or "prefer not" in text or "choose not" in text:
                    return str(v.get("value", v.get("id", "")))

        # Race / ethnicity
        if "race" in lbl or "ethnicity" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "decline" in text or "prefer not" in text or "choose not" in text:
                    return str(v.get("value", v.get("id", "")))

        # Veteran status
        if "veteran" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "not" in text or "decline" in text or "no" in text:
                    return str(v.get("value", v.get("id", "")))

        # How did you hear about us — prefer "LinkedIn" or "Job board"
        if "hear" in lbl or "source" in lbl or "referral" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "linkedin" in text or "job board" in text or "indeed" in text or "jobright" in text:
                    return str(v.get("value", v.get("id", "")))

        # Remote / location type
        remote_pref = (prefs.get("remote_preference") or "").lower()
        if "remote" in lbl or "location type" in lbl:
            for v in values:
                text = (v.get("label") or "").lower()
                if "remote" in remote_pref and "remote" in text:
                    return str(v.get("value", v.get("id", "")))
                if "hybrid" in remote_pref and "hybrid" in text:
                    return str(v.get("value", v.get("id", "")))

        return None

    @staticmethod
    def _match_qa_to_option(qa_text: str, values: list[dict]) -> str | None:
        """
        Map a free-text QA answer (e.g. "Yes", "No", "US Citizen") to the closest
        option's numeric value from the Greenhouse choices array.
        """
        needle = qa_text.strip().lower()
        # Exact label match first
        for v in values:
            if (v.get("label") or "").lower() == needle:
                return str(v.get("value", v.get("id", "")))
        # Substring match — e.g. "yes" inside "Yes, I am authorized"
        for v in values:
            label = (v.get("label") or "").lower()
            if needle in label or label in needle:
                return str(v.get("value", v.get("id", "")))
        # Semantic yes/no
        if needle in {"yes", "true", "1", "y", "yep"}:
            for v in values:
                text = (v.get("label") or "").lower()
                if "yes" in text or text == "true":
                    return str(v.get("value", v.get("id", "")))
        if needle in {"no", "false", "0", "n", "nope"}:
            for v in values:
                text = (v.get("label") or "").lower()
                if text == "no" or text == "false":
                    return str(v.get("value", v.get("id", "")))
        return None

    @staticmethod
    def _first_name(profile: dict) -> str | None:
        full = profile.get("full_name", "")
        if full:
            return full.split()[0]
        return profile.get("first_name")

    @staticmethod
    def _last_name(profile: dict) -> str | None:
        full = profile.get("full_name", "")
        if full:
            parts = full.split()
            return " ".join(parts[1:]) if len(parts) > 1 else None
        return profile.get("last_name")
