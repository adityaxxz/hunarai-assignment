"use client";

import Link from "next/link";
import { RotateCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Call } from "@/lib/api/types";
import { relativeFuture } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/vocab";

const TERMINAL_BAD = new Set(["failed", "dispatch_failed"]);

export function CallTable({
  calls,
  campaignId,
}: {
  calls: Call[];
  campaignId: number;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Candidate</TableHead>
          <TableHead>Stage</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Lifecycle</TableHead>
          <TableHead>Engagement</TableHead>
          <TableHead>Attempts</TableHead>
          <TableHead>Result</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {calls.map((call) => (
          <TableRow key={call.id}>
            <TableCell>
              <Link
                href={`/campaigns/${campaignId}/calls/${call.id}`}
                className="font-medium underline-offset-4 hover:underline"
              >
                {call.candidate_name}
              </Link>
            </TableCell>
            <TableCell>
              <Badge variant={TERMINAL_BAD.has(call.stage) ? "destructive" : "outline"}>
                {STAGE_LABELS[call.stage] ?? call.stage}
              </Badge>
              {call.dispatch_error && (
                <p className="mt-1 max-w-56 text-xs text-destructive">{call.dispatch_error}</p>
              )}
            </TableCell>
            <TableCell className="font-mono text-xs">{call.status ?? "—"}</TableCell>
            <TableCell className="font-mono text-xs">{call.lifecycle_status ?? "—"}</TableCell>
            <TableCell className="font-mono text-xs">
              {call.engagement_status ?? "—"}
            </TableCell>
            <TableCell>
              <Retry call={call} />
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {call.has_result ? "captured" : "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/**
 * Retry state, said out loud.
 *
 * A call that did not connect and is waiting on its second attempt looks
 * identical to a dead one unless the pending retry is on screen. Reading it as
 * failed is how a recruiter starts phoning someone the system is about to call.
 */
function Retry({ call }: { call: Call }) {
  const due = relativeFuture(call.next_retry_scheduled_at);

  return (
    <div className="text-xs">
      <span className="tabular-nums">
        {call.retry_count + 1}
        {call.retries_left !== null && call.retries_left > 0
          ? ` of ${call.retry_count + 1 + call.retries_left}`
          : ""}
      </span>
      {due && (
        <span className="ml-2 inline-flex items-center gap-1 text-muted-foreground">
          <RotateCw className="size-3" aria-hidden />
          retrying {due}
        </span>
      )}
    </div>
  );
}
