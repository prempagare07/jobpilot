from __future__ import annotations

import asyncio
import re
import textwrap
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents.qa_engine import QAEngine
from backend.config import PROJECT_ROOT, settings
from backend.db.models import Application, Job, Profile, ResumeVersion
from backend.services.apply_common import AuditLog, ApplyResult, model_to_dict
from backend.services.generic_apply import GenericApplyService
from backend.services.indeed_apply import IndeedApplyService
from backend.services.job_preparation import JobPreparationService, fallback_cover_letter
from backend.services.linkedin_apply import LinkedInApplyService

ApplicationStatus = Literal["applied", "failed", "needs_human", "skipped"]


class LimitReachedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApplicationResult:
    job_id: str
    status: ApplicationStatus
    success: bool
    screenshot_path: str | None = None
    questions: list[str] = field(default_factory=list)
    error: str | None = None
    apply_result: ApplyResult | None = None


class ApplicationService:
    def __init__(
        self,
        db: Session,
        linkedin_service: LinkedInApplyService | None = None,
        indeed_service: IndeedApplyService | None = None,
        generic_service: GenericApplyService | None = None,
    ) -> None:
        self.db = db
        self.linkedin_service = linkedin_service or LinkedInApplyService()
        self.indeed_service = indeed_service or IndeedApplyService()
        self.generic_service = generic_service or GenericApplyService()

    async def apply_to_job(
        self,
        job_id: str,
        human_approved: bool = False,
        cover_letter_override: str | None = None,
        task_id: str | None = None,
    ) -> ApplicationResult:
        self._enforce_daily_limit()
        job = self.db.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")
        self._validate_job_status(job, human_approved)
        if job.status != "queued":
            job.status = "queued"

        now = datetime.utcnow()
        application = Application(
            job_id=job.id,
            resume_version_id=None,
            applied_at=now,
            apply_status="pending",
            status="applied",
            task_id=task_id,
            response_received=False,
            audit_log_json=[],
        )
        self.db.add(application)
        self.db.commit()

        # Flush callback: write audit entries to DB after every event so the
        # Applications page shows a live trail while the automation runs.
        def _flush_audit() -> None:
            application.audit_log_json = list(audit_log.entries)
            self.db.commit()

        # Status callback: lets the apply services push live status transitions
        # (e.g. "waiting_captcha") that the frontend polls and displays.
        def _update_status(status: str) -> None:
            application.apply_status = status
            application.audit_log_json = list(audit_log.entries)
            self.db.commit()

        audit_log = AuditLog(flush_callback=_flush_audit, status_callback=_update_status)
        audit_log.info(f"Apply started for job {job_id} — {job.title} at {job.company}")
        audit_log.navigate(job.url)
        profile = self.db.scalar(select(Profile).order_by(Profile.id.asc()))
        if profile is None:
            message = "Create your profile in Settings before application automation can run."
            audit_log.error(message)
            job.status = "reviewed"
            job.notes = append_note(job.notes, message)
            application.apply_status = "needs_human"
            application.failure_reason = message
            self.db.commit()
            return ApplicationResult(
                job_id=job.id,
                status="needs_human",
                success=False,
                questions=[message],
                error="Profile required before applying.",
            )

        qa_engine = QAEngine(self.db)
        cover_letter = ""
        resume = None
        prepared = None
        try:
            if job.resume_version_id is None or not job.ats_score:
                application.apply_status = "preparing"
                self.db.commit()
                audit_log.info("Preparing job with fast local resume scoring")
                prepared = await JobPreparationService(self.db).prepare(job.id)
                self.db.refresh(job)

            resume = self._resolve_resume(job)
            resume_path = resolve_project_path(resume.file_path)
            if not resume_path.exists():
                raise FileNotFoundError(f"Resume file does not exist: {resume_path}")

            audit_log.info(f"Selected resume: {resume.name}")
            application.resume_version_id = resume.id
            application.resume_name = resume.name
            application.apply_status = "running"
            self.db.commit()

            cover_letter = (
                cover_letter_override
                or (prepared.cover_letter_text if prepared is not None else "")
                or fallback_cover_letter(job, profile, self._ats_result_from_job(job, resume))
            )
            audit_log.info("Cover letter draft ready")
            application.cover_letter_text = cover_letter
            application.resume_path = str(resume_path) if resume_path else None
            application.audit_log_json = audit_log.entries
            self.db.commit()
            cover_letter_path = write_cover_letter_pdf(
                job.id,
                cover_letter,
                company=job.company,
                title=job.title,
            )
            application.cover_letter_path = str(cover_letter_path)
            self.db.commit()
            audit_log.info(f"Cover letter PDF created: {cover_letter_path.name}")
            result = await self._route_apply(
                job=job,
                profile=profile,
                resume_path=resume_path,
                cover_letter=cover_letter,
                cover_letter_path=cover_letter_path,
                qa_engine=qa_engine,
                audit_log=audit_log,
            )
        except Exception as exc:
            full_error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            audit_log.error(full_error)
            application.apply_status = "needs_human" if is_human_fixable_error(exc) else "failed"
            application.failure_reason = full_error
            application.questions_needing_human_json = [str(exc)]
            if is_human_fixable_error(exc):
                job.status = "reviewed"
                job.notes = append_note(job.notes, f"Application paused before browser submit: {exc}")
                self.db.commit()
                return ApplicationResult(
                    job_id=job.id,
                    status="needs_human",
                    success=False,
                    questions=[str(exc)],
                    error=full_error,
                )
            job.status = "failed"
            job.notes = append_note(job.notes, f"Application failed before browser submit: {exc}")
            self.db.commit()
            return ApplicationResult(job_id=job.id, status="failed", success=False, error=full_error)

        raw_path = result.screenshot_path
        application.screenshot_url = f"/screenshots/{raw_path.rsplit('/', 1)[-1]}" if raw_path else None
        application.questions_encountered_json = result.questions_encountered or []
        application.ats_platform = result.ats_platform or (job.platform or "generic")
        application.audit_log_json = audit_log.entries

        if result.questions_needing_human:
            job.status = "reviewed"
            job.notes = append_note(
                job.notes,
                "Application paused for human answers: " + "; ".join(result.questions_needing_human),
            )
            application.apply_status = "needs_human"
            application.failure_reason = "Unanswered questions: " + "; ".join(result.questions_needing_human)
            application.questions_needing_human_json = result.questions_needing_human
            self.db.commit()
            return ApplicationResult(
                job_id=job.id,
                status="needs_human",
                success=False,
                screenshot_path=result.screenshot_path,
                questions=result.questions_needing_human,
                error=result.error,
                apply_result=result,
            )

        if result.success:
            job.status = "applied"
            job.applied_at = now
            application.apply_status = "applied"
            application.notes = f"Applied via automation. Screenshot: {result.screenshot_path or 'not captured'}"
            self.db.commit()
            return ApplicationResult(
                job_id=job.id,
                status="applied",
                success=True,
                screenshot_path=result.screenshot_path,
                apply_result=result,
            )

        job.status = "failed"
        job.notes = append_note(job.notes, result.error or result.reason or "Application failed.")
        application.apply_status = "failed"
        application.failure_reason = result.error or result.reason or "Application failed."
        application.questions_needing_human_json = result.questions_needing_human or []
        self.db.commit()
        return ApplicationResult(
            job_id=job.id,
            status="failed",
            success=False,
            screenshot_path=result.screenshot_path,
            questions=result.questions_encountered,
            error=result.error,
            apply_result=result,
        )

    async def batch_apply(self, job_ids: list[str], delay_between: int = 30) -> list[ApplicationResult]:
        results: list[ApplicationResult] = []
        for index, job_id in enumerate(job_ids):
            result = await self.apply_to_job(job_id)
            results.append(result)
            if result.status == "needs_human":
                break
            if index < len(job_ids) - 1:
                await asyncio.sleep(delay_between)
        return results

    async def _route_apply(
        self,
        job: Job,
        profile: Profile,
        resume_path: Path,
        cover_letter: str,
        cover_letter_path: Path | None,
        qa_engine: QAEngine,
        audit_log: AuditLog | None = None,
    ) -> ApplyResult:
        profile_dict = model_to_dict(profile)
        job_dict = model_to_dict(job)
        job_dict["url"] = job.url
        profile_dict["resume_path"] = str(resume_path)
        profile_dict["cover_letter_path"] = str(cover_letter_path) if cover_letter_path else None
        profile_dict["job"] = job_dict

        platform = (job.platform or "").lower()
        url = job.url.lower()
        if platform == "linkedin" or "linkedin.com" in url:
            return await self.linkedin_service.apply(
                job=job_dict,
                profile=profile_dict,
                resume_path=str(resume_path),
                cover_letter=cover_letter,
                qa_engine=qa_engine,
                audit_log=audit_log,
            )
        if platform == "indeed" or "indeed.com" in url:
            indeed_result = await self.indeed_service.apply(
                job=job_dict,
                profile=profile_dict,
                resume_path=str(resume_path),
                qa_engine=qa_engine,
                audit_log=audit_log,
            )
            if indeed_result.reason == "external_redirect" and indeed_result.url:
                return await self.generic_service.apply(
                    url=indeed_result.url,
                    profile=profile_dict,
                    resume_path=str(resume_path),
                    cover_letter_path=str(cover_letter_path) if cover_letter_path else None,
                    qa_engine=qa_engine,
                    audit_log=audit_log,
                )
            return indeed_result
        return await self.generic_service.apply(
            url=job.url,
            profile=profile_dict,
            resume_path=str(resume_path),
            cover_letter_path=str(cover_letter_path) if cover_letter_path else None,
            qa_engine=qa_engine,
            audit_log=audit_log,
        )

    def _resolve_resume(self, job: Job) -> ResumeVersion:
        if job.resume_version_id is not None:
            resume = self.db.get(ResumeVersion, job.resume_version_id)
            if resume is not None:
                return resume
        resume = self.db.scalar(select(ResumeVersion).where(ResumeVersion.is_active.is_(True)))
        if resume is None:
            raise ValueError("No resume version is linked to the job and no active resume exists.")
        return resume

    def _ats_result_from_job(self, job: Job, resume: ResumeVersion):
        from backend.agents.ats_scorer import ATSResult
        from backend.services.job_preparation import extract_list_from_notes

        matched = extract_list_from_notes(job.notes, "matched_keywords")
        missing = extract_list_from_notes(job.notes, "missing_keywords")
        return ATSResult(
            resume_name=resume.name,
            score=int(job.ats_score or 0),
            matched_keywords=matched,
            missing_keywords=missing,
            recommendation="Fast local application preparation.",
            tailoring_suggestions=[
                f"Add a concrete bullet showing experience with {keyword}."
                for keyword in missing[:5]
            ],
        )

    def _validate_job_status(self, job: Job, human_approved: bool) -> None:
        # "failed" and "applied" are allowed so the user can retry a failed or previously-applied job.
        allowed_statuses = {"new", "queued", "reviewed", "failed", "applied"}
        if job.status not in allowed_statuses:
            raise ValueError(f"Job {job.id} cannot be applied to in status '{job.status}'.")

    def _enforce_daily_limit(self) -> None:
        start = datetime.combine(datetime.utcnow().date(), time.min)
        applied_today = self.db.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.applied_at >= start,
                Application.apply_status.in_(("pending", "preparing", "running", "waiting_review", "waiting_captcha", "applied")),
            )
        )
        if int(applied_today or 0) >= settings.apply_daily_limit:
            raise LimitReachedError(f"Daily apply limit reached: {settings.apply_daily_limit}")


async def _url_likely_needs_cover_letter(url: str) -> bool:
    """
    Quick pre-check: does the job application form likely have a cover letter field?
    Returns True (generate) when uncertain. Returns False only when confident there is none.
    """
    # Known ATSes/platforms that never show a cover letter upload field
    _no_cl_domains = {"ashbyhq.com", "myworkdayjobs.com", "workday.com"}
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if any(domain in host for domain in _no_cl_domains):
        return False
    # Quick HTTP fetch — look for "cover letter" in the HTML (fast, no JS)
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobPilot/1.0)"}
        async with httpx.AsyncClient(headers=headers, timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url)
            html = resp.text.lower()
            return "cover letter" in html or "cover_letter" in html
    except Exception:
        return True  # default to generating when we can't check


def resolve_project_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def is_human_fixable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    human_fixable_fragments = (
        "upload at least one resume",
        "no resume",
        "resume file does not exist",
        "profile",
        "settings",
        "account creation",
        "sign-in",
        "sign in",
        "login",
    )
    return any(fragment in message for fragment in human_fixable_fragments)


def write_cover_letter_pdf(
    job_id: str,
    cover_letter: str,
    company: str | None = None,
    title: str | None = None,
) -> Path:
    company_stem = safe_stem(company or "company")
    title_stem = safe_stem(title or "job")
    file_stem = "_".join(part for part in ("cover_letter", company_stem, title_stem) if part)
    file_stem = file_stem[:140].rstrip("_")
    path = PROJECT_ROOT / "data" / "cover_letters" / f"{file_stem}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = textwrap.wrap(cover_letter, width=88) or [""]
    stream_lines = ["BT", "/F1 11 Tf", "50 760 Td", "14 TL"]
    for index, line in enumerate(lines[:48]):
        escaped = pdf_escape(line)
        if index == 0:
            stream_lines.append(f"({escaped}) Tj")
        else:
            stream_lines.append(f"T* ({escaped}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    path.write_bytes(build_pdf(objects))
    return path


def build_pdf(objects: list[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", value)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem or "file"


def append_note(existing: str | None, note: str) -> str:
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    line = f"[{timestamp}] {note}"
    return f"{existing}\n{line}" if existing else line
