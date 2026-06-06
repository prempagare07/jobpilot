"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, CheckCircle2, ExternalLink, MailSearch, RefreshCw, Save, Send, UserRound } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  type ApplyTaskResult,
  type Job,
  type PreparedApplication,
  type Profile,
  type ResumeVersion,
  type TaskStatus,
} from "@/lib/api";
import { formatDate } from "@/lib/format";

export default function JobDetailPage({ params }: { params: { id: string } }) {
  const { data: job, mutate, isLoading, error } = useSWR<Job>(`job-${params.id}`, () => api.getJob(params.id));
  const { data: resumes } = useSWR<ResumeVersion[]>("detail-resumes", api.listResumes);
  const { data: profile, error: profileError } = useSWR<Profile>("detail-profile", api.getProfile, {
    shouldRetryOnError: false,
  });
  const [coverLetter, setCoverLetter] = React.useState("");
  const [notes, setNotes] = React.useState("");
  const [message, setMessage] = React.useState<string | null>(null);
  const [preparing, setPreparing] = React.useState(false);
  const [prepAttempted, setPrepAttempted] = React.useState(false);
  const [prepared, setPrepared] = React.useState<PreparedApplication | null>(null);
  const [taskId, setTaskId] = React.useState<string | null>(null);
  const { data: applyTask } = useSWR<TaskStatus>(
    taskId ? `apply-task-${taskId}` : null,
    () => api.getApplyStatus(taskId as string),
    {
      refreshInterval: (latest) =>
        latest?.status === "queued" || latest?.status === "running" ? 2000 : 0,
    },
  );

  const prepareApplication = React.useCallback(
    async (silent = false): Promise<PreparedApplication | null> => {
      setPreparing(true);
      if (!silent) setMessage(null);
      try {
        const result = await api.prepareJob(params.id);
        setPrepared(result);
        setCoverLetter(result.cover_letter_text);
        setNotes(result.job.notes ?? "");
        await mutate(result.job, false);
        if (!silent) {
          setMessage(`Prepared application with ${result.resume_name}. ATS score: ${result.ats_result.score}.`);
        }
        return result;
      } catch (err) {
        setMessage(err instanceof Error ? err.message : "Could not prepare application");
        return null;
      } finally {
        setPreparing(false);
      }
    },
    [mutate, params.id],
  );

  React.useEffect(() => {
    if (job?.notes) setNotes(job.notes);
  }, [job?.notes]);

  React.useEffect(() => {
    if (!job || prepAttempted) return;
    const profileResolved = Boolean(profile) || Boolean(profileError);
    const needsScore = !jobHasPreparedApplication(job);
    const needsCoverLetter = Boolean(profile) && !coverLetter.trim();
    if (!needsScore && !needsCoverLetter && !profileResolved) return;
    setPrepAttempted(true);
    if (needsScore || needsCoverLetter) {
      void prepareApplication(true);
    }
  }, [coverLetter, job, prepAttempted, prepareApplication, profile, profileError]);

  React.useEffect(() => {
    if (applyTask?.status === "succeeded" || applyTask?.status === "failed") {
      void mutate();
    }
  }, [applyTask?.status, mutate]);

  if (isLoading) {
    return (
      <>
        <PageHeader title="Job Detail" description="Loading job details..." />
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="h-[560px] rounded-md border bg-slate-50" />
          <div className="h-[560px] rounded-md border bg-slate-50" />
        </div>
      </>
    );
  }

  if (error || !job) {
    return (
      <>
        <PageHeader title="Job Detail" description="This job could not be loaded." />
        <EmptyState title="Job not found" description={error instanceof Error ? error.message : "No job data returned."} />
      </>
    );
  }

  const parsedNotes = parseAtsNotes(job.notes);
  const selectedResume = resumes?.find((resume) => resume.id === (prepared?.resume_version_id ?? job.resume_version_id));
  const taskResult = applyTask?.result as ApplyTaskResult | undefined;
  const profileMissing = Boolean(profileError) && !profile;
  const taskError = taskResult?.error ?? applyTask?.error;
  const automationStatus = taskResult?.status ?? applyTask?.status ?? "Not started";
  const applyDisabled = applyTask?.status === "queued" || applyTask?.status === "running";

  async function applyToJob() {
    setMessage(null);
    try {
      const draft = coverLetter.trim() || undefined;
      const task = await api.applyToJob(params.id, job?.status === "reviewed", draft);
      setTaskId(task.task_id);
      setMessage(
        profile
          ? `Application task started: ${task.task_id}. Watch Application Progress below for the final URL, screenshot, and questions.`
          : `Application check started: ${task.task_id}. It will pause in Applications until your profile is complete.`,
      );
      await mutate();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not start application");
    }
  }

  async function findRecruiter() {
    setMessage(null);
    try {
      const result = await api.findRecruiters(params.id);
      setMessage(`Found ${result.contacts_found} contacts. Open Outreach to preview emails.`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not find recruiters");
    }
  }

  async function saveNotes() {
    setMessage(null);
    try {
      await api.updateJobNotes(params.id, notes);
      setMessage("Notes saved.");
      await mutate();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not save notes");
    }
  }

  async function refreshJD() {
    setMessage("Fetching full job description from source URL…");
    try {
      await api.refreshJobDescription(params.id);
      setMessage("Job description updated from source.");
      await mutate();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not fetch job description");
    }
  }

  return (
    <>
      <PageHeader
        title={job.title}
        description={`${job.company} - ${job.location ?? "Location not listed"} - ${formatDate(job.scraped_at)}`}
        actions={
          <>
            <StatusBadge status={job.status} />
            <Button size="sm" variant="outline" onClick={() => void refreshJD()}>
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              Refresh JD
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link href={job.url} target="_blank">
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                Source
              </Link>
            </Button>
          </>
        }
      />

      {message && (
        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {message}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_390px]">
        <section className="rounded-md border bg-card">
          <div className="border-b px-5 py-4">
            <h2 className="text-base font-semibold">Job Description</h2>
            <p className="mt-1 text-sm text-muted-foreground">{job.platform} listing</p>
          </div>
          <div className="max-h-[720px] overflow-y-auto whitespace-pre-wrap px-5 py-4 text-sm leading-6 text-slate-800">
            {job.job_description}
          </div>
        </section>

        <aside className="grid gap-5">
          <Card>
            <CardHeader>
              <CardTitle>ATS Match</CardTitle>
              <CardDescription>Resume fit from the local scorer.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              <ScoreRing score={job.ats_score ?? 0} />
              <KeywordBlock label="Matched keywords" items={parsedNotes.matched_keywords} tone="green" />
              <KeywordBlock label="Missing keywords" items={parsedNotes.missing_keywords} tone="red" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Cover Letter</CardTitle>
              <CardDescription>
                {preparing ? "Generating draft and scoring resumes..." : "Generated draft used by Apply."}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              <Textarea
                rows={8}
                value={coverLetter}
                onChange={(event) => setCoverLetter(event.target.value)}
                placeholder="Draft or paste a cover letter preview here."
              />
              <div className="rounded-md border px-3 py-2 text-sm">
                <span className="text-muted-foreground">Selected resume: </span>
                <span className="font-medium">{prepared?.resume_name ?? selectedResume?.name ?? "Not selected"}</span>
                <Button asChild size="sm" variant="ghost" className="ml-2">
                  <Link href="/resumes">Change</Link>
                </Button>
              </div>
              {prepared?.warnings.length ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {prepared.warnings.map((warning) => (
                    <p key={warning}>{warning}</p>
                  ))}
                </div>
              ) : null}
              {profileMissing ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  <div className="flex items-start gap-2">
                    <UserRound className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    <div className="grid gap-1">
                      <p className="font-medium">Profile required before applying</p>
                      <p className="text-xs">ATS scoring can run with your resume, but cover letters and form answers need your profile.</p>
                      <Link className="text-xs font-semibold underline" href="/settings">
                        Open Settings
                      </Link>
                    </div>
                  </div>
                </div>
              ) : null}
              <Button variant="outline" onClick={() => void prepareApplication()} disabled={preparing}>
                {preparing ? (
                  <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                )}
                {preparing ? "Preparing" : "Prepare Application"}
              </Button>
              <div className="grid grid-cols-2 gap-2">
                <Button onClick={() => void applyToJob()} disabled={applyDisabled}>
                  <Send className="h-4 w-4" aria-hidden="true" />
                  Apply
                </Button>
                <Button variant="outline" onClick={() => void findRecruiter()}>
                  <MailSearch className="h-4 w-4" aria-hidden="true" />
                  Recruiter
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Application Progress</CardTitle>
              <CardDescription>Live status from the apply automation task.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 text-sm">
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <span className="text-muted-foreground">Task</span>
                <span className="font-medium">{automationStatus}</span>
              </div>
              {applyTask?.task_id && (
                <div className="flex items-center justify-between rounded-md border px-3 py-2 text-xs">
                  <span className="text-muted-foreground">Task ID</span>
                  <span className="max-w-48 truncate font-mono">{applyTask.task_id}</span>
                </div>
              )}
              {taskResult?.final_url && (
                <Button asChild variant="outline">
                  <Link href={taskResult.final_url} target="_blank">
                    <ExternalLink className="h-4 w-4" aria-hidden="true" />
                    Open Application URL
                  </Link>
                </Button>
              )}
              {taskResult?.screenshot_url && (
                <Button asChild variant="outline">
                  <Link href={api.screenshotUrl(taskResult.screenshot_url) ?? "#"} target="_blank">
                    <ExternalLink className="h-4 w-4" aria-hidden="true" />
                    View Preflight Screenshot
                  </Link>
                </Button>
              )}
              {taskResult?.reason && (
                <div className="rounded-md border px-3 py-2">
                  <span className="text-muted-foreground">Reason: </span>
                  <span className="font-medium">{taskResult.reason}</span>
                </div>
              )}
              {taskError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                  {taskError}
                </div>
              )}
              {taskResult?.questions_needing_human?.length ? (
                <QuestionList title="Needs your answer" questions={taskResult.questions_needing_human} urgent />
              ) : null}
              {taskResult?.questions_encountered?.length ? (
                <QuestionList title="Questions seen on form" questions={taskResult.questions_encountered} />
              ) : null}
              {taskResult?.questions_needing_human?.length ? (
                <Button asChild variant="outline">
                  <Link href="/qa">Open Q&amp;A Memory</Link>
                </Button>
              ) : null}
              <Button asChild variant="outline">
                <Link href="/applications">View Applications</Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Notes</CardTitle>
              <CardDescription>Scorer output and manual review notes.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              <Textarea rows={8} value={notes} onChange={(event) => setNotes(event.target.value)} />
              <Button size="sm" onClick={() => void saveNotes()}>
                <Save className="h-4 w-4" aria-hidden="true" />
                Save Notes
              </Button>
            </CardContent>
          </Card>
        </aside>
      </div>
    </>
  );
}

function QuestionList({ title, questions, urgent = false }: { title: string; questions: string[]; urgent?: boolean }) {
  return (
    <div className={`rounded-md border px-3 py-2 ${urgent ? "border-amber-200 bg-amber-50" : ""}`}>
      <p className="mb-2 flex items-center gap-2 font-medium">
        {urgent && <AlertTriangle className="h-4 w-4 text-amber-700" aria-hidden="true" />}
        {title}
      </p>
      <ul className="grid gap-1 text-xs text-muted-foreground">
        {questions.slice(0, 12).map((question) => (
          <li key={question}>{question}</li>
        ))}
      </ul>
    </div>
  );
}

function ScoreRing({ score }: { score: number }) {
  const normalized = Math.max(0, Math.min(100, Math.round(score)));
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (normalized / 100) * circumference;
  const stroke =
    normalized >= 75 ? "stroke-emerald-500" : normalized >= 50 ? "stroke-amber-500" : "stroke-red-500";

  return (
    <div className="flex items-center justify-center">
      <svg viewBox="0 0 110 110" className="h-36 w-36" aria-label={`ATS score ${normalized}`}>
        <circle cx="55" cy="55" r={radius} className="fill-none stroke-slate-100" strokeWidth="10" />
        <circle
          cx="55"
          cy="55"
          r={radius}
          className={`fill-none ${stroke}`}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 55 55)"
        />
        <text x="55" y="58" textAnchor="middle" className="fill-slate-950 text-xl font-semibold">
          {normalized}
        </text>
      </svg>
    </div>
  );
}

function KeywordBlock({ label, items, tone }: { label: string; items: string[]; tone: "green" | "red" }) {
  const color =
    tone === "green"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : "border-red-200 bg-red-50 text-red-700";
  return (
    <div>
      <p className="mb-2 text-sm font-medium text-slate-800">{label}</p>
      <div className="flex flex-wrap gap-2">
        {items.length ? (
          items.slice(0, 18).map((item) => (
            <Badge key={item} className={color}>
              {item}
            </Badge>
          ))
        ) : (
          <span className="text-sm text-muted-foreground">No keywords stored yet.</span>
        )}
      </div>
    </div>
  );
}

function parseAtsNotes(notes?: string | null) {
  return {
    matched_keywords: extractList(notes, "matched_keywords"),
    missing_keywords: extractList(notes, "missing_keywords"),
  };
}

function jobHasPreparedApplication(job: Job) {
  const notes = parseAtsNotes(job.notes);
  return Boolean(
    job.resume_version_id &&
      (job.ats_score ?? 0) > 0 &&
      (notes.matched_keywords.length > 0 || notes.missing_keywords.length > 0),
  );
}

function extractList(notes: string | null | undefined, key: string) {
  if (!notes) return [];
  const match = notes.match(new RegExp(`${key}'?:\\s*\\[(.*?)\\]`));
  if (!match) return [];
  return Array.from(match[1].matchAll(/'([^']+)'|"([^"]+)"/g))
    .map((item) => item[1] || item[2])
    .filter(Boolean);
}
