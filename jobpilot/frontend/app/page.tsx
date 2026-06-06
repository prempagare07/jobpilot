"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  BriefcaseBusiness,
  CheckCircle2,
  Clock3,
  Mail,
  MessageSquare,
  Search,
  Send,
  Trophy,
  type LucideIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { LoadingTable } from "@/components/LoadingTable";
import { PageHeader } from "@/components/PageHeader";
import { RunScraper } from "@/components/RunScraper";
import { StatusBadge } from "@/components/StatusBadge";
import { ATSBadge } from "@/components/ATSBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api, type Application, type DashboardStats, type Job, type OutreachStats } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

type ActivityItem = {
  id: string;
  label: string;
  detail: string;
  at?: string | null;
  status?: string;
};

export default function DashboardPage() {
  const { data: stats, mutate: mutateStats, isLoading: statsLoading } = useSWR<DashboardStats>(
    "dashboard-stats",
    api.dashboardStats,
  );
  const { data: jobs, mutate: mutateJobs, isLoading: jobsLoading } = useSWR<Job[]>(
    "dashboard-jobs",
    () => api.listJobs({ limit: 10 }),
  );
  const { data: queuedJobs, mutate: mutateQueued } = useSWR<Job[]>(
    "dashboard-queued-jobs",
    () => api.listJobs({ status: "queued", limit: 50 }),
  );
  const { data: applications } = useSWR<Application[]>("dashboard-applications", api.listApplications);
  const { data: outreachStats } = useSWR<OutreachStats>("dashboard-outreach", api.outreachStats);
  const [applyMessage, setApplyMessage] = React.useState<string | null>(null);
  const [applying, setApplying] = React.useState(false);

  const appliedToday = stats?.jobs_applied_today ?? 0;
  const dailyLimit = stats?.daily_limit ?? 20;
  const appliedProgress = Math.min(100, (appliedToday / Math.max(dailyLimit, 1)) * 100);

  const activity = React.useMemo(() => {
    const jobActivity: ActivityItem[] = (jobs ?? []).map((job) => ({
      id: `job-${job.id}`,
      label: `${job.company} - ${job.title}`,
      detail: `Job ${job.status} from ${job.platform}`,
      at: job.scraped_at,
      status: job.status,
    }));
    const applicationActivity: ActivityItem[] = (applications ?? []).slice(0, 10).map((application) => ({
      id: `application-${application.id}`,
      label: `${application.job?.company ?? "Company"} - ${application.job?.title ?? application.job_id}`,
      detail: `Application ${application.status}`,
      at: application.applied_at,
      status: application.status,
    }));
    return [...jobActivity, ...applicationActivity]
      .sort((left, right) => new Date(right.at ?? 0).getTime() - new Date(left.at ?? 0).getTime())
      .slice(0, 10);
  }, [applications, jobs]);

  async function applyToQueue() {
    const ids = (queuedJobs ?? []).map((job) => job.id);
    if (!ids.length) {
      setApplyMessage("Queue is empty. Move jobs to queued before batch applying.");
      return;
    }
    setApplying(true);
    setApplyMessage(null);
    try {
      const task = await api.batchApply(ids);
      setApplyMessage(`Batch apply started: ${task.task_id}`);
      await Promise.all([mutateStats(), mutateJobs(), mutateQueued()]);
    } catch (error) {
      setApplyMessage(error instanceof Error ? error.message : "Could not start batch apply");
    } finally {
      setApplying(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Live view of the local job automation pipeline."
        actions={
          <>
            <RunScraper
              onDone={() => {
                void mutateStats();
                void mutateJobs();
                void mutateQueued();
              }}
            />
            <Button size="sm" variant="outline" onClick={applyToQueue} disabled={applying}>
              <Send className="h-4 w-4" aria-hidden="true" />
              {applying ? "Starting" : "Apply to Queue"}
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link href="/outreach">
                <Search className="h-4 w-4" aria-hidden="true" />
                Find Recruiters
              </Link>
            </Button>
          </>
        }
      />

      {applyMessage && (
        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {applyMessage}
        </div>
      )}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          loading={statsLoading}
          label="Jobs scraped"
          value={`${stats?.jobs_scraped_total ?? 0}`}
          detail={`${stats?.top_platforms?.[0]?.platform ?? "No source"} leading`}
          icon={BriefcaseBusiness}
        />
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
            <CardDescription>Applications today</CardDescription>
            <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden="true" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold">
              {appliedToday}/{dailyLimit}
            </div>
            <Progress className="mt-3" value={appliedProgress} />
          </CardContent>
        </Card>
        <MetricCard
          loading={statsLoading}
          label="Reply rate"
          value={`${outreachStats?.reply_rate ?? stats?.reply_rate ?? 0}%`}
          detail={`${stats?.emails_sent ?? 0} emails sent`}
          icon={Mail}
        />
        <MetricCard
          loading={statsLoading}
          label="Interviews"
          value={`${stats?.interviews ?? 0}`}
          detail={`${stats?.offers ?? 0} offers`}
          icon={Trophy}
        />
      </section>

      <section className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
            <CardDescription>Last 10 status changes and fresh jobs.</CardDescription>
          </CardHeader>
          <CardContent>
            {jobsLoading ? (
              <LoadingTable rows={5} />
            ) : activity.length ? (
              <div className="overflow-hidden rounded-md border">
                <div className="divide-y">
                  {activity.map((item) => (
                    <div key={item.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-slate-950">{item.label}</p>
                        <p className="text-xs text-muted-foreground">{item.detail}</p>
                      </div>
                      <div className="flex items-center gap-3">
                        <StatusBadge status={item.status} />
                        <span className="text-xs text-muted-foreground">{formatDateTime(item.at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <EmptyState title="No activity yet" description="Run a scrape to pull your first local job queue." />
            )}
          </CardContent>
        </Card>

        <div className="grid gap-5">
          <Card>
            <CardHeader>
              <CardTitle>Pipeline Health</CardTitle>
              <CardDescription>Latest scrape and scoring summary.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 text-sm">
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Clock3 className="h-4 w-4 text-accent" aria-hidden="true" />
                  Last scrape
                </span>
                <span className="font-medium">{formatDateTime(stats?.last_scrape)}</span>
              </div>
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <span className="text-muted-foreground">Average ATS</span>
                <ATSBadge score={stats?.avg_ats_score ?? null} />
              </div>
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <span className="text-muted-foreground">Queued jobs</span>
                <Badge variant="secondary">{queuedJobs?.length ?? 0}</Badge>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Top Platforms</CardTitle>
              <CardDescription>Where jobs are coming from.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2">
              {(stats?.top_platforms ?? []).length ? (
                stats?.top_platforms.map((platform) => (
                  <div key={platform.platform} className="flex items-center justify-between text-sm">
                    <span className="capitalize text-muted-foreground">{platform.platform}</span>
                    <span className="font-medium">{platform.count}</span>
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">No platform data yet.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Q&A Queue</CardTitle>
              <CardDescription>Questions that need your answer.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button asChild variant="outline" className="w-full">
                <Link href="/qa">
                  <MessageSquare className="h-4 w-4" aria-hidden="true" />
                  Review Pending Questions
                </Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </section>
    </>
  );
}

function MetricCard({
  loading,
  label,
  value,
  detail,
  icon: Icon,
}: {
  loading?: boolean;
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardDescription>{label}</CardDescription>
        <Icon className="h-4 w-4 text-primary" aria-hidden />
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold">{loading ? "--" : value}</div>
        <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}
