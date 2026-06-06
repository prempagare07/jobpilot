"use client";

import * as React from "react";
import useSWR from "swr";
import { Edit3, Save, Search, Trash2 } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { LoadingTable } from "@/components/LoadingTable";
import { PageHeader } from "@/components/PageHeader";
import { PaginationControls } from "@/components/PaginationControls";
import { QuestionCard } from "@/components/QuestionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, type QAMemory } from "@/lib/api";

const PAGE_SIZE = 50;

export default function QAPage() {
  const { data: pending, mutate: mutatePending } = useSWR<QAMemory[]>("qa-pending", api.qaPending);
  const { data: memory, mutate: mutateMemory, isLoading } = useSWR<QAMemory[]>("qa-memory", api.qaMemory);
  const [search, setSearch] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [editingId, setEditingId] = React.useState<number | null>(null);
  const [editAnswer, setEditAnswer] = React.useState("");
  const [message, setMessage] = React.useState<string | null>(null);

  const filtered = React.useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return memory ?? [];
    return (memory ?? []).filter(
      (item) =>
        item.question_text.toLowerCase().includes(needle) ||
        item.answer_text.toLowerCase().includes(needle) ||
        item.source.toLowerCase().includes(needle),
    );
  }, [memory, search]);
  const page = filtered.slice(offset, offset + PAGE_SIZE);

  async function refresh() {
    await Promise.all([mutatePending(), mutateMemory()]);
  }

  function startEdit(item: QAMemory) {
    setEditingId(item.id);
    setEditAnswer(item.answer_text);
  }

  async function saveEdit(item: QAMemory) {
    setMessage(null);
    try {
      await api.updateMemory(item.id, { answer_text: editAnswer, confidence: Math.max(item.confidence, 0.8) });
      setEditingId(null);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update memory");
    }
  }

  async function deleteItem(item: QAMemory) {
    setMessage(null);
    try {
      await api.deleteMemory(item.id);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not delete memory");
    }
  }

  return (
    <>
      <PageHeader
        title="Q&A Memory"
        description="Teach the auto-filler reusable answers for application questions."
      />

      {message && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {message}
        </div>
      )}

      <section className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="rounded-md border bg-slate-50">
          <div className="border-b px-5 py-4">
            <h2 className="text-base font-semibold text-slate-950">Pending Questions</h2>
            <p className="mt-1 text-sm text-muted-foreground">Low-confidence answers that need you.</p>
          </div>
          <div className="grid gap-3 p-4">
            {pending?.length ? (
              pending.map((question) => (
                <QuestionCard key={question.id} question={question} onSaved={() => void refresh()} />
              ))
            ) : (
              <EmptyState
                title="No pending questions"
                description="The auto-filler will park uncertain questions here."
              />
            )}
          </div>
        </section>

        <section>
          <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-base font-semibold text-slate-950">Memory Bank</h2>
              <p className="text-sm text-muted-foreground">Search, edit, or remove stored answers.</p>
            </div>
            <label className="relative w-full sm:max-w-sm">
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                className="pl-9"
                placeholder="Search memory"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setOffset(0);
                }}
              />
            </label>
          </div>

          {isLoading ? (
            <LoadingTable rows={8} />
          ) : page.length ? (
            <div className="overflow-hidden rounded-md border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Question</TableHead>
                    <TableHead>Answer</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Used</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {page.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell className="max-w-64 align-top font-medium">{item.question_text}</TableCell>
                      <TableCell className="max-w-96 align-top">
                        {editingId === item.id ? (
                          <Textarea rows={4} value={editAnswer} onChange={(event) => setEditAnswer(event.target.value)} />
                        ) : (
                          <span className="text-sm text-muted-foreground">{item.answer_text}</span>
                        )}
                      </TableCell>
                      <TableCell className="min-w-32 align-top">
                        <Progress value={item.confidence * 100} />
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {Math.round(item.confidence * 100)}%
                        </span>
                      </TableCell>
                      <TableCell className="align-top">{item.times_used}</TableCell>
                      <TableCell className="align-top">
                        <Badge variant={item.source === "user_provided" ? "default" : "secondary"}>
                          {item.source}
                        </Badge>
                      </TableCell>
                      <TableCell className="align-top">
                        <div className="flex justify-end gap-2">
                          {editingId === item.id ? (
                            <Button size="icon" variant="outline" onClick={() => void saveEdit(item)} aria-label="Save">
                              <Save className="h-4 w-4" aria-hidden="true" />
                            </Button>
                          ) : (
                            <Button size="icon" variant="outline" onClick={() => startEdit(item)} aria-label="Edit">
                              <Edit3 className="h-4 w-4" aria-hidden="true" />
                            </Button>
                          )}
                          <Button
                            size="icon"
                            variant="outline"
                            onClick={() => void deleteItem(item)}
                            aria-label="Delete"
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
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
                count={page.length}
                onPrevious={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                onNext={() => setOffset(offset + PAGE_SIZE)}
              />
            </div>
          ) : (
            <EmptyState title="No memory entries" description="Profile setup will seed common questions." />
          )}
        </section>
      </section>
    </>
  );
}
