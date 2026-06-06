from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String)
    last_name: Mapped[str | None] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    address_street: Mapped[str | None] = mapped_column(String)
    address_city: Mapped[str | None] = mapped_column(String)
    address_state: Mapped[str | None] = mapped_column(String)
    address_zip: Mapped[str | None] = mapped_column(String)
    address_country: Mapped[str | None] = mapped_column(String)
    linkedin_url: Mapped[str | None] = mapped_column(String)
    github_url: Mapped[str | None] = mapped_column(String)
    portfolio_url: Mapped[str | None] = mapped_column(String)
    work_authorization: Mapped[str | None] = mapped_column(String)
    years_experience: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    willing_to_relocate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    skills_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    education_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list, nullable=False)
    target_roles_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_locations_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    experience_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list, nullable=False)
    projects_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list, nullable=False)
    achievements_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    certifications_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    languages_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    application_preferences_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    eeo_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    target_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ats_score_avg: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)

    jobs: Mapped[list[Job]] = relationship(back_populates="resume_version")
    applications: Mapped[list[Application]] = relationship(back_populates="resume_version")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String)
    job_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    date_posted: Mapped[datetime | None] = mapped_column(DateTime)
    easy_apply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    status: Mapped[str] = mapped_column(String, default="new", nullable=False)
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    ats_score: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)

    resume_version: Mapped[ResumeVersion | None] = relationship(back_populates="jobs")
    applications: Mapped[list[Application]] = relationship(back_populates="job", cascade="all, delete-orphan")
    outreach_contacts: Mapped[list[OutreachContact]] = relationship(back_populates="job")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id", ondelete="SET NULL"),
    )
    resume_name: Mapped[str | None] = mapped_column(String)
    resume_path: Mapped[str | None] = mapped_column(String)
    cover_letter_text: Mapped[str | None] = mapped_column(Text)
    cover_letter_path: Mapped[str | None] = mapped_column(String)
    applied_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, default="applied", nullable=False)
    apply_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    ats_platform: Mapped[str | None] = mapped_column(String)
    task_id: Mapped[str | None] = mapped_column(String, index=True)
    questions_encountered_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    questions_needing_human_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    screenshot_url: Mapped[str | None] = mapped_column(String)
    response_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    audit_log_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)

    job: Mapped[Job] = relationship(back_populates="applications")
    resume_version: Mapped[ResumeVersion | None] = relationship(back_populates="applications")


class QAMemory(Base):
    __tablename__ = "qa_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String, default="text", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String, default="user_provided", nullable=False)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class OutreachContact(Base):
    __tablename__ = "outreach_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String, index=True)
    email_status: Mapped[str | None] = mapped_column(String)
    company: Mapped[str] = mapped_column(String, nullable=False, index=True)
    seniority: Mapped[str | None] = mapped_column(String)
    department: Mapped[str | None] = mapped_column(String)
    linkedin_url: Mapped[str | None] = mapped_column(String)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    email_subject: Mapped[str | None] = mapped_column(String)
    email_body: Mapped[str | None] = mapped_column(Text)
    reply_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reply_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    apollo_id: Mapped[str | None] = mapped_column(String)

    job: Mapped[Job | None] = relationship(back_populates="outreach_contacts")


class ApolloUsage(Base):
    __tablename__ = "apollo_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    usage_date: Mapped[str] = mapped_column(String, nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    credits_used: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    subject_template: Mapped[str] = mapped_column(String, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    role_type: Mapped[str] = mapped_column(String, default="General", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_applied: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
