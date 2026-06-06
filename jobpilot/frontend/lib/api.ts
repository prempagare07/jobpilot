export const API_BASE_URL = "http://localhost:8000";

export type JobStatus =
  | "new"
  | "queued"
  | "reviewed"
  | "applied"
  | "skip"
  | "interview"
  | "offer"
  | "rejected"
  | "failed";

export interface HealthResponse {
  status: string;
  ollama: boolean;
  db: boolean;
}

export interface Profile {
  id: number;
  full_name: string;
  first_name?: string | null;
  last_name?: string | null;
  email: string;
  phone?: string | null;
  location?: string | null;
  address_street?: string | null;
  address_city?: string | null;
  address_state?: string | null;
  address_zip?: string | null;
  address_country?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  portfolio_url?: string | null;
  work_authorization?: "US Citizen" | "GC" | "H1B" | "OPT" | "CPT" | null;
  years_experience: number;
  willing_to_relocate: boolean;
  salary_min?: number | null;
  salary_max?: number | null;
  summary?: string | null;
  skills_json: string[];
  education_json: Array<{ school: string; degree: string; year: string }>;
  target_roles_json: string[];
  preferred_locations_json: string[];
  experience_json: ExperienceItem[];
  projects_json: ProjectItem[];
  achievements_json: string[];
  certifications_json: string[];
  languages_json: string[];
  application_preferences_json: ApplicationPreferences;
  eeo_json: EEOData;
  created_at?: string;
  updated_at?: string;
}

export type ProfileInput = Omit<Profile, "id" | "created_at" | "updated_at">;

export interface ExperienceItem {
  company: string;
  title: string;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  highlights: string[];
  technologies: string[];
}

export interface ProjectItem {
  name: string;
  url?: string | null;
  description: string;
  impact?: string | null;
  technologies: string[];
}

export interface ApplicationPreferences {
  remote_preference?: string | null;
  employment_types: string[];
  earliest_start_date?: string | null;
  notice_period?: string | null;
  preferred_timezone?: string | null;
  requires_sponsorship?: boolean | null;
  sponsorship_notes?: string | null;
  open_to_background_check?: boolean | null;
  open_to_drug_test?: boolean | null;
}

export interface EEOData {
  gender?: string | null;
  race_ethnicity?: string | null;
  veteran_status?: string | null;
  disability_status?: string | null;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  location?: string | null;
  job_description: string;
  url: string;
  platform: string;
  date_posted?: string | null;
  easy_apply: boolean;
  scraped_at: string;
  status: JobStatus;
  resume_version_id?: number | null;
  ats_score?: number | null;
  notes?: string | null;
  applied_at?: string | null;
}

export interface JobListParams {
  status?: string;
  platform?: string;
  min_ats?: number;
  limit?: number;
  offset?: number;
}

export interface JobStats {
  by_status: Record<string, number>;
  by_platform: Record<string, number>;
  count_today: number;
}

export interface TaskResponse {
  task_id: string;
}

export interface TaskStatus {
  task_id: string;
  name: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: unknown;
  error?: string | null;
}

export interface ResumeVersion {
  id: number;
  name: string;
  file_path: string;
  target_roles: string[];
  keywords_json: string[];
  ats_score_avg: number;
  is_active: boolean;
  created_at?: string;
}

export interface ATSResult {
  resume_name: string;
  score: number;
  matched_keywords: string[];
  missing_keywords: string[];
  recommendation: string;
  tailoring_suggestions: string[];
}

export interface PreparedApplication {
  job_id: string;
  resume_version_id: number;
  resume_name: string;
  ats_result: ATSResult;
  cover_letter_text: string;
  warnings: string[];
  job: Job;
}

export interface ApplyTaskResult {
  job_id: string;
  status: string;
  success: boolean;
  screenshot_path?: string | null;
  screenshot_url?: string | null;
  questions?: string[];
  questions_encountered?: string[];
  questions_needing_human?: string[];
  final_url?: string | null;
  reason?: string | null;
  error?: string | null;
}

export interface Application {
  id: number;
  job_id: string;
  resume_version_id?: number | null;
  resume_name?: string | null;
  resume_path?: string | null;
  cover_letter_text?: string | null;
  cover_letter_path?: string | null;
  applied_at: string;
  follow_up_sent_at?: string | null;
  status: string;
  apply_status: string;
  ats_platform?: string | null;
  task_id?: string | null;
  questions_encountered_json: string[];
  questions_needing_human_json: string[];
  failure_reason?: string | null;
  screenshot_url?: string | null;
  response_received: boolean;
  notes?: string | null;
  audit_log_json: AuditEntry[];
  job?: {
    id: string;
    title: string;
    company: string;
    platform: string;
    status: string;
    ats_score?: number | null;
  } | null;
}

export interface AuditEntry {
  ts: string;
  event: "navigate" | "fill" | "upload" | "step" | "submit" | "error" | "info" | "skip";
  // fill
  field?: string;
  value?: string;
  field_type?: string;
  selector?: string;
  // upload
  file?: string;
  // step
  action?: string;
  page?: number;
  // navigate / submit
  url?: string;
  // error / info / skip
  message?: string;
  reason?: string;
}

export interface QAMemory {
  id: number;
  question_hash: string;
  question_text: string;
  answer_text: string;
  answer_type: string;
  confidence: number;
  times_used: number;
  last_used_at?: string | null;
  source: string;
  tags_json: string[];
}

export interface QAAnswer {
  answer: string;
  confidence: number;
  source: string;
  question_hash: string;
}

export interface OutreachContact {
  id: number;
  name: string;
  title?: string | null;
  email?: string | null;
  email_status?: string | null;
  company: string;
  seniority?: string | null;
  department?: string | null;
  linkedin_url?: string | null;
  job_id?: string | null;
  email_sent: boolean;
  email_sent_at?: string | null;
  follow_up_sent_at?: string | null;
  email_subject?: string | null;
  email_body?: string | null;
  reply_received: boolean;
  reply_at?: string | null;
  notes?: string | null;
  apollo_id?: string | null;
}

export interface OutreachPreview {
  contact_id: number;
  contact_name: string;
  email?: string | null;
  subject: string;
  body: string;
  personalization_notes: string[];
}

export interface OutreachResult {
  contacts_found: number;
  emails_sent: number;
  skipped: number;
  previews: OutreachPreview[];
}

export interface OutreachStats {
  sent: number;
  opened: number;
  replied: number;
  reply_rate: number;
}

export interface EmailSendResult {
  success: boolean;
  message_id: string;
  error?: string | null;
}

export interface DashboardStats {
  jobs_scraped_total: number;
  jobs_applied_total: number;
  jobs_applied_today: number;
  daily_limit: number;
  interviews: number;
  offers: number;
  emails_sent: number;
  reply_rate: number;
  top_platforms: Array<{ platform: string; count: number }>;
  applications_by_week: Array<{ week: string; count: number }>;
  avg_ats_score: number;
  last_scrape?: string | null;
}

export interface SchedulerRun {
  id: number;
  run_type: string;
  started_at: string;
  finished_at?: string | null;
  jobs_found: number;
  jobs_applied: number;
  errors_json: string[];
}

export interface ScraperStatus {
  last_run?: SchedulerRun | null;
  active_tasks: TaskStatus[];
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  const shouldSendJsonHeader = Boolean(init?.body) && !(init?.body instanceof FormData);
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        ...(shouldSendJsonHeader ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "network request failed";
    throw new Error(`FastAPI backend is not reachable at ${API_BASE_URL}: ${detail}`);
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload.detail || payload.message || message;
    } catch {
      const text = await response.text();
      message = text || message;
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function qs(params: object) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (
      value !== undefined &&
      value !== null &&
      value !== "" &&
      (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
    ) {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  health: () => apiFetch<HealthResponse>("/health"),

  getProfile: () => apiFetch<Profile>("/api/profile/"),
  createProfile: (profile: ProfileInput) =>
    apiFetch<Profile>("/api/profile/", { method: "POST", body: JSON.stringify(profile) }),
  updateProfile: (profile: ProfileInput) =>
    apiFetch<Profile>("/api/profile/", { method: "PUT", body: JSON.stringify(profile) }),
  setupProfileComplete: () => apiFetch<{ ok: boolean; qa_memory_seeded: number; previous_count: number }>(
    "/api/profile/setup-complete",
    { method: "POST" },
  ),

  listJobs: (params: JobListParams = {}) => apiFetch<Job[]>(`/api/jobs/${qs(params)}`),
  getJob: (id: string) => apiFetch<Job>(`/api/jobs/${id}`),
  prepareJob: (id: string) =>
    apiFetch<PreparedApplication>(`/api/jobs/${id}/prepare`, { method: "POST" }),
  updateJobStatus: (id: string, status: JobStatus) =>
    apiFetch<Job>(`/api/jobs/${id}/status`, { method: "PUT", body: JSON.stringify({ status }) }),
  updateJobNotes: (id: string, notes: string | null) =>
    apiFetch<Job>(`/api/jobs/${id}/notes`, { method: "PUT", body: JSON.stringify({ notes }) }),
  refreshJobDescription: (id: string) =>
    apiFetch<Job>(`/api/jobs/${id}/refresh-jd`, { method: "POST" }),
  triggerJobScrape: () => apiFetch<TaskResponse>("/api/jobs/trigger-scrape", { method: "POST" }),
  jobStats: () => apiFetch<JobStats>("/api/jobs/stats"),

  listApplications: () => apiFetch<Application[]>("/api/applications/"),
  getApplication: (id: number) => apiFetch<Application>(`/api/applications/${id}`),
  deleteApplication: (id: number) => apiFetch<{ deleted: boolean; id: number }>(`/api/applications/${id}`, { method: "DELETE" }),
  retryApplication: (id: number, coverLetterText?: string) =>
    apiFetch<TaskResponse>(`/api/applications/${id}/retry`, {
      method: "POST",
      body: JSON.stringify({ human_approved: true, cover_letter_text: coverLetterText }),
    }),
  applyToJob: (jobId: string, humanApproved = false, coverLetterText?: string) =>
    apiFetch<TaskResponse>(`/api/applications/apply/${jobId}`, {
      method: "POST",
      body: JSON.stringify({ human_approved: humanApproved, cover_letter_text: coverLetterText }),
    }),
  getApplyStatus: (taskId: string) => apiFetch<TaskStatus>(`/api/applications/apply/status/${taskId}`),
  batchApply: (jobIds: string[], delayBetween = 30, humanApproved = false) =>
    apiFetch<TaskResponse>("/api/applications/apply/batch", {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds, delay_between: delayBetween, human_approved: humanApproved }),
    }),
  applicationsToday: () => apiFetch<Application[]>("/api/applications/today"),

  qaMemory: () => apiFetch<QAMemory[]>("/api/qa/memory"),
  qaPending: () => apiFetch<QAMemory[]>("/api/qa/pending"),
  answerQuestion: (questionHash: string, answer: string) =>
    apiFetch<QAMemory>("/api/qa/answer", {
      method: "POST",
      body: JSON.stringify({ question_hash: questionHash, answer }),
    }),
  updateMemory: (id: number, payload: Partial<QAMemory>) =>
    apiFetch<QAMemory>(`/api/qa/memory/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteMemory: (id: number) => apiFetch<{ deleted: boolean }>(`/api/qa/memory/${id}`, { method: "DELETE" }),
  testQA: (question: string, context: Record<string, unknown> = {}) =>
    apiFetch<QAAnswer>("/api/qa/test", { method: "POST", body: JSON.stringify({ question, context }) }),

  listResumes: () => apiFetch<ResumeVersion[]>("/api/resumes/"),
  uploadResume: (formData: FormData) =>
    apiFetch<ResumeVersion>("/api/resumes/upload", { method: "POST", body: formData }),
  updateResume: (id: number, payload: Partial<Pick<ResumeVersion, "name" | "target_roles" | "is_active">>) =>
    apiFetch<ResumeVersion>(`/api/resumes/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deactivateResume: (id: number) => apiFetch<{ deactivated: boolean }>(`/api/resumes/${id}`, { method: "DELETE" }),
  scoreResume: (id: number, jobDescription: string) =>
    apiFetch<ATSResult>(`/api/resumes/${id}/score`, {
      method: "POST",
      body: JSON.stringify({ job_description: jobDescription }),
    }),
  resumeDownloadUrl: (id: number) => `${API_BASE_URL}/api/resumes/${id}/download`,
  screenshotUrl: (path?: string | null) => {
    if (!path) return null;
    if (path.startsWith("http")) return path;
    if (path.startsWith("/screenshots/")) return `${API_BASE_URL}${path}`;
    return `${API_BASE_URL}/screenshots/${path.split("/").pop()}`;
  },

  dashboardStats: () => apiFetch<DashboardStats>("/api/stats/dashboard"),
  runScraper: () => apiFetch<TaskResponse>("/api/scraper/run", { method: "POST" }),
  scraperStatus: () => apiFetch<ScraperStatus>("/api/scraper/status"),
  scraperRuns: () => apiFetch<SchedulerRun[]>("/api/scraper/runs"),

  outreachContacts: () => apiFetch<OutreachContact[]>("/api/outreach/contacts"),
  findRecruiters: (jobId: string) => apiFetch<OutreachResult>(`/api/outreach/find/${jobId}`, { method: "POST" }),
  sendContact: (contactId: number | string) =>
    apiFetch<EmailSendResult>(`/api/outreach/send/${contactId}`, { method: "POST" }),
  sendBulkForJob: (jobId: string, approved = false) =>
    apiFetch<OutreachResult>(`/api/outreach/send-bulk/${jobId}${qs({ approved })}`, { method: "POST" }),
  markReplied: (contactId: number | string) =>
    apiFetch<{ updated: boolean }>(`/api/outreach/contacts/${contactId}/replied`, { method: "PUT" }),
  outreachStats: () => apiFetch<OutreachStats>("/api/outreach/stats"),
};

export const swrFetcher = <T>(path: string) => apiFetch<T>(path);
