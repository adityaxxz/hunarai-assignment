"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { AlertCircle, CheckCircle2 } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getPreflight } from "@/lib/api/client";

/**
 * What would happen if a campaign launched right now, stated pessimistically.
 *
 * Shown before money is spent, so it reports what will fail rather than what
 * should work: a candidate Hunar would reject counts as excluded, not dialable.
 */
export function PreflightPanel({ requisitionId }: { requisitionId: number }) {
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["preflight", requisitionId],
    queryFn: () => getPreflight(requisitionId),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Preflight</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isPending && <LoadingState label="Checking" />}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}
        {data && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Stat label="Candidates" value={data.candidates} />
              <Stat label="Dialable" value={data.dialable} />
              <Stat
                label="Agent"
                value={data.hunar_agent_id ? "ready" : "none"}
                mono={Boolean(data.hunar_agent_id)}
              />
            </div>

            {data.ready ? (
              <p className="flex items-center gap-2 text-sm">
                <CheckCircle2 className="size-4" aria-hidden />
                Ready to launch.
              </p>
            ) : (
              <ul className="space-y-1 text-sm">
                {data.blockers.map((blocker) => (
                  <li key={blocker} className="flex items-start gap-2">
                    <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
                    {blocker}
                  </li>
                ))}
              </ul>
            )}

            {data.excluded.length > 0 && (
              <div>
                <p className="mb-2 text-sm font-medium">
                  {data.excluded.length} candidate
                  {data.excluded.length === 1 ? "" : "s"} would not be called
                </p>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {data.excluded.map((row) => (
                    <li key={row.candidate_id}>
                      <span className="text-foreground">{row.name}</span>{" "}
                      — {row.reasons.join("; ")}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <Button asChild disabled={!data.ready} size="sm">
              <Link href={`/requisitions/${requisitionId}/launch`}>Set up a campaign</Link>
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, mono }: { label: string; value: number | string; mono?: boolean }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={mono ? "mt-1 font-mono text-xs" : "mt-1 text-lg tabular-nums"}>{value}</p>
    </div>
  );
}
