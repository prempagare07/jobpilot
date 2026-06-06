import Link from "next/link";

import { ATSBadge } from "@/components/ATSBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Job } from "@/lib/api";

export function JobCard({ job }: { job: Job }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="leading-snug">{job.title}</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">{job.company}</p>
          </div>
          <ATSBadge score={job.ats_score} />
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={job.status} />
          <span className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground">{job.platform}</span>
        </div>
        <p className="max-h-16 overflow-hidden text-sm text-muted-foreground">{job.job_description}</p>
        <Button asChild size="sm" variant="outline">
          <Link href={`/jobs/${job.id}`}>View Details</Link>
        </Button>
      </CardContent>
    </Card>
  );
}
