"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { VariableDiff } from "@/components/requisitions/variable-diff";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { createAgent, listAgentVersions, previewAgent } from "@/lib/api/client";
import type { AgentPayload, AgentVersion, Requisition } from "@/lib/api/types";

/**
 * Tokens Hunar populates itself and never reports as custom_variables.
 *
 * Observed in the capture, and enforced by the backend, which refuses either as
 * a candidate variable. They appear in the prompt but never in the read-back, so
 * without this list the diff would flag both as errors on every single agent —
 * and a panel that cries wolf is a panel nobody reads.
 */
const RESERVED_TOKENS = ["callee_name", "mobile_number"] as const;

/** The {token}s Hunar will scan for. Computed the same way it does, so the diff
 * below compares like with like instead of comparing against our intent. */
function tokensIn(payload: AgentPayload): string[] {
  const text = [payload.agent_prompt, payload.introduction, payload.objective].join("\n");
  const found = [...text.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map((match) => match[1]);
  return [...new Set(found)].sort();
}

export function AgentPanel({ requisition }: { requisition: Requisition }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<AgentPayload | null>(null);
  const [schemaText, setSchemaText] = useState("");
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const versions = useQuery({
    queryKey: ["agent-versions", requisition.id],
    queryFn: () => listAgentVersions(requisition.id),
  });

  const preview = useMutation({
    mutationFn: () => previewAgent(requisition.id),
    onSuccess: (payload) => {
      setDraft(payload);
      setSchemaText(JSON.stringify(payload.result_schema, null, 2));
      setSchemaError(null);
    },
  });

  const create = useMutation({
    mutationFn: (payload: AgentPayload) => createAgent(requisition.id, payload),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["agent-versions", requisition.id] }),
  });

  // Generated on open rather than behind a button: the payload is the point of
  // the panel, and a recruiter should not have to ask for it.
  const runPreview = preview.mutate;
  useEffect(() => {
    runPreview();
  }, [runPreview, requisition.id]);

  const live = versions.data?.filter((version) => version.hunar_agent_id) ?? [];
  const latest = live.at(-1);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Agent</CardTitle>
        <CardDescription className="text-xs">
          The exact body that POST /agents/ receives. Edit it here and this is what
          Hunar gets.
        </CardDescription>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            onClick={() => preview.mutate()}
            disabled={preview.isPending}
          >
            Regenerate from criteria
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-6">
        {preview.isPending && !draft && <LoadingState label="Building the agent payload" />}
        {preview.isError && (
          <ErrorState error={preview.error} onRetry={() => preview.mutate()} />
        )}

        {draft && (
          <>
            <div className="grid gap-4">
              <Field label="Introduction" hint="The first thing the candidate hears.">
                <Textarea
                  rows={3}
                  value={draft.introduction}
                  onChange={(e) => setDraft({ ...draft, introduction: e.target.value })}
                />
              </Field>
              <Field
                label="Objective"
                hint="What the agent is trying to achieve on the call."
              >
                <Textarea
                  rows={3}
                  value={draft.objective}
                  onChange={(e) => setDraft({ ...draft, objective: e.target.value })}
                />
              </Field>
              <Field
                label="Agent prompt"
                hint="Carries one token per candidate variable. A token that is not here is a variable Hunar will never know about."
              >
                <Textarea
                  rows={16}
                  className="font-mono text-xs"
                  value={draft.agent_prompt}
                  onChange={(e) => setDraft({ ...draft, agent_prompt: e.target.value })}
                />
              </Field>
              <Field
                label="Result prompt"
                hint="How the structured answers are extracted after the call."
              >
                <Textarea
                  rows={5}
                  value={draft.result_prompt}
                  onChange={(e) => setDraft({ ...draft, result_prompt: e.target.value })}
                />
              </Field>
              <Field
                label="Result schema"
                hint="One key per criterion. These keys are what the rubric scores."
              >
                <Textarea
                  rows={12}
                  className="font-mono text-xs"
                  value={schemaText}
                  onChange={(e) => {
                    setSchemaText(e.target.value);
                    try {
                      setDraft({ ...draft, result_schema: JSON.parse(e.target.value) });
                      setSchemaError(null);
                    } catch {
                      // Held locally rather than thrown: the recruiter is
                      // mid-keystroke and every partial edit is invalid JSON.
                      setSchemaError("Not valid JSON yet, so the agent cannot be created.");
                    }
                  }}
                />
              </Field>
              {schemaError && <p className="text-xs text-destructive">{schemaError}</p>}
            </div>

            {create.isError && <ErrorState error={create.error} />}

            <div className="flex items-center gap-3">
              <Button
                onClick={() => create.mutate(draft)}
                disabled={create.isPending || schemaError !== null}
              >
                {create.isPending ? "Creating on Hunar…" : "Create agent"}
              </Button>
              <p className="text-xs text-muted-foreground">
                Creates a new version. Campaigns already launched keep the agent they
                were launched with.
              </p>
            </div>
          </>
        )}

        {latest && draft && (
          <>
            <Separator />
            <CreatedAgent
              version={latest}
              sentTokens={tokensIn(draft)}
              versionCount={live.length}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function CreatedAgent({
  version,
  sentTokens,
  versionCount,
}: {
  version: AgentVersion;
  sentTokens: string[];
  versionCount: number;
}) {
  const schemaKeys = Object.keys(version.result_schema ?? {}).sort();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Live on Hunar</span>
        <Badge variant="secondary" className="font-mono text-xs">
          {version.hunar_agent_id}
        </Badge>
        <span className="text-xs text-muted-foreground">
          version {version.version} of {versionCount}
        </span>
      </div>

      <p className="text-xs text-muted-foreground">
        Everything below was read back from Hunar after creation, not echoed from what we
        sent. Hunar derives these lists itself, so this read-back is the only reliable
        statement of the contract the calls will run under.
      </p>

      <VariableDiff
        title="Candidate variables"
        note="Tokens in the prompt we sent, against the custom_variables Hunar scanned out of it. Anything missing on the right is a value Hunar will not accept, and the call is rejected at dispatch."
        sent={sentTokens}
        derived={[...version.custom_variables].sort()}
        reserved={[...RESERVED_TOKENS]}
      />

      <VariableDiff
        title="Result variables"
        note="Keys in the result_schema we sent, against the result_variables Hunar says it will return. The rubric can only score what appears on the right."
        sent={schemaKeys}
        derived={[...version.result_variables].sort()}
      />

      {version.required_variables.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Hunar marks these as required:{" "}
          <span className="font-mono">{version.required_variables.join(", ")}</span>
        </p>
      )}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-sm">{label}</Label>
      {children}
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}
