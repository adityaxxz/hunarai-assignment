"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Plus } from "lucide-react";

import { PageContainer } from "@/components/page-container";
import { RequisitionForm } from "@/components/requisitions/requisition-form";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { listRequisitions } from "@/lib/api/client";

export default function RequisitionsPage() {
  const [creating, setCreating] = useState(false);
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["requisitions"],
    queryFn: listRequisitions,
  });

  return (
    <PageContainer
      title="Requisitions"
      description="A role to screen for. Its criteria become the agent's questions, the result schema and the rubric."
    >
      {!creating && (
        <div className="mb-4 flex justify-end">
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden /> New requisition
          </Button>
        </div>
      )}
      {creating && (
        <div className="mb-8">
          <RequisitionForm onDone={() => setCreating(false)} />
        </div>
      )}

      {isPending && <LoadingState label="Loading requisitions" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {data?.length === 0 && !creating && (
        <EmptyState
          title="No requisitions yet"
          description="Create one to define what the voice agent screens for, then import candidates against it."
        />
      )}
      {data && data.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Location</TableHead>
              <TableHead>Voice</TableHead>
              <TableHead className="text-right">Criteria</TableHead>
              <TableHead className="text-right">Openings</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((requisition) => (
              <TableRow key={requisition.id}>
                <TableCell>
                  <Link
                    href={`/requisitions/${requisition.id}`}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {requisition.title}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{requisition.location}</TableCell>
                <TableCell>
                  <Badge variant="outline">
                    {requisition.voice_persona} · {requisition.language}
                  </Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {requisition.criteria.length}
                </TableCell>
                <TableCell className="text-right tabular-nums">{requisition.openings}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </PageContainer>
  );
}
