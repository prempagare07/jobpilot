from __future__ import annotations

import re
import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.ats_scorer import ATSResult, ATSScorer
from backend.agents.cover_letter import CoverLetterAgent
from backend.config import PROJECT_ROOT, settings
from backend.db.models import Job, Profile, ResumeVersion
from backend.services.apply_common import model_to_dict


@dataclass(frozen=True)
class PreparedApplication:
    job_id: str
    resume_version_id: int
    resume_name: str
    ats_result: ATSResult
    cover_letter_text: str
    warnings: list[str]


class JobPreparationService:
    def __init__(
        self,
        db: Session,
        ats_scorer: ATSScorer | None = None,
        cover_letter_agent: CoverLetterAgent | None = None,
    ) -> None:
        self.db = db
        self.ats_scorer = ats_scorer or ATSScorer()
        self.cover_letter_agent = cover_letter_agent or CoverLetterAgent()

    async def prepare(self, job_id: str) -> PreparedApplication:
        job = self.db.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")

        profile = self.db.scalar(select(Profile).order_by(Profile.id.asc()))

        resume_payloads = self._resume_payloads()
        if not resume_payloads:
            raise ValueError("Upload at least one resume with extractable text before preparing applications.")

        warnings: list[str] = []
        best_resume, best_resume_text, ats_result = self._fallback_best_resume(job, resume_payloads)

        if settings.use_ollama_preparation:
            try:
                best_name, ats_result = await self.ats_scorer.pick_best_resume(job.job_description, resume_payloads)
                best_resume = next(
                    (payload["resume"] for payload in resume_payloads if payload["name"] == best_name),
                    resume_payloads[0]["resume"],
                )
                best_resume_text = next(
                    (payload["resume_text"] for payload in resume_payloads if payload["resume"].id == best_resume.id),
                    "",
                )
            except Exception as exc:
                warnings.append(f"Ollama ATS scoring unavailable; used local keyword fallback. {exc}")
        else:
            warnings.append("Used fast local ATS scoring. Set USE_OLLAMA_PREPARATION=true for slower LLM scoring.")

        if profile is None:
            warnings.append("Create your profile in Settings before cover letters and applications can run.")
            cover_letter_text = ""
        else:
            cover_letter_text = fallback_cover_letter(job, profile, ats_result)
            if settings.use_ollama_preparation:
                try:
                    cover_letter = await self.cover_letter_agent.generate(
                        job=model_to_dict(job),
                        profile=model_to_dict(profile),
                        resume_text=best_resume_text,
                        ats_result=ats_result,
                    )
                    cover_letter_text = cover_letter.body
                except Exception as exc:
                    warnings.append(f"Ollama cover letter generation unavailable; used local fallback. {exc}")

        job.resume_version_id = best_resume.id
        job.ats_score = float(ats_result.score)
        job.notes = render_ats_notes(ats_result)
        self.db.commit()
        self.db.refresh(job)

        return PreparedApplication(
            job_id=job.id,
            resume_version_id=best_resume.id,
            resume_name=best_resume.name,
            ats_result=ats_result,
            cover_letter_text=cover_letter_text,
            warnings=warnings,
        )

    def _resume_payloads(self) -> list[dict[str, Any]]:
        resumes = list(self.db.scalars(select(ResumeVersion).order_by(ResumeVersion.is_active.desc(), ResumeVersion.id.desc())))
        payloads: list[dict[str, Any]] = []
        for resume in resumes:
            resume_text = read_resume_text(resume.file_path)
            if not resume_text.strip():
                continue
            payloads.append(
                {
                    "id": resume.id,
                    "name": resume.name,
                    "resume_name": resume.name,
                    "resume_text": resume_text,
                    "resume": resume,
                }
            )
        return payloads

    def _fallback_best_resume(
        self,
        job: Job,
        resume_payloads: list[dict[str, Any]],
    ) -> tuple[ResumeVersion, str, ATSResult]:
        keywords = extract_keywords(job.job_description)
        scored: list[tuple[int, ResumeVersion, str, list[str], list[str]]] = []
        for payload in resume_payloads:
            resume_text = str(payload["resume_text"])
            resume_words = normalize_words(resume_text)
            matched = [keyword for keyword in keywords if keyword in resume_words or keyword.replace(" ", "") in resume_words]
            missing = [keyword for keyword in keywords if keyword not in matched]
            score = round((len(matched) / max(len(keywords), 1)) * 100)
            scored.append((score, payload["resume"], resume_text, matched, missing))
        score, resume, resume_text, matched, missing = max(scored, key=lambda item: item[0])
        ats_result = ATSResult(
            resume_name=resume.name,
            score=max(0, min(100, score)),
            matched_keywords=matched[:30],
            missing_keywords=missing[:30],
            recommendation="Local keyword score generated because Ollama scoring was unavailable.",
            tailoring_suggestions=[
                f"Add a concrete bullet showing experience with {keyword}."
                for keyword in missing[:5]
            ],
        )
        return resume, resume_text, ats_result


def render_ats_notes(ats_result: ATSResult) -> str:
    return str(
        {
            "recommendation": ats_result.recommendation,
            "matched_keywords": ats_result.matched_keywords,
            "missing_keywords": ats_result.missing_keywords,
            "tailoring_suggestions": ats_result.tailoring_suggestions,
        }
    )


def extract_list_from_notes(notes: str | None, key: str) -> list[str]:
    if not notes:
        return []
    try:
        payload = ast.literal_eval(notes)
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
    except Exception:
        pass
    match = re.search(rf"{re.escape(key)}'?:\s*\[(.*?)\]", notes)
    if not match:
        return []
    return [
        item[0] or item[1]
        for item in re.findall(r"'([^']+)'|\"([^\"]+)\"", match.group(1))
        if item[0] or item[1]
    ]


def read_resume_text(file_path: str) -> str:
    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""


def extract_keywords(text: str) -> list[str]:
    skill_keywords = [
        "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust", "sql",
        "postgresql", "mysql", "sqlite", "mongodb", "redis", "aws", "azure", "gcp",
        "docker", "kubernetes", "terraform", "linux", "fastapi", "django", "flask",
        "react", "next.js", "node.js", "spring", "graphql", "rest", "microservices",
        "distributed systems", "machine learning", "deep learning", "llm", "rag",
        "pytorch", "tensorflow", "scikit-learn", "spark", "kafka", "airflow",
        "ci/cd", "github actions", "security", "authentication", "authorization",
        "data structures", "algorithms", "system design", "apis", "etl",
    ]
    common = {
        "the",
        "and",
        "for",
        "with",
        "you",
        "our",
        "will",
        "are",
        "this",
        "that",
        "from",
        "have",
        "work",
        "team",
        "role",
        "job",
        "experience",
    }
    lowered = text.lower()
    prioritized = [keyword for keyword in skill_keywords if keyword in lowered]
    tokens = re.findall(r"[a-zA-Z][a-zA-Z+#.\-]{2,}", lowered)
    counts: dict[str, int] = {}
    for token in tokens:
        cleaned = token.strip(".,:-").lower()
        if cleaned in common:
            continue
        counts[cleaned] = counts.get(cleaned, 0) + 1
    frequent = [keyword for keyword, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)]
    combined: list[str] = []
    for keyword in prioritized + frequent:
        if keyword not in combined:
            combined.append(keyword)
        if len(combined) >= 30:
            break
    return combined


def normalize_words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#.\-]+", text.lower()))


def fallback_cover_letter(job: Job, profile: Profile, ats_result: ATSResult) -> str:
    name = profile.full_name or "Candidate"
    matched = ", ".join(ats_result.matched_keywords[:8]) or "the core requirements"
    summary = profile.summary or f"{profile.years_experience} years of relevant engineering experience"
    return (
        f"Dear {job.company} hiring team,\n\n"
        f"{job.title} stands out because the role centers on work I have been building toward: "
        f"{matched}. My background includes {summary}, and I would bring that focus to the problems "
        f"described in this role.\n\n"
        f"I am especially interested in contributing to {job.company}'s engineering work with practical, "
        f"reliable execution across the stack. I would be glad to share more about the projects and systems "
        f"that map most closely to this position.\n\n"
        f"Best,\n{name}"
    )


def prepared_to_dict(prepared: PreparedApplication) -> dict[str, Any]:
    payload = asdict(prepared)
    payload["ats_result"] = asdict(prepared.ats_result)
    return payload
