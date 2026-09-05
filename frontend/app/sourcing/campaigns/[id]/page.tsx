"use client";

import { use, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { CallTable } from "@/components/campaigns/call-table";
import { Funnel } from "@/components/campaigns/funnel";
import { PageContainer } from "@/components/page-container";
import { Insights } from "@/components/sourcing/insights";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { getCampaign, getSourcingInsights, isActive, listCalls } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

const POLL_MS = 4_000;

/**
 * The reachout results view.
 *
 * Deliberately the same `Funnel` and `CallTable` components Module A renders,
 * over the same `calls` rows, polled the same way. The only thing this page adds
 * is the aggregate panel — because a sourcing batch is answering a different
 * question ("is this pool interested?") from a screening batch ("who qualified?"),
 * while being the same machinery underneath.
 */
export default function SourcingCampaignPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const id = Number(use(params).id);
  const [stage, setStage] = useState<string | null>(null);

  const calls = useQuery({
    queryKey: ["calls", id],
    queryFn: () => listCalls(id),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return POLL_MS;
      return data.results.some(isActive) ? POLL_MS : false;
    },
  });

  const campaign = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => getCampaign(id),
  });

  const insights = useQuery({
    queryKey: ["sourcing-insights", id],
    queryFn: () => getSourcingInsights(id),
    // Follows the call list: results arrive with the calls, so refreshing the
    // aggregates on a separate schedule would only make the two disagree.
    // A function, not a captured number — on the first render `calls.data` is
    // undefined, and a number computed then freezes the panel at "nobody has
    // answered yet" while the funnel beside it fills up.
    refetchInterval: () => {
      const rows = calls.data?.results;
      if (!rows) return POLL_MS;
      return rows.some(isActive) ? POLL_MS : false;
    },
  });

  if (campaign.isPending) {
    return (
      <PageContainer title="Reachout">
        <LoadingState label="Loading the campaign" />
      </PageContainer>
    );
  }
  if (campaign.isError) {
    return (
      <PageContainer title="Reachout">
        <ErrorState error={campaign.error} onRetry={() => campaign.refetch()} />
      </PageContainer>
    );
  }

  const rows = calls.data?.results ?? [];
  const live = rows.some(isActive);
  const shown = stage ? rows.filter((call) => call.stage === stage) : rows;
  const counts = rows.reduce<Record<string, number>>((acc, call) => {
    acc[call.stage] = (acc[call.stage] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <PageContainer
      title={campaign.data.name}
      description={`${campaign.data.total_calls} reachout calls · dispatched ${formatDateTime(
        campaign.data.dispatched_at,
      )}`}
    >
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Badge variant={campaign.data.status === "RUNNING" ? "secondary" : "destructive"}>
          {campaign.data.status}
        </Badge>
        <Badge variant="outline">Sourcing</Badge>
        {live ? (
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="size-2 animate-pulse rounded-full bg-foreground" aria-hidden />
            Live, refreshing every {POLL_MS / 1000}s
          </span>
        ) : (
          // Terminal is not finished: Hunar backfills results after COMPLETED,
          // so the aggregates can still move once polling has stopped.
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            Every call has reached a terminal status. Results can still arrive.
            <button
              type="button"
              onClick={() => {
                calls.refetch();
                insights.refetch();
              }}
              className="underline underline-offset-4 hover:text-foreground"
            >
              Check again
            </button>
          </span>
        )}
        <Link href="/sourcing" className="text-xs underline underline-offset-4">
          New search
        </Link>
      </div>

      {insights.data && (
        <div className="mb-8">
          <Insights data={insights.data} />
        </div>
      )}

      <Funnel counts={counts} selected={stage} onSelect={setStage} />

      <div className="mt-8">
        {calls.isPending && <LoadingState label="Loading calls" />}
        {calls.isError && <ErrorState error={calls.error} onRetry={() => calls.refetch()} />}
        {calls.data && rows.length === 0 && (
          <EmptyState
            title="No calls in this reachout"
            description="Nothing was dispatched, which usually means every selected profile was filtered by the consent gate."
          />
        )}
        {shown.length > 0 && <CallTable calls={shown} campaignId={id} />}
      </div>
    </PageContainer>
  );
}
