"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { ExternalLink, Mail, Search, Send, SkipForward } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { LoadingTable } from "@/components/LoadingTable";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, type Job, type OutreachContact, type OutreachPreview, type OutreachResult } from "@/lib/api";
import { formatDate, truncate } from "@/lib/format";

export default function OutreachPage() {
  const { data: jobs } = useSWR<Job[]>("outreach-jobs", () => api.listJobs({ limit: 500 }));
  const { data: contacts, mutate, isLoading } = useSWR<OutreachContact[]>("outreach-contacts", api.outreachContacts);
  const [company, setCompany] = React.useState("");
  const [jobTitle, setJobTitle] = React.useState("");
  const [selectedJobId, setSelectedJobId] = React.useState("");
  const [previews, setPreviews] = React.useState<OutreachPreview[]>([]);
  const [message, setMessage] = React.useState<string | null>(null);
  const [skipped, setSkipped] = React.useState<Set<number>>(new Set());

  const matchingJobs = React.useMemo(() => {
    const companyNeedle = company.trim().toLowerCase();
    const titleNeedle = jobTitle.trim().toLowerCase();
    return (jobs ?? []).filter((job) => {
      const companyMatch = companyNeedle ? job.company.toLowerCase().includes(companyNeedle) : true;
      const titleMatch = titleNeedle ? job.title.toLowerCase().includes(titleNeedle) : true;
      return companyMatch && titleMatch;
    });
  }, [company, jobTitle, jobs]);

  const queuedContacts = (contacts ?? []).filter((contact) => {
    return !contact.email_sent && Boolean(contact.email) && !skipped.has(contact.id);
  });
  const sentContacts = (contacts ?? []).filter((contact) => contact.email_sent);
  const contactById = React.useMemo(() => {
    return new Map((contacts ?? []).map((contact) => [contact.id, contact]));
  }, [contacts]);

  async function findRecruiters() {
    const jobId = selectedJobId || matchingJobs[0]?.id;
    if (!jobId) {
      setMessage("Select a saved job first. Apollo search is routed through /api/outreach/find/{job_id}.");
      return;
    }
    setMessage(null);
    try {
      const result: OutreachResult = await api.findRecruiters(jobId);
      setPreviews(result.previews);
      setMessage(`Found ${result.contacts_found} contacts and prepared ${result.previews.length} email previews.`);
      await mutate();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not find recruiters");
    }
  }

  async function send(contact: OutreachContact) {
    setMessage(null);
    try {
      const result = await api.sendContact(contact.id);
      setMessage(result.success ? `Email sent to ${contact.name}.` : result.error || "Email failed.");
      await mutate();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not send email");
    }
  }

  async function markReplied(contact: OutreachContact) {
    setMessage(null);
    try {
      await api.markReplied(contact.id);
      await mutate();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not mark reply");
    }
  }

  return (
    <>
      <PageHeader
        title="Outreach"
        description="Find hiring managers, preview cold emails, and track replies."
      />

      {message && (
        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {message}
        </div>
      )}

      <section className="grid gap-5">
        <Card>
          <CardHeader>
            <CardTitle>Find Recruiters</CardTitle>
            <CardDescription>Filter saved jobs, then run Apollo for the selected company and role.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[1fr_1fr_1.4fr_auto]">
            <Input placeholder="Company name" value={company} onChange={(event) => setCompany(event.target.value)} />
            <Input placeholder="Job title" value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} />
            <Select value={selectedJobId} onChange={(event) => setSelectedJobId(event.target.value)}>
              <option value="">Use first matching saved job</option>
              {matchingJobs.slice(0, 30).map((job) => (
                <option key={job.id} value={job.id}>
                  {job.company} - {job.title}
                </option>
              ))}
            </Select>
            <Button onClick={() => void findRecruiters()}>
              <Search className="h-4 w-4" aria-hidden="true" />
              Find
            </Button>
          </CardContent>
        </Card>

        {previews.length > 0 && (
          <section>
            <div className="mb-3">
              <h2 className="text-base font-semibold text-slate-950">Apollo Results</h2>
              <p className="text-sm text-muted-foreground">Contacts are cached immediately to avoid repeat credits.</p>
            </div>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {previews.map((preview) => (
                <RecruiterPreviewCard
                  key={preview.contact_id}
                  preview={preview}
                  contact={contactById.get(preview.contact_id)}
                />
              ))}
            </div>
          </section>
        )}

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div>
            <div className="mb-3">
              <h2 className="text-base font-semibold text-slate-950">Email Queue</h2>
              <p className="text-sm text-muted-foreground">Preview each draft before sending.</p>
            </div>
            {isLoading ? (
              <LoadingTable rows={4} />
            ) : queuedContacts.length ? (
              <div className="grid gap-4">
                {queuedContacts.map((contact) => (
                  <Card key={contact.id}>
                    <CardHeader>
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <CardTitle className="text-sm">{contact.name}</CardTitle>
                          <CardDescription>
                            {contact.title ?? "Contact"} at {contact.company}
                          </CardDescription>
                        </div>
                        <Badge variant="secondary">{contact.email_status ?? "email"}</Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="grid gap-3">
                      <Input value={contact.email_subject ?? "Draft email"} readOnly />
                      <Textarea rows={6} value={contact.email_body ?? "No draft generated yet."} readOnly />
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" onClick={() => void send(contact)}>
                          <Send className="h-4 w-4" aria-hidden="true" />
                          Send
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            setSkipped((current) => {
                              const next = new Set(current);
                              next.add(contact.id);
                              return next;
                            })
                          }
                        >
                          <SkipForward className="h-4 w-4" aria-hidden="true" />
                          Skip
                        </Button>
                        {contact.linkedin_url && (
                          <Button asChild size="sm" variant="outline">
                            <Link href={contact.linkedin_url} target="_blank">
                              <ExternalLink className="h-4 w-4" aria-hidden="true" />
                              LinkedIn
                            </Link>
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : (
              <EmptyState
                title="No queued contacts"
                description="Run recruiter search for a saved job to generate email previews."
              />
            )}
          </div>

          <div>
            <div className="mb-3">
              <h2 className="text-base font-semibold text-slate-950">Sent</h2>
              <p className="text-sm text-muted-foreground">History and reply tracking.</p>
            </div>
            {sentContacts.length ? (
              <div className="overflow-hidden rounded-md border bg-card">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Sent</TableHead>
                      <TableHead>Contact</TableHead>
                      <TableHead>Subject</TableHead>
                      <TableHead>Open</TableHead>
                      <TableHead>Reply</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sentContacts.map((contact) => (
                      <TableRow key={contact.id}>
                        <TableCell className="text-muted-foreground">{formatDate(contact.email_sent_at)}</TableCell>
                        <TableCell>
                          <p className="font-medium">{contact.name}</p>
                          <p className="text-xs text-muted-foreground">{contact.company}</p>
                        </TableCell>
                        <TableCell>{truncate(contact.email_subject ?? "No subject", 48)}</TableCell>
                        <TableCell>
                          <Badge variant="secondary">untracked</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={contact.reply_received ? "default" : "secondary"}>
                            {contact.reply_received ? "replied" : "pending"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void markReplied(contact)}
                            disabled={contact.reply_received}
                          >
                            <Mail className="h-4 w-4" aria-hidden="true" />
                            Mark Replied
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <EmptyState title="No sent email yet" description="Sent outreach appears here." />
            )}
          </div>
        </section>
      </section>
    </>
  );
}

function RecruiterPreviewCard({
  preview,
  contact,
}: {
  preview: OutreachPreview;
  contact?: OutreachContact;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm">{contact?.name ?? preview.contact_name}</CardTitle>
            <CardDescription>
              {contact?.title ?? "Hiring contact"} at {contact?.company ?? "company"}
            </CardDescription>
          </div>
          <Badge variant="secondary">{contact?.email_status ?? "email"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <p className="text-sm font-medium">{preview.subject}</p>
        <p className="max-h-24 overflow-hidden text-sm text-muted-foreground">{preview.body}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled>
            Add to queue
          </Button>
          {contact?.linkedin_url && (
            <Button asChild size="sm" variant="outline">
              <Link href={contact.linkedin_url} target="_blank">
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                LinkedIn
              </Link>
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
