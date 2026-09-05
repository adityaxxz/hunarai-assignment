"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { PageContainer } from "@/components/page-container";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { listCampaigns } from "@/lib/api/client";
import type { CampaignSummary } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { FUNNEL_STAGES, STAGE_LABELS } from "@/lib/vocab";

/** Statuses that mean something went wrong, so the badge earns its colour. */
const BAD_STATUS = new Set(["FAILED", "PARTIALLY_DISPATCHED"]);

export default function CampaignsPage() {
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["campaigns"],
    queryFn: listCampaigns,
  });

  return (
    <PageContainer
      title="Campaigns"
      description="Every batch of calls, screening and sourcing, newest first."
    >
      {isPending && <LoadingState label="Loading campaigns" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {data?.total === 0 && (
        <EmptyState
          title="No campaigns yet"
          description="Launch one from a requisition, or run a sourcing search."
        />
      )}
      {data && data.total > 0 && (
        <div className="space-y-4">
          {data.results.map((campaign) => (
            <CampaignCard key={campaign.id} campaign={campaign} />
          ))}
        </div>
      )}
    </PageContainer>
  );
}

function CampaignCard({ campaign }: { campaign: CampaignSummary }) {
  // Sourcing campaigns open on their own page, which adds the interest and
  // objection aggregates above the same funnel.
  const href =
    campaign.kind === "SOURCING"
      ? `/sourcing/campaigns/${campaign.id}`
      : `/campaigns/${campaign.id}`;

  const stages = FUNNEL_STAGES.filter((stage) => (campaign.funnel[stage] ?? 0) > 0);

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <Link href={href} className="font-medium underline-offset-4 hover:underline">
            {campaign.name}
          </Link>
          <span className="text-xs text-muted-foreground">
            {formatDateTime(campaign.dispatched_at ?? campaign.created_at)}
          </span>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge variant={campaign.kind === "SOURCING" ? "outline" : "secondary"}>
            {campaign.kind === "SOURCING" ? "Sourcing" : "Screening"}
          </Badge>
          <Badge variant={BAD_STATUS.has(campaign.status) ? "destructive" : "outline"}>
            {campaign.status}
          </Badge>
          {campaign.requisition_id && (
            <Link
              href={`/requisitions/${campaign.requisition_id}`}
              className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              {campaign.requisition_title}
            </Link>
          )}
          <span className="text-xs text-muted-foreground">
            {campaign.total_calls} call{campaign.total_calls === 1 ? "" : "s"}
          </span>
        </div>

        {campaign.dispatch_error && (
          <p className="mt-2 text-sm text-destructive">{campaign.dispatch_error}</p>
        )}

        {/* Only the stages that have anyone in them. The full eight-column
            funnel belongs on the campaign itself; here it would be mostly
            zeroes and would bury the two numbers that differ between rows. */}
        {stages.length > 0 && (
          <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
            {stages.map((stage) => (
              <div key={stage} className="flex items-baseline gap-1.5">
                <dt className="text-xs text-muted-foreground">{STAGE_LABELS[stage]}</dt>
                <dd className="text-sm tabular-nums">{campaign.funnel[stage]}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}
