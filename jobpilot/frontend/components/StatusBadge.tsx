import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const statusColors: Record<string, string> = {
  new: "border-slate-200 bg-slate-50 text-slate-700",
  queued: "border-blue-200 bg-blue-50 text-blue-700",
  reviewed: "border-cyan-200 bg-cyan-50 text-cyan-700",
  applied: "border-purple-200 bg-purple-50 text-purple-700",
  interview: "border-green-200 bg-green-50 text-green-700",
  offer: "border-teal-200 bg-teal-50 text-teal-700",
  rejected: "border-red-200 bg-red-50 text-red-700",
  skip: "border-slate-200 bg-slate-100 text-slate-600",
  failed: "border-red-200 bg-red-50 text-red-700",
};

export function StatusBadge({ status }: { status?: string | null }) {
  const label = status || "unknown";
  return <Badge className={cn("capitalize", statusColors[label] ?? statusColors.new)}>{label}</Badge>;
}
