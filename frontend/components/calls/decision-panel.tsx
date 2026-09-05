"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ErrorState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { overrideDecision } from "@/lib/api/client";
import type { CallDetail, OverrideReasonCode } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

/** Mirrors `OVERRIDE_REASON_LABELS` in the backend's schemas. A closed list,
 * because the point of a reason code is that it can be counted later. */
const REASONS: { code: OverrideReasonCode; label: string }[] = [
  { code: "SPOKE_TO_CANDIDATE", label: "I spoke to the candidate myself" },
  { code: "AGENT_MISHEARD", label: "The agent misheard or mis-recorded an answer" },
  { code: "RESULT_INCOMPLETE", label: "The call ended before the answers were complete" },
  { code: "REQUIREMENTS_CHANGED", label: "The role requirements changed" },
  { code: "OTHER", label: "Other" },
];

const DECISIONS = ["QUALIFIED", "REJECTED", "UNDECIDED"] as const;

export function DecisionPanel({ call }: { call: CallDetail }) {
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState<(typeof DECISIONS)[number]>("QUALIFIED");
  const [reasonCode, setReasonCode] = useState<OverrideReasonCode>("SPOKE_TO_CANDIDATE");
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: () =>
      overrideDecision(call.id, { decision, reason_code: reasonCode, note: note || null }),
    onSuccess: (updated) => {
      setOpen(false);
      setNote("");
      // Written straight into the cache rather than invalidated. The POST
      // already returns the full updated call — that is why the endpoint
      // responds with CallDetail — so refetching would be a second round trip
      // for something we are holding. It also removes the window where the
      // panel shows the old decision while the refetch is in flight.
      queryClient.setQueryData(["call", call.id], updated);
      // The funnel list is a different shape and genuinely does need re-reading.
      queryClient.invalidateQueries({ queryKey: ["calls", call.campaign_id] });
    },
  });

  const noteRequired = reasonCode === "OTHER";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <DecisionBadge decision={call.effective_decision} />
        {call.evaluation && (
          // Labelled, because "REJECTED · score 100" reads as a contradiction
          // otherwise. It is not one: knock-outs are excluded from the score and
          // decide on their own, so a candidate can answer every weighted
          // question well and still be rejected by the one that matters.
          <span
            className="text-sm text-muted-foreground"
            title="Weighted percentage across the non-knock-out questions. Knock-outs decide the outcome on their own."
          >
            {call.evaluation.score} on the weighted questions
          </span>
        )}
        {!open && (
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            {call.override ? "Change the decision" : "Override this decision"}
          </Button>
        )}
      </div>

      {call.override && call.evaluation && (
        // Both, side by side, always. Replacing the computed decision would
        // destroy the only thing worth auditing: what the machine concluded
        // before a person disagreed with it.
        <div className="rounded-md border p-3 text-sm">
          <p>
            {call.override.decision === call.evaluation.decision ? (
              <>
                A recruiter confirmed{" "}
                <span className="font-medium">{call.override.decision}</span>, which is
                also what the screening call scored.
              </>
            ) : (
              <>
                A recruiter set this to{" "}
                <span className="font-medium">{call.override.decision}</span>. The
                screening call scored it{" "}
                <span className="font-medium">{call.evaluation.decision}</span>.
              </>
            )}
          </p>
          <p className="mt-1 text-muted-foreground">
            {call.override.reason_label}
            {call.override.note ? ` — ${call.override.note}` : ""}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatDateTime(call.override.at)}
          </p>
        </div>
      )}

      {open && (
        <form
          className="space-y-3 rounded-md border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="text-sm">Decision</Label>
              <Select
                value={decision}
                onValueChange={(v) => setDecision(v as (typeof DECISIONS)[number])}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DECISIONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">Reason</Label>
              <Select
                value={reasonCode}
                onValueChange={(v) => setReasonCode(v as OverrideReasonCode)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {REASONS.map((reason) => (
                    <SelectItem key={reason.code} value={reason.code}>
                      {reason.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">
              Note {noteRequired && <span className="text-destructive">*</span>}
            </Label>
            <Textarea
              rows={2}
              value={note}
              placeholder="What did you find out?"
              onChange={(e) => setNote(e.target.value)}
            />
            {noteRequired && (
              <p className="text-xs text-muted-foreground">
                Required for &quot;Other&quot;. An override with no recorded reason is the
                one thing an audit trail must not contain.
              </p>
            )}
          </div>

          {submit.isError && <ErrorState error={submit.error} />}

          <div className="flex gap-2">
            <Button
              type="submit"
              size="sm"
              disabled={submit.isPending || (noteRequired && !note.trim())}
            >
              {submit.isPending ? "Saving…" : "Save decision"}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

function DecisionBadge({ decision }: { decision: string | null }) {
  if (!decision) return <Badge variant="outline">No decision</Badge>;
  return (
    <Badge variant={decision === "REJECTED" ? "destructive" : "secondary"}>
      {decision}
    </Badge>
  );
}
