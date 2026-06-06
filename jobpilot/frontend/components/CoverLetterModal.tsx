"use client";

import * as React from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function CoverLetterModal({
  open,
  value,
  onChange,
  onClose,
}: {
  open: boolean;
  value: string;
  onChange: (value: string) => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-slate-950/70 p-4">
      <div className="mx-auto flex h-full max-w-5xl flex-col rounded-md bg-card shadow-xl">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <h2 className="text-base font-semibold">Cover Letter Preview</h2>
          <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="min-h-0 flex-1 p-5">
          <Textarea className="h-full min-h-full resize-none" value={value} onChange={(event) => onChange(event.target.value)} />
        </div>
      </div>
    </div>
  );
}
