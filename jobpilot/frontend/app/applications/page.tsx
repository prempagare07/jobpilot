"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock, ExternalLink, FileText, Globe, Info, RefreshCw, SkipForward, Trash2, Upload, XCircle } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { LoadingTable } from "@/components/LoadingTable";
import { PageHeader } from "@/components/PageHeader";
import { PaginationControls } from "@/components/PaginationControls";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, type Application, type AuditEntry, API_BASE_URL } from "@/lib/api";
import { formatDate } from "@/lib/format";

const STATUS_FILTERS = [
  { value: "all",           label: "All" },
  { value: "applied",       label: "Submitted" },
  { value: "needs_human",   label: "Needs Review" },
  { value: "waiting_review",label: "Review Browser" },
  { value: "waiting_captcha",label: "CAPTCHA" },
  { value: "running",       label: "Running" },
  { value: "failed",        label: "Failed" },
  { value: "pending",       label: "Pending" },
];

const PAGE_SIZE = 50;

const APPLY_STATUS_CONFIG: Record<string, { label: string; icon: React.ReactNode; className: string }> = {
  pending:          { label: "Pending",          icon: <Clock className="h-3 w-3" />,                      className: "bg-slate-100 text-slate-700 border-slate-200" },
  preparing:        { label: "Preparing",        icon: <Clock className="h-3 w-3 animate-spin" />,          className: "bg-cyan-50 text-cyan-700 border-cyan-200" },
  running:          { label: "Running",          icon: <Clock className="h-3 w-3 animate-spin" />,          className: "bg-blue-50 text-blue-700 border-blue-200" },
  waiting_review:   { label: "Review Browser",   icon: <AlertTriangle className="h-3 w-3 animate-pulse" />, className: "bg-violet-50 text-violet-800 border-violet-200" },
  waiting_captcha:  { label: "CAPTCHA — Act Now",icon: <AlertTriangle className="h-3 w-3 animate-pulse" />, className: "bg-orange-100 text-orange-800 border-orange-300" },
  applied:          { label: "Submitted",        icon: <CheckCircle2 className="h-3 w-3" />,                className: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  needs_human:      { label: "Needs Review",     icon: <AlertTriangle className="h-3 w-3" />,               className: "bg-amber-50 text-amber-700 border-amber-200" },
  failed:           { label: "Failed",           icon: <XCircle className="h-3 w-3" />,                     className: "bg-red-50 text-red-700 border-red-200" },
};

function ApplyStatusBadge({ status }: { status: string }) {
  const cfg = APPLY_STATUS_CONFIG[status] ?? { label: status, icon: null, className: "bg-slate-100 text-slate-600" };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${cfg.className}`}>
      {cfg.icon}
      {cfg.label}
    </span>
  );
}

function PlatformBadge({ platform }: { platform?: string | null }) {
  if (!platform) return <span className="text-muted-foreground text-xs">—</span>;
  const colors: Record<string, string> = {
    linkedin: "bg-blue-100 text-blue-800",
    indeed: "bg-indigo-100 text-indigo-800",
    greenhouse: "bg-green-100 text-green-800",
    lever: "bg-purple-100 text-purple-800",
    workday: "bg-orange-100 text-orange-800",
    bamboohr: "bg-teal-100 text-teal-800",
    generic: "bg-slate-100 text-slate-700",
    unknown: "bg-slate-100 text-slate-500",
  };
  const cls = colors[platform.toLowerCase()] ?? "bg-slate-100 text-slate-700";
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}>
      {platform}
    </span>
  );
}

const AUDIT_EVENT_CONFIG: Record<AuditEntry["event"], { icon: React.ReactNode; rowCls: string; label: string }> = {
  navigate: { icon: <Globe className="h-3 w-3" />,       rowCls: "bg-blue-50 text-blue-800",    label: "Navigate"  },
  fill:     { icon: <FileText className="h-3 w-3" />,    rowCls: "bg-white text-slate-800",      label: "Fill"      },
  upload:   { icon: <Upload className="h-3 w-3" />,      rowCls: "bg-indigo-50 text-indigo-800", label: "Upload"    },
  step:     { icon: <ChevronRight className="h-3 w-3" />,rowCls: "bg-slate-100 text-slate-700",  label: "Step"      },
  submit:   { icon: <CheckCircle2 className="h-3 w-3" />,rowCls: "bg-emerald-50 text-emerald-800",label: "Submit"  },
  error:    { icon: <XCircle className="h-3 w-3" />,     rowCls: "bg-red-50 text-red-800",       label: "Error"     },
  info:     { icon: <Info className="h-3 w-3" />,        rowCls: "bg-slate-50 text-slate-600",   label: "Info"      },
  skip:     { icon: <SkipForward className="h-3 w-3" />, rowCls: "bg-amber-50 text-amber-700",   label: "Skip"      },
};

function auditSummary(entry: AuditEntry): string {
  switch (entry.event) {
    case "navigate": return entry.url ?? "";
    case "fill":     return `${entry.field ?? "?"} → ${entry.value ?? ""}${entry.field_type ? ` (${entry.field_type})` : ""}`;
    case "upload":   return `${entry.field ?? "file"} ← ${entry.file ?? ""}`;
    case "step":     return entry.action ?? "";
    case "submit":   return entry.url ? `Submitted at ${entry.url}` : "Submit clicked";
    case "error":    return entry.message ?? "";
    case "info":     return entry.message ?? "";
    case "skip":     return `${entry.field ?? "?"} — ${entry.reason ?? "skipped"}`;
    default:         return JSON.stringify(entry);
  }
}

function AuditTrail({ entries }: { entries: AuditEntry[] }) {
  if (!entries.length) {
    return <p className="text-xs text-muted-foreground italic">No audit trail recorded for this application.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b bg-slate-50 text-left text-slate-500">
            <th className="px-2 py-1.5 font-medium w-28">Time</th>
            <th className="px-2 py-1.5 font-medium w-20">Event</th>
            <th className="px-2 py-1.5 font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => {
            const cfg = AUDIT_EVENT_CONFIG[entry.event] ?? AUDIT_EVENT_CONFIG.info;
            const isError = entry.event === "error";
            return (
              <tr key={i} className={`border-b last:border-0 ${cfg.rowCls}`}>
                <td className="px-2 py-1 font-mono whitespace-nowrap opacity-70">
                  {entry.ts.replace("T", " ").replace("+00:00", "Z")}
                </td>
                <td className="px-2 py-1">
                  <span className="inline-flex items-center gap-1 font-medium">
                    {cfg.icon}{cfg.label}
                  </span>
                </td>
                <td className="px-2 py-1 max-w-lg">
                  {isError ? (
                    <details>
                      <summary className="cursor-pointer truncate max-w-sm">
                        {(entry.message ?? "").split("\n")[0]}
                      </summary>
                      <pre className="mt-1 whitespace-pre-wrap font-mono text-[10px] text-red-900 leading-4">
                        {entry.message}
                      </pre>
                    </details>
                  ) : (
                    <span className="break-all">{auditSummary(entry)}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ExpandedRow({
  application,
  onRetry,
  onDelete,
}: {
  application: Application;
  onRetry: () => void;
  onDelete: () => void;
}) {
  const [retrying, setRetrying] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const screenshotHref = application.screenshot_url
    ? application.screenshot_url.startsWith("http")
      ? application.screenshot_url
      : `${API_BASE_URL}${application.screenshot_url}`
    : null;

  const isRetryable = application.apply_status === "failed" || application.apply_status === "needs_human";
  const isLive = ["pending", "preparing", "running", "waiting_review", "waiting_captcha"].includes(application.apply_status);

  async function handleRetry() {
    setRetrying(true);
    try {
      await api.retryApplication(application.id);
      onRetry();
    } finally {
      setRetrying(false);
    }
  }

  async function handleDelete() {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setDeleting(true);
    try {
      await api.deleteApplication(application.id);
      onDelete();
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  return (
    <TableRow className="bg-slate-50 hover:bg-slate-50">
      <TableCell colSpan={8} className="px-8 pb-4 pt-2">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {/* Resume & Cover Letter */}
          <div className="grid gap-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Resume Used</p>
            {application.resume_path ? (
              <Link
                href={`${API_BASE_URL}/api/applications/${application.id}/resume-pdf`}
                target="_blank"
                className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline"
              >
                <FileText className="h-3.5 w-3.5" />
                {application.resume_name ?? "View Resume"}
              </Link>
            ) : (
              <p className="text-sm text-slate-800">
                {application.resume_name ?? (application.resume_version_id ? `Resume #${application.resume_version_id}` : "Not yet selected")}
              </p>
            )}
          </div>

          <div className="grid gap-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Cover Letter</p>
            {application.cover_letter_path ? (
              <div className="grid gap-1">
                <Link
                  href={`${API_BASE_URL}/api/applications/${application.id}/cover-letter-pdf`}
                  target="_blank"
                  className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline"
                >
                  <FileText className="h-3.5 w-3.5" />
                  View Cover Letter PDF
                </Link>
                {application.cover_letter_text && (
                  <p className="text-xs text-slate-500 line-clamp-2 mt-0.5">{application.cover_letter_text.slice(0, 120)}…</p>
                )}
              </div>
            ) : (
              <p className="text-sm text-slate-700 line-clamp-3">
                {application.cover_letter_text ?? "Not yet generated"}
              </p>
            )}
          </div>

          {/* Failure reason / success note */}
          <div className="col-span-full grid gap-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Outcome</p>
            {application.failure_reason ? (
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 font-mono">
                {application.failure_reason}
              </pre>
            ) : application.apply_status === "applied" ? (
              <p className="text-sm text-emerald-700">Application submitted successfully.</p>
            ) : (
              <p className="text-sm text-muted-foreground">—</p>
            )}
          </div>

          {/* Questions needing human */}
          {application.questions_needing_human_json?.length ? (
            <div className="col-span-full grid gap-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Unanswered Questions</p>
              <ul className="grid gap-1">
                {application.questions_needing_human_json.map((q, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-amber-900">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
                    {q}
                  </li>
                ))}
              </ul>
              <Button asChild size="sm" variant="outline" className="mt-1 w-fit">
                <Link href="/qa">Answer in Q&A Memory</Link>
              </Button>
            </div>
          ) : null}

          {/* Questions encountered */}
          {application.questions_encountered_json?.length ? (
            <div className="col-span-full grid gap-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Questions Seen on Form ({application.questions_encountered_json.length})
              </p>
              <div className="flex flex-wrap gap-1">
                {application.questions_encountered_json.slice(0, 20).map((q, i) => (
                  <span key={i} className="rounded border bg-white px-2 py-0.5 text-xs text-slate-600">{q}</span>
                ))}
              </div>
            </div>
          ) : null}

          {/* Audit trail */}
          <div className="col-span-full grid gap-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 flex items-center gap-2">
              Audit Trail ({application.audit_log_json?.length ?? 0} events)
              {isLive && <span className="inline-flex items-center gap-1 text-blue-600 font-normal normal-case"><Clock className="h-3 w-3 animate-spin" /> Live</span>}
            </p>
            <AuditTrail entries={application.audit_log_json ?? []} />
          </div>

          {/* CAPTCHA instruction banner */}
          {application.apply_status === "waiting_review" && (
            <div className="col-span-full rounded-md border-2 border-violet-300 bg-violet-50 px-4 py-3 text-sm text-violet-950">
              <p className="font-bold text-base mb-1">Review needed in the visible browser</p>
              <p>The form has been filled. Make any edits in the browser window, then click the site&apos;s Submit button.</p>
              <p className="mt-1 text-xs text-violet-700">JobPilot is watching for the confirmation page and will update this row automatically.</p>
            </div>
          )}

          {/* CAPTCHA instruction banner */}
          {application.apply_status === "waiting_captcha" && (
            <div className="col-span-full rounded-md border-2 border-orange-400 bg-orange-50 px-4 py-3 text-sm text-orange-900 animate-pulse">
              <p className="font-bold text-base mb-1">⚠️ Action required — CAPTCHA detected</p>
              <p>A browser window has opened with the form pre-filled.</p>
              <ul className="mt-1 list-disc list-inside space-y-0.5">
                <li><strong>Visible CAPTCHA</strong> (checkbox / image): solve it — we&apos;ll auto-submit once done.</li>
                <li><strong>Ashby / invisible reCAPTCHA</strong>: just click <strong>&quot;Submit Application&quot;</strong> in that window.</li>
              </ul>
              <p className="mt-1 text-xs text-orange-700">This page updates automatically. Status will change to &quot;Submitted&quot; once complete.</p>
            </div>
          )}

          {/* Resume tip for needs_human */}
          {application.apply_status === "needs_human" && (
            <div className="col-span-full rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <span className="font-semibold">To resume:</span> answer the unanswered questions in{" "}
              <Link href="/qa" className="underline font-medium">Q&amp;A Memory</Link>, then click{" "}
              <span className="font-semibold">Resume Application</span> below.
            </div>
          )}

          {/* Actions */}
          <div className="col-span-full flex flex-wrap gap-2">
            {screenshotHref && (
              <Button asChild size="sm" variant="outline">
                <Link href={screenshotHref} target="_blank">
                  <ExternalLink className="h-3 w-3" />
                  View Screenshot
                </Link>
              </Button>
            )}
            <Button asChild size="sm" variant="outline">
              <Link href={`/jobs/${application.job_id}`}>
                <ExternalLink className="h-3 w-3" />
                Open Job
              </Link>
            </Button>
            {isRetryable && (
              <Button size="sm" variant={application.apply_status === "needs_human" ? "default" : "outline"} onClick={() => void handleRetry()} disabled={retrying}>
                <RefreshCw className={`h-3 w-3 ${retrying ? "animate-spin" : ""}`} />
                {retrying ? "Resuming…" : application.apply_status === "needs_human" ? "Resume Application" : "Retry"}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              className={confirmDelete ? "border-red-300 bg-red-50 text-red-700 hover:bg-red-100" : undefined}
              onClick={() => void handleDelete()}
              disabled={deleting}
            >
              <Trash2 className="h-3 w-3" />
              {deleting ? "Deleting…" : confirmDelete ? "Confirm delete" : "Delete"}
            </Button>
            {confirmDelete && !deleting && (
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      </TableCell>
    </TableRow>
  );
}

export default function ApplicationsPage() {
  const { data: applications, isLoading, mutate } = useSWR<Application[]>("applications", api.listApplications, {
    refreshInterval: (data) =>
      data?.some((a) => ["pending", "preparing", "running", "waiting_review", "waiting_captcha"].includes(a.apply_status)) ? 2000 : 0,
  });
  const [offset, setOffset] = React.useState(0);
  const [expanded, setExpanded] = React.useState<Set<number>>(new Set());
  const [statusFilter, setStatusFilter] = React.useState("all");

  const all = applications ?? [];
  const submittedCount = all.filter((a) => a.apply_status === "applied").length;
  const filtered = statusFilter === "all" ? all : all.filter((a) => a.apply_status === statusFilter);
  const page = filtered.slice(offset, offset + PAGE_SIZE);

  function toggleRow(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function handleFilterChange(value: string) {
    setStatusFilter(value);
    setOffset(0);
  }

  return (
    <>
      <PageHeader
        title="Applications"
        description="Every apply attempt — pending, submitted, failed, and needs-review."
      />

      {/* Stats bar + filters */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg border bg-emerald-50 px-4 py-2 text-center min-w-[80px]">
            <p className="text-2xl font-bold text-emerald-700">{submittedCount}</p>
            <p className="text-xs text-emerald-600">Submitted</p>
          </div>
          <div className="rounded-lg border bg-slate-50 px-4 py-2 text-center min-w-[80px]">
            <p className="text-2xl font-bold text-slate-700">{all.length}</p>
            <p className="text-xs text-slate-500">Total</p>
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((f) => {
            const count = f.value === "all" ? all.length : all.filter((a) => a.apply_status === f.value).length;
            const active = statusFilter === f.value;
            return (
              <button
                key={f.value}
                onClick={() => handleFilterChange(f.value)}
                className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  active
                    ? "bg-slate-800 text-white border-slate-800"
                    : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                }`}
              >
                {f.label}
                {count > 0 && (
                  <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${active ? "bg-white/20 text-white" : "bg-slate-100 text-slate-500"}`}>
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {isLoading ? (
        <LoadingTable rows={8} />
      ) : page.length ? (
        <div className="overflow-hidden rounded-md border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead className="w-10 text-center text-slate-400">#</TableHead>
                <TableHead>Applied</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Platform</TableHead>
                <TableHead>Automation</TableHead>
                <TableHead>Tracking</TableHead>
                <TableHead>Resume</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.map((application, idx) => (
                <React.Fragment key={application.id}>
                  <TableRow
                    className="cursor-pointer"
                    onClick={() => toggleRow(application.id)}
                  >
                    <TableCell className="text-muted-foreground">
                      {expanded.has(application.id) ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </TableCell>
                    <TableCell className="text-center text-xs text-slate-400 font-mono">
                      {offset + idx + 1}
                    </TableCell>
                    <TableCell className="text-muted-foreground whitespace-nowrap">
                      {formatDate(application.applied_at)}
                    </TableCell>
                    <TableCell>{application.job?.company ?? "Unknown"}</TableCell>
                    <TableCell className="max-w-60 font-medium truncate">
                      {application.job?.title ?? application.job_id}
                    </TableCell>
                    <TableCell>
                      <PlatformBadge platform={application.ats_platform ?? application.job?.platform} />
                    </TableCell>
                    <TableCell>
                      <ApplyStatusBadge status={application.apply_status ?? "applied"} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={application.job?.status ?? application.status} />
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground truncate max-w-32">
                      {application.resume_name ?? (application.resume_version_id ? `#${application.resume_version_id}` : "—")}
                    </TableCell>
                  </TableRow>
                  {expanded.has(application.id) && (
                    <ExpandedRow
                      application={application}
                      onRetry={() => void mutate()}
                      onDelete={() => void mutate()}
                    />
                  )}
                </React.Fragment>
              ))}
            </TableBody>
          </Table>
          <PaginationControls
            offset={offset}
            limit={PAGE_SIZE}
            count={filtered.length}
            onPrevious={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            onNext={() => setOffset(offset + PAGE_SIZE)}
          />
        </div>
      ) : (
        <EmptyState
          title={statusFilter === "all" ? "No applications yet" : `No ${STATUS_FILTERS.find(f => f.value === statusFilter)?.label ?? statusFilter} applications`}
          description={statusFilter === "all" ? "Every time you click Apply on a job, an entry appears here immediately — even before the automation finishes." : "Try a different filter above."}
        />
      )}
    </>
  );
}
