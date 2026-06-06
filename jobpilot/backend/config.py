from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: object, **kwargs: object) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_base_url: str
    ollama_fast_model: str
    ollama_smart_model: str
    apollo_api_key: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    linkedin_email: str
    linkedin_password: str
    jobright_session_token: str
    jobright_session_id: str
    jobright_cookie: str
    jobright_email: str
    jobright_password: str
    indeed_publisher_id: str
    scrape_interval_hours: int
    max_jobs_per_run: int
    apply_daily_limit: int
    captcha_solver_key: str  # 2captcha API key — leave empty to disable
    imap_host: str           # IMAP server for reading verification emails (default: derived from SMTP_HOST)
    gmail_oauth_token_path: str  # Path to Gmail OAuth2 token pickle
    use_ollama_preparation: bool
    apply_browser_headless: bool
    apply_require_human_review: bool
    application_review_timeout_seconds: int

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must use the sqlite:/// scheme")
        raw_path = self.database_url.removeprefix(prefix)
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @property
    def chroma_path(self) -> Path:
        return (PROJECT_ROOT / "data" / "chroma").resolve()

    @property
    def resumes_path(self) -> Path:
        return (PROJECT_ROOT / "resumes").resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/jobpilot.db"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_fast_model=os.getenv("OLLAMA_FAST_MODEL", "llama3.2"),
        ollama_smart_model=os.getenv("OLLAMA_SMART_MODEL", "llama3.1:8b"),
        apollo_api_key=os.getenv("APOLLO_API_KEY", ""),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=_int_env("SMTP_PORT", 587),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        linkedin_email=os.getenv("LINKEDIN_EMAIL", ""),
        linkedin_password=os.getenv("LINKEDIN_PASSWORD", ""),
        jobright_session_token=os.getenv("JOBRIGHT_SESSION_TOKEN", ""),
        jobright_session_id=os.getenv("JOBRIGHT_SESSION_ID", ""),
        jobright_cookie=os.getenv("JOBRIGHT_COOKIE", ""),
        jobright_email=os.getenv("JOBRIGHT_EMAIL", ""),
        jobright_password=os.getenv("JOBRIGHT_PASSWORD", ""),
        indeed_publisher_id=os.getenv("INDEED_PUBLISHER_ID", ""),
        scrape_interval_hours=_int_env("SCRAPE_INTERVAL_HOURS", 6),
        max_jobs_per_run=_int_env("MAX_JOBS_PER_RUN", 100),
        apply_daily_limit=_int_env("APPLY_DAILY_LIMIT", 20),
        captcha_solver_key=os.getenv("CAPTCHA_SOLVER_KEY", ""),
        imap_host=os.getenv("IMAP_HOST", ""),
        gmail_oauth_token_path=os.getenv("GMAIL_OAUTH_TOKEN_PATH", "data/gmail_token.pickle"),
        use_ollama_preparation=_bool_env("USE_OLLAMA_PREPARATION", False),
        apply_browser_headless=_bool_env("APPLY_BROWSER_HEADLESS", False),
        apply_require_human_review=_bool_env("APPLY_REQUIRE_HUMAN_REVIEW", False),
        application_review_timeout_seconds=_int_env("APPLICATION_REVIEW_TIMEOUT_SECONDS", 600),
    )


settings = get_settings()

DATABASE_URL = settings.database_url
OLLAMA_BASE_URL = settings.ollama_base_url
OLLAMA_FAST_MODEL = settings.ollama_fast_model
OLLAMA_SMART_MODEL = settings.ollama_smart_model
APOLLO_API_KEY = settings.apollo_api_key
SMTP_HOST = settings.smtp_host
SMTP_PORT = settings.smtp_port
SMTP_USER = settings.smtp_user
SMTP_PASSWORD = settings.smtp_password
LINKEDIN_EMAIL = settings.linkedin_email
LINKEDIN_PASSWORD = settings.linkedin_password
JOBRIGHT_SESSION_TOKEN = settings.jobright_session_token
JOBRIGHT_SESSION_ID = settings.jobright_session_id
JOBRIGHT_COOKIE = settings.jobright_cookie
JOBRIGHT_EMAIL = settings.jobright_email
JOBRIGHT_PASSWORD = settings.jobright_password
INDEED_PUBLISHER_ID = settings.indeed_publisher_id
SCRAPE_INTERVAL_HOURS = settings.scrape_interval_hours
MAX_JOBS_PER_RUN = settings.max_jobs_per_run
APPLY_DAILY_LIMIT = settings.apply_daily_limit
USE_OLLAMA_PREPARATION = settings.use_ollama_preparation
APPLY_BROWSER_HEADLESS = settings.apply_browser_headless
APPLY_REQUIRE_HUMAN_REVIEW = settings.apply_require_human_review
APPLICATION_REVIEW_TIMEOUT_SECONDS = settings.application_review_timeout_seconds
