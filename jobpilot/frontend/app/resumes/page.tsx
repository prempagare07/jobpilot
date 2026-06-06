"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { Download, FileUp, Gauge, ToggleLeft, ToggleRight, X } from "lucide-react";

import { ATSBadge } from "@/components/ATSBadge";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, type ATSResult, type ResumeVersion } from "@/lib/api";
import { formatDate, splitCsv } from "@/lib/format";

export default function ResumesPage() {
  const {
    data: resumes,
    mutate,
    isLoading,
    error: resumesError,
  } = useSWR<ResumeVersion[]>("resumes", api.listResumes);
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [testResume, setTestResume] = React.useState<ResumeVersion | null>(null);
  const [message, setMessage] = React.useState<string | null>(null);

  async function toggleActive(resume: ResumeVersion) {
    setMessage(null);
    try {
      if (resume.is_active) {
        await api.deactivateResume(resume.id);
      } else {
        await api.updateResume(resume.id, { is_active: true });
      }
      await mutate();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update resume");
    }
  }

  return (
    <>
      <PageHeader
        title="Resumes"
        description="Manage resume versions and test ATS fit against any job description."
        actions={
          <Button size="sm" onClick={() => setUploadOpen(true)}>
            <FileUp className="h-4 w-4" aria-hidden="true" />
            Upload New Resume
          </Button>
        }
      />

      {message && (
        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {message}
        </div>
      )}
      {resumesError && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Resume list refresh failed:{" "}
          {resumesError instanceof Error ? resumesError.message : "Could not fetch resumes."}
        </div>
      )}

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="h-52 rounded-md border bg-slate-50" />
          ))}
        </div>
      ) : resumes?.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {resumes.map((resume) => (
            <Card key={resume.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle className="leading-snug">{resume.name}</CardTitle>
                    <CardDescription>Uploaded {formatDate(resume.created_at)}</CardDescription>
                  </div>
                  <Badge variant={resume.is_active ? "default" : "secondary"}>
                    {resume.is_active ? "active" : "inactive"}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="flex flex-wrap gap-2">
                  {resume.target_roles.length ? (
                    resume.target_roles.map((role) => (
                      <Badge key={role} variant="secondary">
                        {role}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-sm text-muted-foreground">No target roles set.</span>
                  )}
                </div>
                <div className="flex items-center justify-between rounded-md border px-3 py-2">
                  <span className="text-sm text-muted-foreground">Avg ATS score</span>
                  <ATSBadge score={resume.ats_score_avg} />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => setTestResume(resume)}>
                    <Gauge className="h-4 w-4" aria-hidden="true" />
                    Test ATS
                  </Button>
                  <Button asChild size="sm" variant="outline">
                    <Link href={api.resumeDownloadUrl(resume.id)} target="_blank">
                      <Download className="h-4 w-4" aria-hidden="true" />
                      Download
                    </Link>
                  </Button>
                  <Button
                    size="sm"
                    variant={resume.is_active ? "outline" : "default"}
                    onClick={() => void toggleActive(resume)}
                  >
                    {resume.is_active ? (
                      <><ToggleRight className="h-4 w-4" aria-hidden="true" /> Deactivate</>
                    ) : (
                      <><ToggleLeft className="h-4 w-4" aria-hidden="true" /> Activate</>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No resumes yet"
          description="Upload a PDF resume to start scoring jobs and applying."
          action={
            <Button onClick={() => setUploadOpen(true)}>
              <FileUp className="h-4 w-4" aria-hidden="true" />
              Upload Resume
            </Button>
          }
        />
      )}

      <UploadResumeModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={(uploadedResume) => {
          setUploadOpen(false);
          setMessage("Resume uploaded.");
          void mutate((current = []) => [uploadedResume, ...current.filter((resume) => resume.id !== uploadedResume.id)], {
            revalidate: false,
          });
          void mutate().catch((error) => {
            setMessage(
              error instanceof Error
                ? `Resume uploaded, but refresh failed: ${error.message}`
                : "Resume uploaded, but refresh failed.",
            );
          });
        }}
      />
      <TestAtsModal resume={testResume} onClose={() => setTestResume(null)} onScored={() => void mutate()} />
    </>
  );
}

function UploadResumeModal({
  open,
  onClose,
  onUploaded,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: (resume: ResumeVersion) => void;
}) {
  const [name, setName] = React.useState("");
  const [roles, setRoles] = React.useState("");
  const [file, setFile] = React.useState<File | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);

  if (!open) return null;

  async function upload() {
    if (!file || !name.trim()) {
      setError("Add a resume name and PDF file.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.set("name", name.trim());
      formData.set("target_roles_json", JSON.stringify(splitCsv(roles)));
      formData.set("is_active", "true");
      formData.set("file", file);
      const uploadedResume = await api.uploadResume(formData);
      onUploaded(uploadedResume);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not upload resume");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/70 p-4">
      <div className="mx-auto max-w-xl rounded-md bg-card shadow-xl">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <h2 className="text-base font-semibold">Upload Resume</h2>
          <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="grid gap-4 p-5">
          <Input placeholder="Resume name" value={name} onChange={(event) => setName(event.target.value)} />
          <Input
            placeholder="Target roles, comma separated"
            value={roles}
            onChange={(event) => setRoles(event.target.value)}
          />
          <Input
            type="file"
            accept="application/pdf"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Button onClick={() => void upload()} disabled={saving}>
            {saving ? "Uploading" : "Upload"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TestAtsModal({
  resume,
  onClose,
  onScored,
}: {
  resume: ResumeVersion | null;
  onClose: () => void;
  onScored: () => void;
}) {
  const [jobDescription, setJobDescription] = React.useState("");
  const [result, setResult] = React.useState<ATSResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [scoring, setScoring] = React.useState(false);

  React.useEffect(() => {
    setJobDescription("");
    setResult(null);
    setError(null);
  }, [resume?.id]);

  if (!resume) return null;
  const activeResume = resume;

  async function score() {
    setScoring(true);
    setError(null);
    try {
      const next = await api.scoreResume(activeResume.id, jobDescription);
      setResult(next);
      onScored();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not score resume");
    } finally {
      setScoring(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/70 p-4">
      <div className="mx-auto flex h-full max-w-5xl flex-col rounded-md bg-card shadow-xl">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <h2 className="text-base font-semibold">Test ATS: {activeResume.name}</h2>
          <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="grid min-h-0 flex-1 gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Textarea
            className="min-h-96 resize-none"
            value={jobDescription}
            onChange={(event) => setJobDescription(event.target.value)}
            placeholder="Paste a job description here."
          />
          <div className="grid content-start gap-4">
            <Button onClick={() => void score()} disabled={scoring || !jobDescription.trim()}>
              {scoring ? "Scoring" : "Score Resume"}
            </Button>
            {error && <p className="text-sm text-red-600">{error}</p>}
            {result && (
              <div className="grid gap-4 rounded-md border p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Score</span>
                  <ATSBadge score={result.score} />
                </div>
                <p className="text-sm">{result.recommendation}</p>
                <div>
                  <p className="mb-2 text-sm font-medium">Suggestions</p>
                  <ul className="grid gap-2 text-sm text-muted-foreground">
                    {result.tailoring_suggestions.map((suggestion) => (
                      <li key={suggestion}>{suggestion}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
