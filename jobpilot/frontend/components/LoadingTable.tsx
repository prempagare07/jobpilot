import { Skeleton } from "@/components/ui/skeleton";

export function LoadingTable({ rows = 6 }: { rows?: number }) {
  return (
    <div className="rounded-md border bg-card">
      <div className="border-b px-4 py-3">
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="divide-y">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="grid gap-3 px-4 py-4 sm:grid-cols-4">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ))}
      </div>
    </div>
  );
}
