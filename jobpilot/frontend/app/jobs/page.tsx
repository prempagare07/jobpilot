"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { CheckSquare, Eye, MailSearch, Send, XCircle } from "lucide-react";

import { ATSBadge } from "@/components/ATSBadge";
import { EmptyState } from "@/components/EmptyState";
import { LoadingTable } from "@/components/LoadingTable";
import { PageHeader } from "@/components/PageHeader";
import { PaginationControls } from "@/components/PaginationControls";
import { RunScraper } from "@/components/RunScraper";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, type Job, type JobStatus } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;
const statuses = ["all", "new", "queued", "reviewed", "applied", "skip", "interview", "offer", "rejected"];
const platforms = ["linkedin", "indeed", "jobright", "monster", "simplify"];

type SortMode = "ats" | "date" | "company";

export default function JobsPage() {
  const [status, setStatus] = React.useState("all");
  const [selectedPlatforms, setSelectedPlatforms] = React.useState<string[]>([]);
  const [minAts, setMinAts] = React.useState(0);
  const [sort, setSort] = React.useState<SortMode>("ats");
  const [offset, setOffset] = React.useState(0);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [message, setMessage] = React.useState<string | null>(null);

  const singlePlatform = selectedPlatforms.length === 1 ? selectedPlatforms[0] : undefined;
  const swrKey = ["jobs", status, singlePlatform ?? "all", minAts, offset].join(":");
  const {
    data: jobs,
    mutate,
    isLoading,
  } = useSWR<Job[]>(swrKey, () =>
    api.listJobs({
      status: status === "all" ? undefined : status,
      platform: singlePlatform,
      min_ats: minAts || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
  );

  const visibleJobs = React.useMemo(() => {
    const filtered =
      selectedPlatforms.length > 1
        ? (jobs ?? []).filter((job) => selectedPlatforms.includes(job.platform))
        : jobs ?? [];
    return [...filtered].sort((left, right) => {
      if (sort === "company") return left.company.localeCompare(right.company);
      if (sort === "date") {
        return new Date(right.scraped_at).getTime() - new Date(left.scraped_at).getTime();
      }
      return (right.ats_score ?? -1) - (left.ats_score ?? -1);
    });
  }, [jobs, selectedPlatforms, sort]);

  function togglePlatform(platform: string) {
    setOffset(0);
    setSelectedPlatforms((current) =>
      current.includes(platform) ? current.filter((item) => item !== platform) : [...current, platform],
    );
  }

  function toggleSelected(jobId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }

  async function optimisticStatus(jobId: string, nextStatus: JobStatus) {
    const previous = jobs ?? [];
    void mutate(
      previous.map((job) => (job.id === jobId ? { ...job, status: nextStatus } : job)),
      false,
    );
    try {
      await api.updateJobStatus(jobId, nextStatus);
      await mutate();
    } catch (error) {
      void mutate(previous, false);
      setMessage(error instanceof Error ? error.message : "Could not update job status");
    }
  }

  async function applyOne(job: Job) {
    setMessage(null);
    try {
      const task = await api.applyToJob(job.id);
      setMessage(`Application started for ${job.company}: ${task.task_id}`);
      await mutate();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start application");
    }
  }

  async function applySelected() {
    const ids = Array.from(selected);
    if (!ids.length) {
      setMessage("Select at least one job to apply.");
      return;
    }
    setMessage(null);
    try {
      const task = await api.batchApply(ids);
      setSelected(new Set());
      setMessage(`Batch apply started: ${task.task_id}`);
      await mutate();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start batch apply");
    }
  }

  async function findRecruiter(job: Job) {
    setMessage(null);
    try {
      const result = await api.findRecruiters(job.id);
      setMessage(`Found ${result.contacts_found} contacts for ${job.company}. Email previews are in Outreach.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not find recruiters");
    }
  }

  return (
    <>
      <PageHeader
        title="Job Queue"
        description="Review, rank, and move jobs through the local apply pipeline."
        actions={
          <>
            <RunScraper onDone={() => void mutate()} />
            <Button size="sm" onClick={applySelected}>
              <Send className="h-4 w-4" aria-hidden="true" />
              Apply All Selected
            </Button>
          </>
        }
      />

      {message && (
        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {message}
        </div>
      )}

      <Card className="mb-5">
        <CardContent className="grid gap-4 p-4 lg:grid-cols-[180px_1fr_220px_180px]">
          <label className="grid gap-1 text-sm">
            <span className="font-medium text-slate-800">Status</span>
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              {statuses.map((item) => (
                <option key={item} value={item}>
                  {item === "all" ? "All statuses" : item}
                </option>
              ))}
            </Select>
          </label>

          <div className="grid gap-1 text-sm">
            <span className="font-medium text-slate-800">Platforms</span>
            <div className="flex flex-wrap gap-2">
              {platforms.map((platform) => (
                <Button
                  key={platform}
                  type="button"
                  size="sm"
                  variant={selectedPlatforms.includes(platform) ? "secondary" : "outline"}
                  onClick={() => togglePlatform(platform)}
                >
                  <CheckSquare
                    className={cn("h-4 w-4", !selectedPlatforms.includes(platform) && "opacity-35")}
                    aria-hidden="true"
                  />
                  {platform}
                </Button>
              ))}
            </div>
          </div>

          <label className="grid gap-2 text-sm">
            <span className="font-medium text-slate-800">Min ATS: {minAts}</span>
            <Slider
              min={0}
              max={100}
              step={5}
              value={minAts}
              onChange={(event) => {
                setMinAts(Number(event.target.value));
                setOffset(0);
              }}
            />
          </label>

          <label className="grid gap-1 text-sm">
            <span className="font-medium text-slate-800">Sort by</span>
            <Select value={sort} onChange={(event) => setSort(event.target.value as SortMode)}>
              <option value="ats">ATS score</option>
              <option value="date">Date</option>
              <option value="company">Company</option>
            </Select>
          </label>
        </CardContent>
      </Card>

      {isLoading ? (
        <LoadingTable rows={8} />
      ) : visibleJobs.length ? (
        <div className="overflow-hidden rounded-md border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <span className="sr-only">Select</span>
                </TableHead>
                <TableHead>ATS</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Platform</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleJobs.map((job) => (
                <TableRow key={job.id}>
                  <TableCell>
                    <input
                      type="checkbox"
                      checked={selected.has(job.id)}
                      onChange={() => toggleSelected(job.id)}
                      aria-label={`Select ${job.title}`}
                    />
                  </TableCell>
                  <TableCell>
                    <ATSBadge score={job.ats_score} />
                  </TableCell>
                  <TableCell className="max-w-72">
                    <Link href={`/jobs/${job.id}`} className="font-medium text-slate-950 hover:text-primary">
                      {job.title}
                    </Link>
                    <p className="truncate text-xs text-muted-foreground">{job.location}</p>
                  </TableCell>
                  <TableCell>{job.company}</TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="capitalize">
                      {job.platform}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={job.status} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(job.scraped_at)}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-2">
                      <Button size="icon" variant="outline" asChild aria-label="View details">
                        <Link href={`/jobs/${job.id}`}>
                          <Eye className="h-4 w-4" aria-hidden="true" />
                        </Link>
                      </Button>
                      <Button size="icon" variant="outline" onClick={() => void applyOne(job)} aria-label="Apply">
                        <Send className="h-4 w-4" aria-hidden="true" />
                      </Button>
                      <Button
                        size="icon"
                        variant="outline"
                        onClick={() => void optimisticStatus(job.id, "skip")}
                        aria-label="Skip"
                      >
                        <XCircle className="h-4 w-4" aria-hidden="true" />
                      </Button>
                      <Button
                        size="icon"
                        variant="outline"
                        onClick={() => void findRecruiter(job)}
                        aria-label="Find recruiter"
                      >
                        <MailSearch className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <PaginationControls
            offset={offset}
            limit={PAGE_SIZE}
            count={visibleJobs.length}
            onPrevious={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            onNext={() => setOffset(offset + PAGE_SIZE)}
          />
        </div>
      ) : (
        <EmptyState
          title="No jobs match these filters"
          description="Try lowering the ATS threshold or run the scraper to refresh the queue."
          action={<RunScraper onDone={() => void mutate()} />}
        />
      )}
    </>
  );
}
