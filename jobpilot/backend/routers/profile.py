# ruff: noqa: UP045
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents.qa_engine import QAEngine
from backend.db.models import Profile, QAMemory
from backend.services.database import get_db
from backend.services.vector_store import vector_store

router = APIRouter(prefix="/api/profile", tags=["profile"])


class EducationItem(BaseModel):
    school: str
    degree: str
    year: str


class ExperienceItem(BaseModel):
    company: str = ""
    title: str = ""
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    highlights: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str = ""
    url: Optional[str] = None
    description: str = ""
    impact: Optional[str] = None
    technologies: list[str] = Field(default_factory=list)


class ApplicationPreferences(BaseModel):
    remote_preference: Optional[str] = None
    employment_types: list[str] = Field(default_factory=list)
    earliest_start_date: Optional[str] = None
    notice_period: Optional[str] = None
    preferred_timezone: Optional[str] = None
    requires_sponsorship: Optional[bool] = None
    sponsorship_notes: Optional[str] = None
    open_to_background_check: Optional[bool] = None
    open_to_drug_test: Optional[bool] = None


class EEOData(BaseModel):
    gender: Optional[str] = None
    race_ethnicity: Optional[str] = None
    veteran_status: Optional[str] = None
    disability_status: Optional[str] = None


class ProfileIn(BaseModel):
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    phone: Optional[str] = None
    location: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    address_country: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    work_authorization: Optional[str] = Field(default=None, pattern="^(US Citizen|GC|H1B|OPT|CPT)$")
    years_experience: int = 0
    willing_to_relocate: bool = False
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    summary: Optional[str] = None
    skills_json: list[str] = Field(default_factory=list)
    education_json: list[EducationItem] = Field(default_factory=list)
    target_roles_json: list[str] = Field(default_factory=list)
    preferred_locations_json: list[str] = Field(default_factory=list)
    experience_json: list[ExperienceItem] = Field(default_factory=list)
    projects_json: list[ProjectItem] = Field(default_factory=list)
    achievements_json: list[str] = Field(default_factory=list)
    certifications_json: list[str] = Field(default_factory=list)
    languages_json: list[str] = Field(default_factory=list)
    application_preferences_json: ApplicationPreferences = Field(default_factory=ApplicationPreferences)
    eeo_json: EEOData = Field(default_factory=EEOData)


class ProfileOut(ProfileIn):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/", response_model=ProfileOut)
def get_profile(db: Annotated[Session, Depends(get_db)]) -> Profile:
    profile = db.scalar(select(Profile).order_by(Profile.id.asc()))
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not set up yet")
    return profile


@router.post("/", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileIn, db: Annotated[Session, Depends(get_db)]) -> Profile:
    existing = db.scalar(select(Profile).order_by(Profile.id.asc()))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Profile already exists. Use PUT /api/profile/ to update.")
    profile = Profile(**payload_values(payload))
    db.add(profile)
    db.commit()
    db.refresh(profile)
    upsert_profile_embedding(profile)
    return profile


@router.put("/", response_model=ProfileOut)
def update_profile(payload: ProfileIn, db: Annotated[Session, Depends(get_db)]) -> Profile:
    profile = db.scalar(select(Profile).order_by(Profile.id.asc()))
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not set up yet")
    for key, value in payload_values(payload).items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    upsert_profile_embedding(profile)
    return profile


@router.post("/setup-complete")
def setup_complete(db: Annotated[Session, Depends(get_db)]) -> dict[str, int | bool]:
    profile = db.scalar(select(Profile).order_by(Profile.id.asc()))
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not set up yet")
    before = int(db.scalar(select(func.count()).select_from(QAMemory)) or 0)
    QAEngine(db).prepopulate_common_questions(profile)
    after = int(db.scalar(select(func.count()).select_from(QAMemory)) or 0)
    return {"ok": True, "qa_memory_seeded": after - before, "previous_count": before}


def payload_values(payload: ProfileIn) -> dict[str, object]:
    values = payload.model_dump()
    values["education_json"] = [item.model_dump() for item in payload.education_json]
    values["experience_json"] = [item.model_dump() for item in payload.experience_json]
    values["projects_json"] = [item.model_dump() for item in payload.projects_json]
    values["application_preferences_json"] = payload.application_preferences_json.model_dump()
    values["eeo_json"] = payload.eeo_json.model_dump()
    return values


def upsert_profile_embedding(profile: Profile) -> None:
    text = profile_embedding_text(profile)
    try:
        vector_store.upsert_text("profile:primary", text, {"kind": "profile", "profile_id": profile.id})
    except Exception as exc:
        print(f"[profile] ChromaDB profile upsert skipped: {exc}")


def profile_embedding_text(profile: Profile) -> str:
    payload = {
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "work_authorization": profile.work_authorization,
        "years_experience": profile.years_experience,
        "willing_to_relocate": profile.willing_to_relocate,
        "salary_min": profile.salary_min,
        "salary_max": profile.salary_max,
        "summary": profile.summary,
        "skills_json": profile.skills_json,
        "education_json": profile.education_json,
        "target_roles_json": profile.target_roles_json,
        "preferred_locations_json": profile.preferred_locations_json,
        "experience_json": profile.experience_json,
        "projects_json": profile.projects_json,
        "achievements_json": profile.achievements_json,
        "certifications_json": profile.certifications_json,
        "languages_json": profile.languages_json,
        "application_preferences_json": profile.application_preferences_json,
        "eeo_json": profile.eeo_json,
    }
    return json.dumps(payload, sort_keys=True)


def profile_common_questions() -> list[str]:
    return [
        "Are you authorized to work in the US?",
        "Do you require visa sponsorship?",
        "Are you willing to relocate?",
        "What is your desired salary?",
        "Are you available for full-time employment?",
        "How many years of experience do you have?",
        "What roles are you targeting?",
        "What locations are you open to?",
        "What is your earliest start date?",
        "Are you open to remote work?",
    ]
