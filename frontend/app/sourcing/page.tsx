"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { AlertCircle } from "lucide-react";

import { PageContainer } from "@/components/page-container";
import { ResultsTable } from "@/components/sourcing/results-table";
import { ErrorState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  createAgent,
  createCampaign,
  createSourcingSearch,
  importSourced,
  previewAgent,
  runSourcingSearch,
} from "@/lib/api/client";
import type { SearchRun, SourcingSearch } from "@/lib/api/types";

const SAMPLE_JD = `We are hiring a Senior Instrumentation Engineer in Pune for a process automation project.

The role covers loop checking, commissioning support and vendor coordination on a live plant. Four or more years of instrumentation experience is expected, ideally in oil and gas or chemicals.`;

export default function SourcingPage() {
  const router = useRouter();
  const [jd, setJd] = useState("");
  const [search, setSearch] = useState<SourcingSearch | null>(null);
  const [queryText, setQueryText] = useState("");
  const [queryError, setQueryError] = useState<string | null>(null);
  const [run, setRun] = useState<SearchRun | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("Senior Instrumentation Engineer");
  const [location, setLocation] = useState("Pune");
  const [confirmed, setConfirmed] = useState(false);

  const generate = useMutation({
    mutationFn: () => createSourcingSearch(jd),
    onSuccess: (result) => {
      setSearch(result);
      setQueryText(JSON.stringify(result.query, null, 2));
      setRun(null);
      setSelected(new Set());
    },
  });

  const execute = useMutation({
    mutationFn: () => runSourcingSearch(search!.id, JSON.parse(queryText)),
    onSuccess: (result) => {
      setRun(result);
      // Pre-select everything the consent gate would accept. The recruiter
      // deselects rather than hunts, and the gate still requires an explicit tick.
      setSelected(
        new Set(
          result.profiles
            .filter((p) => p.phone_e164 && !p.do_not_call && !p.already_a_candidate)
            .map((p) => p.dedupe_key),
        ),
      );
    },
  });

  /** Import, build the agent, dispatch. Three existing endpoints, no new ones. */
  const dispatch = useMutation({
    mutationFn: async () => {
      const chosen = run!.profiles.filter((p) => selected.has(p.dedupe_key));
      const imported = await importSourced(search!.id, {
        title,
        location,
        profiles: chosen,
        confirm: true,
      });
      const payload = await previewAgent(imported.requisition_id);
      await createAgent(imported.requisition_id, payload);
      return createCampaign({
        requisition_id: imported.requisition_id,
        name: `${title} reachout`,
        kind: "SOURCING",
        timezone: "Asia/Kolkata",
        retry_config: { max_retry_count: 1, retry_interval_hours: 3 },
      });
    },
    onSuccess: (campaign) => router.push(`/sourcing/campaigns/${campaign.id}`),
  });

  const selectable = run?.profiles.filter(
    (p) => p.phone_e164 && !p.do_not_call && !p.already_a_candidate,
  ).length ?? 0;

  // Why nobody can be selected, in the order the consent gate excludes them.
  // Rendered instead of the gate rather than left blank: a table followed by
  // nothing reads as a broken page, and the reason is already known here.
  const exclusions = run
    ? [
        {
          count: run.profiles.filter((p) => p.already_a_candidate).length,
          reason: "already candidates from an earlier sourcing run",
        },
        {
          count: run.profiles.filter((p) => p.do_not_call).length,
          reason: "on the do-not-call list",
        },
        {
          count: run.profiles.filter((p) => !p.phone_e164).length,
          reason: "without a number this plan will release",
        },
      ].filter((e) => e.count > 0)
    : [];

  return (
    <PageContainer
      title="Sourcing"
      description="Find people who have not applied, resolve a number for them, and ask permission before dialling."
    >
      <div className="space-y-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">1. The job description</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              rows={8}
              value={jd}
              // Not SAMPLE_JD: an identical placeholder makes "Use the sample"
              // look broken, because filling the field changes nothing on screen.
              placeholder="Paste a job description. Prose is fine — the point of this step is that the input is unstructured."
              onChange={(e) => setJd(e.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => generate.mutate()}
                disabled={generate.isPending || jd.trim().length < 20}
              >
                {generate.isPending ? "Reading the description…" : "Generate a search query"}
              </Button>
              <Button variant="ghost" onClick={() => setJd(SAMPLE_JD)}>
                Use the sample
              </Button>
            </div>
            {generate.isError && <ErrorState error={generate.error} />}
          </CardContent>
        </Card>

        {search && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">2. The query</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={search.query_source === "gemini" ? "secondary" : "outline"}>
                  {search.query_source === "gemini" ? "Written by Gemini" : "Keyword fallback"}
                </Badge>
                <Badge variant="outline">Provider: {search.provider}</Badge>
              </div>
              <p className="text-xs text-muted-foreground">{search.note}</p>
              <Textarea
                rows={12}
                className="font-mono text-xs"
                value={queryText}
                onChange={(e) => {
                  setQueryText(e.target.value);
                  try {
                    JSON.parse(e.target.value);
                    setQueryError(null);
                  } catch {
                    setQueryError("Not valid JSON yet.");
                  }
                }}
              />
              {queryError && <p className="text-xs text-destructive">{queryError}</p>}
              <p className="text-xs text-muted-foreground">
                This is what runs. The model proposes it and you decide, which matters
                when the alternative is spending metered search credits on a guess.
              </p>
              <Button
                onClick={() => execute.mutate()}
                disabled={execute.isPending || queryError !== null}
              >
                {execute.isPending ? "Searching…" : "Run the search"}
              </Button>
              {execute.isError && <ErrorState error={execute.error} />}
            </CardContent>
          </Card>
        )}

        {run && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">
                3. {run.profiles.length} profiles, {run.from_provider} with a number
                from the provider
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {run.dialable > run.from_provider && (
                <p className="text-xs text-muted-foreground">
                  {run.dialable - run.from_provider} of these are dialable only because
                  a demo number is standing in. Each is badged below.
                </p>
              )}
              {run.notes.map((note) => (
                <p key={note} className="text-xs text-muted-foreground">
                  {note}
                </p>
              ))}
              <ResultsTable
                profiles={run.profiles}
                selected={selected}
                onToggle={(key) =>
                  setSelected((current) => {
                    const next = new Set(current);
                    if (next.has(key)) next.delete(key);
                    else next.add(key);
                    return next;
                  })
                }
              />
            </CardContent>
          </Card>
        )}

        {run && selectable === 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">4. Consent</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-sm">
                None of these {run.profiles.length} profiles can be called:{" "}
                {exclusions.map((e) => `${e.count} ${e.reason}`).join(", ")}.
              </p>
              <p className="text-sm text-muted-foreground">
                Edit the query above and search again to find people who are not
                already in the pipeline.
              </p>
            </CardContent>
          </Card>
        )}

        {run && selectable > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">4. Consent</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* The gate an inbound pipeline does not need. A CSV of applicants
                  is already evidence of interest; a scraped profile is not. */}
              <p className="text-sm">
                These {selected.size} people did not apply for this role and have not
                asked to be contacted. Dialling them is a decision, so the system will
                not do it until someone has looked at the list and said so.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-sm">Role they will be told about</Label>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-sm">Location</Label>
                  <Input value={location} onChange={(e) => setLocation(e.target.value)} />
                </div>
              </div>
              <label className="flex items-start gap-2 text-sm">
                <Checkbox
                  checked={confirmed}
                  onCheckedChange={(v) => setConfirmed(v === true)}
                />
                I have reviewed this list and I am authorising a call to each of them.
              </label>

              {dispatch.isError && <ErrorState error={dispatch.error} />}

              <div className="flex items-center gap-3">
                <Button
                  onClick={() => dispatch.mutate()}
                  disabled={!confirmed || selected.size === 0 || dispatch.isPending}
                >
                  {dispatch.isPending
                    ? "Building the agent and dispatching…"
                    : `Call ${selected.size} ${selected.size === 1 ? "person" : "people"}`}
                </Button>
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <AlertCircle className="size-3.5" aria-hidden />
                  Imports, builds a reachout agent and dispatches through the same
                  campaign endpoint Module A uses.
                </p>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </PageContainer>
  );
}
