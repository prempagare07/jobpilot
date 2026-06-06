import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";

export function PaginationControls({
  offset,
  limit,
  count,
  onPrevious,
  onNext,
}: {
  offset: number;
  limit: number;
  count: number;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-t px-4 py-3">
      <p className="text-xs text-muted-foreground">
        Showing {count ? offset + 1 : 0}-{offset + count}
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={onPrevious} disabled={offset === 0}>
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          Prev
        </Button>
        <Button size="sm" variant="outline" onClick={onNext} disabled={count < limit}>
          Next
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
