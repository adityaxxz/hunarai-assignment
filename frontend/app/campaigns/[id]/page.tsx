"use client";

import { use, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { CallTable } from "@/components/campaigns/call-table";
import { Funnel } from "@/components/campaigns/funnel";
import { PageContainer } from "@/components/page-container";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { getCampaign, isActive, listCalls } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/vocab";

/** Four seconds. The backend rate-limits its own reconciliation to one call per
 * campaign per five seconds, so polling faster buys nothing but requests. */
const POLL_MS = 4_000;

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const id = Number(use(params).id);
  const [stage, setStage] = useState<string | null>(null);

  const calls = useQuery({
    queryKey: ["calls", id],
    queryFn: () => listCalls(id),
    // Polling IS the funnel. Hunar sends one status webhook per call, at the
    // terminal transition, so nothing intermediate arrives by push; the backend
    // reconciles against the API when this endpoint is read. Stopping once
    // every call is settled keeps a finished campaign from polling forever.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return POLL_MS;
      return data.results.some(isActive) ? POLL_MS : false;
    },
  });

  // Fetched once, not polled. Nothing on it moves during a run: status and the
  // dial window are fixed at dispatch. Its `funnel` counts DO move, but they are
  // not used here — see below.
  const campaign = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => getCampaign(id),
  });

  if (campaign.isPending) {
    return (
      <PageContainer title="Campaign">
        <LoadingState label="Loading campaign" />
      </PageContainer>
    );
  }
  if (campaign.isError) {
    return (
      <PageContainer title="Campaign">
        <ErrorState error={campaign.error} onRetry={() => campaign.refetch()} />
      </PageContainer>
    );
  }

  const rows = calls.data?.results ?? [];
  const live = rows.some(isActive);
  const shown = stage ? rows.filter((call) => call.stage === stage) : rows;

  // Counted from the same rows the table renders, rather than read from the
  // campaign's own `funnel`. Two endpoints polled independently drift by one
  // interval, and the screen then shows "3 queued" above a table of three calls
  // that are ringing — which reads as a bug in the funnel, not a stale fetch.
  const counts = rows.reduce<Record<string, number>>((acc, call) => {
    acc[call.stage] = (acc[call.stage] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <PageContainer
      title={campaign.data.name}
      description={`${campaign.data.total_calls} calls · dispatched ${formatDateTime(
        campaign.data.dispatched_at,
      )}`}
    >
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Badge variant={campaign.data.status === "RUNNING" ? "secondary" : "destructive"}>
          {campaign.data.status}
        </Badge>
        {live ? (
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="size-2 animate-pulse rounded-full bg-foreground" aria-hidden />
            Live, refreshing every {POLL_MS / 1000}s
          </span>
        ) : (
          // Terminal is not the same as complete. Hunar's API is eventually
          // consistent after COMPLETED, so a call can be settled here while its
          // result and engagement are still being backfilled by reconciliation.
          // Polling on regardless would run forever on a call that will never
          // produce a result, so the honest option is to stop and offer a
          // refresh rather than spin indefinitely or pretend this is final.
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            Every call has reached a terminal status, so this has stopped
            refreshing. Results can still arrive afterwards.
            <button
              type="button"
              onClick={() => calls.refetch()}
              className="underline underline-offset-4 hover:text-foreground"
            >
              Check again
            </button>
          </span>
        )}
        {campaign.data.requisition_id && (
          <Link
            href={`/requisitions/${campaign.data.requisition_id}`}
            className="text-xs underline underline-offset-4"
          >
            Requisition
          </Link>
        )}
      </div>

      {campaign.data.dispatch_error && (
        <Card className="mb-6">
          <CardContent className="pt-6 text-sm text-destructive">
            {campaign.data.dispatch_error}
          </CardContent>
        </Card>
      )}

      {!campaign.data.dialling_now && (
        <Card className="mb-6">
          <CardContent className="pt-6 text-sm">{campaign.data.dial_window_note}</CardContent>
        </Card>
      )}

      <Funnel counts={counts} selected={stage} onSelect={setStage} />

      <div className="mt-8">
        {calls.isPending && <LoadingState label="Loading calls" />}
        {calls.isError && <ErrorState error={calls.error} onRetry={() => calls.refetch()} />}
        {calls.data && rows.length === 0 && (
          <EmptyState
            title="No calls in this campaign"
            description="Nothing was dispatched, which usually means every candidate was excluded at preflight."
          />
        )}
        {shown.length === 0 && rows.length > 0 && stage && (
          <EmptyState
            title="No calls at this stage"
            description={`Nothing in this campaign is at "${
              STAGE_LABELS[stage] ?? stage
            }". Select the stage again to show every call.`}
          />
        )}
        {shown.length > 0 && <CallTable calls={shown} />}
      </div>
    </PageContainer>
  );
}
