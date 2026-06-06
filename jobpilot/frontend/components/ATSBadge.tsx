import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function ATSBadge({ score }: { score?: number | null }) {
  const value = typeof score === "number" ? Math.round(score) : null;
  const color =
    value === null
      ? "border-slate-200 bg-slate-50 text-slate-600"
      : value >= 75
        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
        : value >= 50
          ? "border-amber-200 bg-amber-50 text-amber-700"
          : "border-red-200 bg-red-50 text-red-700";
  return <Badge className={cn("min-w-14 justify-center", color)}>{value === null ? "N/A" : `${value}`}</Badge>;
}
