"use client";

import { useQuery } from "@tanstack/react-query";

import { PageContainer } from "@/components/page-container";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getHealth } from "@/lib/api/client";

/**
 * The wire test. The only route in this task that talks to the backend: if this
 * renders against a running API, the whole client path is proven — base URL,
 * error taxonomy, query provider, loading and error states.
 */
export default function OverviewPage() {
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  return (
    <PageContainer
      title="Overview"
      description="Backend connectivity and build information."
    >
      {isPending && <LoadingState label="Contacting the backend" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Backend</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Status</dt>
                <dd className="mt-1">
                  <Badge variant={data.status === "ok" ? "secondary" : "destructive"}>
                    {data.status}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Voice provider</dt>
                <dd className="mt-1">
                  <Badge variant="outline">
                    {data.demo_mode ? "simulated" : "live Hunar API"}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Version</dt>
                <dd className="mt-1 font-mono text-xs">{data.version}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      )}
    </PageContainer>
  );
}
