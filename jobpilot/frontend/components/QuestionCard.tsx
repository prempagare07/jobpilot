"use client";

import * as React from "react";
import { Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api, type QAMemory } from "@/lib/api";

export function QuestionCard({ question, onSaved }: { question: QAMemory; onSaved: () => void }) {
  const [answer, setAnswer] = React.useState(question.answer_text || "");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.answerQuestion(question.question_hash, answer);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save answer");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm leading-snug">{question.question_text}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Textarea value={answer} onChange={(event) => setAnswer(event.target.value)} rows={3} />
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-red-600">{error}</p>
          <Button size="sm" onClick={save} disabled={saving}>
            <Save className="h-4 w-4" aria-hidden="true" />
            {saving ? "Saving" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
