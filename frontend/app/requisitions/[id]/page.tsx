"use client";

import { use } from "react";
import { useQuery } from "@tanstack/react-query";

import { PageContainer } from "@/components/page-container";
import { AgentPanel } from "@/components/requisitions/agent-panel";
import { CandidatesPanel } from "@/components/requisitions/candidates-panel";
import { PreflightPanel } from "@/components/requisitions/preflight-panel";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { getRequisition } from "@/lib/api/client";

export default function RequisitionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const id = Number(use(params).id);
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["requisition", id],
    queryFn: () => getRequisition(id),
  });

  if (isPending) {
    return (
      <PageContainer title="Requisition">
        <LoadingState label="Loading requisition" />
      </PageContainer>
    );
  }
  if (isError) {
    return (
      <PageContainer title="Requisition">
        <ErrorState error={error} onRetry={() => refetch()} />
      </PageContainer>
    );
  }

  const pay =
    data.pay_min || data.pay_max
      ? `₹${data.pay_min ?? "?"}–${data.pay_max ?? "?"}`
      : null;

  return (
    <PageContainer
      title={data.title}
      description={[data.location, data.shift, pay].filter(Boolean).join(" · ")}
    >
      <div className="mb-6 flex flex-wrap gap-2">
        <Badge variant="outline">{data.language}</Badge>
        <Badge variant="outline">Voice: {data.voice_persona}</Badge>
        <Badge variant="outline">
          {data.criteria.length} criteri{data.criteria.length === 1 ? "on" : "a"}
        </Badge>
        {data.candidate_variables.map((variable) => (
          <Badge key={variable} variant="secondary" className="font-mono text-xs">
            {variable}
          </Badge>
        ))}
      </div>

      <div className="space-y-8">
        <AgentPanel requisition={data} />
        <CandidatesPanel requisition={data} />
        <PreflightPanel requisitionId={id} />
      </div>
    </PageContainer>
  );
}
