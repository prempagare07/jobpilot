from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.config import PROJECT_ROOT, settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_database() -> Path:
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.resumes_path.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        ensure_profile_agent_columns(connection)
        ensure_outreach_columns(connection)
        ensure_jobs_failed_status(connection)
        ensure_application_columns(connection)
        connection.commit()

    return settings.sqlite_path


def ensure_profile_agent_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(profile)").fetchall()}
    migrations = {
        "first_name": "ALTER TABLE profile ADD COLUMN first_name TEXT",
        "last_name": "ALTER TABLE profile ADD COLUMN last_name TEXT",
        "address_street": "ALTER TABLE profile ADD COLUMN address_street TEXT",
        "address_city": "ALTER TABLE profile ADD COLUMN address_city TEXT",
        "address_state": "ALTER TABLE profile ADD COLUMN address_state TEXT",
        "address_zip": "ALTER TABLE profile ADD COLUMN address_zip TEXT",
        "address_country": "ALTER TABLE profile ADD COLUMN address_country TEXT",
        "willing_to_relocate": "ALTER TABLE profile ADD COLUMN willing_to_relocate INTEGER DEFAULT 0 NOT NULL",
        "salary_min": "ALTER TABLE profile ADD COLUMN salary_min INTEGER",
        "salary_max": "ALTER TABLE profile ADD COLUMN salary_max INTEGER",
        "target_roles_json": "ALTER TABLE profile ADD COLUMN target_roles_json TEXT DEFAULT '[]' NOT NULL",
        "preferred_locations_json": "ALTER TABLE profile ADD COLUMN preferred_locations_json TEXT DEFAULT '[]' NOT NULL",
        "experience_json": "ALTER TABLE profile ADD COLUMN experience_json TEXT DEFAULT '[]' NOT NULL",
        "projects_json": "ALTER TABLE profile ADD COLUMN projects_json TEXT DEFAULT '[]' NOT NULL",
        "achievements_json": "ALTER TABLE profile ADD COLUMN achievements_json TEXT DEFAULT '[]' NOT NULL",
        "certifications_json": "ALTER TABLE profile ADD COLUMN certifications_json TEXT DEFAULT '[]' NOT NULL",
        "languages_json": "ALTER TABLE profile ADD COLUMN languages_json TEXT DEFAULT '[]' NOT NULL",
        "application_preferences_json": "ALTER TABLE profile ADD COLUMN application_preferences_json TEXT DEFAULT '{}' NOT NULL",
        "eeo_json": "ALTER TABLE profile ADD COLUMN eeo_json TEXT DEFAULT '{}' NOT NULL",
    }
    for column_name, statement in migrations.items():
        if column_name not in existing_columns:
            connection.execute(statement)


def ensure_outreach_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(outreach_contacts)").fetchall()}
    migrations = {
        "email_status": "ALTER TABLE outreach_contacts ADD COLUMN email_status TEXT",
        "seniority": "ALTER TABLE outreach_contacts ADD COLUMN seniority TEXT",
        "department": "ALTER TABLE outreach_contacts ADD COLUMN department TEXT",
        "follow_up_sent_at": "ALTER TABLE outreach_contacts ADD COLUMN follow_up_sent_at DATETIME",
    }
    for column_name, statement in migrations.items():
        if column_name not in existing_columns:
            connection.execute(statement)


def ensure_application_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)").fetchall()}
    migrations = {
        "resume_name": "ALTER TABLE applications ADD COLUMN resume_name TEXT",
        "apply_status": "ALTER TABLE applications ADD COLUMN apply_status TEXT DEFAULT 'applied' NOT NULL",
        "ats_platform": "ALTER TABLE applications ADD COLUMN ats_platform TEXT",
        "task_id": "ALTER TABLE applications ADD COLUMN task_id TEXT",
        "questions_encountered_json": "ALTER TABLE applications ADD COLUMN questions_encountered_json TEXT DEFAULT '[]' NOT NULL",
        "questions_needing_human_json": "ALTER TABLE applications ADD COLUMN questions_needing_human_json TEXT DEFAULT '[]' NOT NULL",
        "failure_reason": "ALTER TABLE applications ADD COLUMN failure_reason TEXT",
        "screenshot_url": "ALTER TABLE applications ADD COLUMN screenshot_url TEXT",
        "audit_log_json": "ALTER TABLE applications ADD COLUMN audit_log_json TEXT DEFAULT '[]' NOT NULL",
        "cover_letter_path": "ALTER TABLE applications ADD COLUMN cover_letter_path TEXT",
        "resume_path": "ALTER TABLE applications ADD COLUMN resume_path TEXT",
    }
    for column_name, statement in migrations.items():
        if column_name not in existing_columns:
            connection.execute(statement)

    connection.execute("CREATE INDEX IF NOT EXISTS ix_applications_apply_status ON applications (apply_status)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_applications_task_id ON applications (task_id)")

    # Make resume_version_id nullable if it currently has a NOT NULL constraint.
    # SQLite requires table recreation for this change.
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()
    if row and "resume_version_id INTEGER NOT NULL" in row[0]:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            CREATE TABLE applications_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                resume_version_id INTEGER REFERENCES resume_versions(id) ON DELETE SET NULL,
                resume_name TEXT,
                cover_letter_text TEXT,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                follow_up_sent_at DATETIME,
                status TEXT DEFAULT 'applied' NOT NULL,
                apply_status TEXT DEFAULT 'applied' NOT NULL,
                ats_platform TEXT,
                task_id TEXT,
                questions_encountered_json TEXT DEFAULT '[]' NOT NULL,
                questions_needing_human_json TEXT DEFAULT '[]' NOT NULL,
                failure_reason TEXT,
                screenshot_url TEXT,
                response_received INTEGER DEFAULT 0 NOT NULL,
                notes TEXT,
                audit_log_json TEXT DEFAULT '[]' NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO applications_new (
                id, job_id, resume_version_id, cover_letter_text, applied_at,
                follow_up_sent_at, status, apply_status, response_received, notes, audit_log_json
            )
            SELECT
                id, job_id, resume_version_id, cover_letter_text, applied_at,
                follow_up_sent_at, status, status, response_received, notes, '[]'
            FROM applications
            """
        )
        connection.execute("DROP TABLE applications")
        connection.execute("ALTER TABLE applications_new RENAME TO applications")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_applications_apply_status ON applications (apply_status)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_applications_task_id ON applications (task_id)")
        connection.execute("PRAGMA foreign_keys = ON")


def ensure_jobs_failed_status(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if row is None or "'failed'" in row[0]:
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """
        CREATE TABLE jobs_new (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            job_description TEXT DEFAULT '' NOT NULL,
            url TEXT NOT NULL,
            platform TEXT NOT NULL CHECK (platform IN ('linkedin', 'indeed', 'jobright', 'monster', 'simplify')),
            date_posted DATETIME,
            easy_apply INTEGER DEFAULT 0 NOT NULL CHECK (easy_apply IN (0, 1)),
            scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'new' NOT NULL CHECK (
                status IN ('new', 'queued', 'reviewed', 'applied', 'skip', 'interview', 'offer', 'rejected', 'failed')
            ),
            resume_version_id INTEGER,
            ats_score REAL,
            notes TEXT,
            applied_at DATETIME,
            FOREIGN KEY (resume_version_id) REFERENCES resume_versions (id) ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO jobs_new (
            id, title, company, location, job_description, url, platform, date_posted, easy_apply,
            scraped_at, status, resume_version_id, ats_score, notes, applied_at
        )
        SELECT
            id, title, company, location, job_description, url, platform, date_posted, easy_apply,
            scraped_at, status, resume_version_id, ats_score, notes, applied_at
        FROM jobs
        """
    )
    connection.execute("DROP TABLE jobs")
    connection.execute("ALTER TABLE jobs_new RENAME TO jobs")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs (company)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_jobs_platform ON jobs (platform)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_jobs_scraped_at ON jobs (scraped_at)")
    connection.execute("PRAGMA foreign_keys = ON")


def main() -> None:
    db_path = init_database()
    rel_path = db_path.relative_to(PROJECT_ROOT)
    print(f"Initialized SQLite database at {rel_path}")
    print(f"ChromaDB persistence folder ready at {settings.chroma_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
