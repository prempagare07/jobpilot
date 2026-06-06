from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.ats_scorer import ATSResult, ATSScorer
from backend.config import PROJECT_ROOT, settings
from backend.db.models import ResumeVersion
from backend.services.database import get_db
from backend.services.vector_store import vector_store

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


class ResumeOut(BaseModel):
    id: int
    name: str
    file_path: str
    target_roles: list[str]
    keywords_json: list[str]
    ats_score_avg: float
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ResumeUpdateIn(BaseModel):
    name: str | None = None
    target_roles: list[str] | None = None
    is_active: bool | None = None


class ResumeScoreIn(BaseModel):
    job_description: str = Field(min_length=1)


class ATSResultOut(BaseModel):
    resume_name: str
    score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    recommendation: str
    tailoring_suggestions: list[str]


@router.get("/", response_model=list[ResumeOut])
def list_resumes(db: Annotated[Session, Depends(get_db)]) -> list[ResumeVersion]:
    return list(db.scalars(select(ResumeVersion).order_by(ResumeVersion.created_at.desc())))


@router.post("/upload", response_model=ResumeOut, status_code=201)
async def upload_resume(
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    target_roles_json: Annotated[str, Form()] = "[]",
    is_active: Annotated[bool, Form()] = True,
) -> ResumeVersion:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=422, detail="Only PDF resumes are supported")

    settings.resumes_path.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or f"{slugify(name)}.pdf").name
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    destination = unique_path(settings.resumes_path / safe_name)
    destination.write_bytes(await file.read())

    target_roles = json.loads(target_roles_json)
    if not isinstance(target_roles, list):
        raise HTTPException(status_code=422, detail="target_roles_json must be a JSON array")

    # Do NOT deactivate existing resumes — allow multiple active resumes so the
    # ATS scorer can compare all of them and pick the best match per job.
    resume = ResumeVersion(
        name=name,
        file_path=str(destination.relative_to(PROJECT_ROOT)),
        target_roles=[str(role) for role in target_roles],
        keywords_json=[],
        ats_score_avg=0.0,
        is_active=is_active,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    text = extract_resume_text(destination)
    if text.strip():
        upsert_resume_embedding(resume, text)
    return resume


@router.put("/{resume_id}", response_model=ResumeOut)
def update_resume(resume_id: int, payload: ResumeUpdateIn, db: Annotated[Session, Depends(get_db)]) -> ResumeVersion:
    resume = db.get(ResumeVersion, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume version not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(resume, key, value)
    db.commit()
    db.refresh(resume)
    return resume


@router.delete("/{resume_id}")
def deactivate_resume(resume_id: int, db: Annotated[Session, Depends(get_db)]) -> dict[str, bool]:
    resume = db.get(ResumeVersion, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume version not found")
    resume.is_active = False
    db.commit()
    return {"deactivated": True}


@router.post("/{resume_id}/score", response_model=ATSResultOut)
async def score_resume(
    resume_id: int,
    payload: ResumeScoreIn,
    db: Annotated[Session, Depends(get_db)],
) -> ATSResult:
    resume = db.get(ResumeVersion, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume version not found")
    path = resolve_project_path(resume.file_path)
    text = extract_resume_text(path)
    if not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from resume PDF")
    result = await ATSScorer().score(
        job_description=payload.job_description,
        resume_text=text,
        resume_name=resume.name,
    )
    resume.ats_score_avg = float(result.score)
    resume.keywords_json = result.matched_keywords
    db.commit()
    return result


@router.get("/{resume_id}/download")
def download_resume(resume_id: int, db: Annotated[Session, Depends(get_db)]) -> FileResponse:
    resume = db.get(ResumeVersion, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume version not found")
    path = resolve_project_path(resume.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Resume file missing on disk")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


def deactivate_active_resumes(db: Session) -> None:
    for resume in db.scalars(select(ResumeVersion).where(ResumeVersion.is_active.is_(True))):
        resume.is_active = False


def resolve_project_path(file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def extract_resume_text(path: Path) -> str:
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


def upsert_resume_embedding(resume: ResumeVersion, text: str) -> None:
    try:
        vector_store.upsert_text(
            f"resume:{resume.id}",
            text,
            {"kind": "resume", "resume_id": resume.id, "name": resume.name},
        )
    except Exception as exc:
        print(f"[resumes] ChromaDB resume upsert skipped: {exc}")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not choose a unique filename for {path.name}")


def slugify(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part) or "resume"
