PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    email TEXT NOT NULL,
    phone TEXT,
    location TEXT,
    address_street TEXT,
    address_city TEXT,
    address_state TEXT,
    address_zip TEXT,
    address_country TEXT,
    linkedin_url TEXT,
    github_url TEXT,
    portfolio_url TEXT,
    work_authorization TEXT CHECK (work_authorization IN ('US Citizen', 'GC', 'H1B', 'OPT', 'CPT')),
    years_experience INTEGER DEFAULT 0 NOT NULL,
    willing_to_relocate INTEGER DEFAULT 0 NOT NULL CHECK (willing_to_relocate IN (0, 1)),
    salary_min INTEGER,
    salary_max INTEGER,
    summary TEXT,
    skills_json TEXT DEFAULT '[]' NOT NULL,
    education_json TEXT DEFAULT '[]' NOT NULL,
    target_roles_json TEXT DEFAULT '[]' NOT NULL,
    preferred_locations_json TEXT DEFAULT '[]' NOT NULL,
    experience_json TEXT DEFAULT '[]' NOT NULL,
    projects_json TEXT DEFAULT '[]' NOT NULL,
    achievements_json TEXT DEFAULT '[]' NOT NULL,
    certifications_json TEXT DEFAULT '[]' NOT NULL,
    languages_json TEXT DEFAULT '[]' NOT NULL,
    application_preferences_json TEXT DEFAULT '{}' NOT NULL,
    eeo_json TEXT DEFAULT '{}' NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    target_roles TEXT DEFAULT '[]' NOT NULL,
    keywords_json TEXT DEFAULT '[]' NOT NULL,
    ats_score_avg REAL DEFAULT 0 NOT NULL,
    is_active INTEGER DEFAULT 0 NOT NULL CHECK (is_active IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
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
    status TEXT DEFAULT 'new' NOT NULL CHECK (status IN ('new', 'queued', 'reviewed', 'applied', 'skip', 'interview', 'offer', 'rejected', 'failed')),
    resume_version_id INTEGER,
    ats_score REAL,
    notes TEXT,
    applied_at DATETIME,
    FOREIGN KEY (resume_version_id) REFERENCES resume_versions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs (company);
CREATE INDEX IF NOT EXISTS ix_jobs_platform ON jobs (platform);
CREATE INDEX IF NOT EXISTS ix_jobs_scraped_at ON jobs (scraped_at);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    resume_version_id INTEGER,
    resume_name TEXT,
    cover_letter_text TEXT,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    follow_up_sent_at DATETIME,
    status TEXT DEFAULT 'applied' NOT NULL,
    apply_status TEXT DEFAULT 'pending' NOT NULL,
    ats_platform TEXT,
    task_id TEXT,
    questions_encountered_json TEXT DEFAULT '[]' NOT NULL,
    questions_needing_human_json TEXT DEFAULT '[]' NOT NULL,
    failure_reason TEXT,
    screenshot_url TEXT,
    response_received INTEGER DEFAULT 0 NOT NULL CHECK (response_received IN (0, 1)),
    notes TEXT,
    audit_log_json TEXT DEFAULT '[]' NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE,
    FOREIGN KEY (resume_version_id) REFERENCES resume_versions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_applications_job_id ON applications (job_id);
CREATE INDEX IF NOT EXISTS ix_applications_status ON applications (status);
CREATE INDEX IF NOT EXISTS ix_applications_apply_status ON applications (apply_status);
CREATE INDEX IF NOT EXISTS ix_applications_task_id ON applications (task_id);

CREATE TABLE IF NOT EXISTS qa_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL UNIQUE,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    answer_type TEXT DEFAULT 'text' NOT NULL CHECK (answer_type IN ('text', 'yesno', 'number', 'select')),
    confidence REAL DEFAULT 0 NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    times_used INTEGER DEFAULT 0 NOT NULL,
    last_used_at DATETIME,
    source TEXT DEFAULT 'user_provided' NOT NULL CHECK (source IN ('user_provided', 'ai_generated')),
    tags_json TEXT DEFAULT '[]' NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_qa_memory_question_hash ON qa_memory (question_hash);

CREATE TABLE IF NOT EXISTS outreach_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    title TEXT,
    email TEXT,
    email_status TEXT,
    company TEXT NOT NULL,
    seniority TEXT,
    department TEXT,
    linkedin_url TEXT,
    job_id TEXT,
    email_sent INTEGER DEFAULT 0 NOT NULL CHECK (email_sent IN (0, 1)),
    email_sent_at DATETIME,
    follow_up_sent_at DATETIME,
    email_subject TEXT,
    email_body TEXT,
    reply_received INTEGER DEFAULT 0 NOT NULL CHECK (reply_received IN (0, 1)),
    reply_at DATETIME,
    notes TEXT,
    apollo_id TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_outreach_contacts_email ON outreach_contacts (email);
CREATE INDEX IF NOT EXISTS ix_outreach_contacts_company ON outreach_contacts (company);
CREATE INDEX IF NOT EXISTS ix_outreach_contacts_job_id ON outreach_contacts (job_id);
CREATE INDEX IF NOT EXISTS ix_outreach_contacts_apollo_id ON outreach_contacts (apollo_id);

CREATE TABLE IF NOT EXISTS apollo_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usage_date TEXT NOT NULL,
    operation TEXT NOT NULL,
    credits_used INTEGER DEFAULT 1 NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_apollo_usage_date ON apollo_usage (usage_date);

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    subject_template TEXT NOT NULL,
    body_template TEXT NOT NULL,
    role_type TEXT DEFAULT 'General' NOT NULL CHECK (role_type IN ('AI Engineer', 'SDE', 'General')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    is_active INTEGER DEFAULT 1 NOT NULL CHECK (is_active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS ix_email_templates_role_type ON email_templates (role_type);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL CHECK (run_type IN ('scrape', 'apply', 'outreach')),
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    finished_at DATETIME,
    jobs_found INTEGER DEFAULT 0 NOT NULL,
    jobs_applied INTEGER DEFAULT 0 NOT NULL,
    errors_json TEXT DEFAULT '[]' NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_scheduler_runs_run_type ON scheduler_runs (run_type);
CREATE INDEX IF NOT EXISTS ix_scheduler_runs_started_at ON scheduler_runs (started_at);
