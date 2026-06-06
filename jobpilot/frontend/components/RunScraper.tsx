"use client";

import * as React from "react";
import useSWR from "swr";
import { Play, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, type ScraperStatus } from "@/lib/api";

export function RunScraper({ onDone }: { onDone?: () => void }) {
  const [taskId, setTaskId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const { data: status } = useSWR<ScraperStatus>(
    taskId ? `/api/scraper/status?task=${taskId}` : null,
    () => api.scraperStatus(),
    { refreshInterval: taskId ? 1500 : 0 },
  );
  const task = status?.active_tasks.find((item) => item.task_id === taskId);

  React.useEffect(() => {
    if (taskId && status && !task) {
      onDone?.();
      setTaskId(null);
    }
  }, [onDone, status, task, taskId]);

  async function run() {
    setError(null);
    try {
      const response = await api.runScraper();
      setTaskId(response.task_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start scraper");
    }
  }

  const running = Boolean(taskId && (!status || task));
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={run} disabled={running}>
        {running ? (
          <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <Play className="h-4 w-4" aria-hidden="true" />
        )}
        {running ? "Running" : "Run Scraper Now"}
      </Button>
      {task?.status && <span className="text-xs text-muted-foreground">{task.status}</span>}
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
