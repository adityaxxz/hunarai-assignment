"use client";

import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { DecisionPanel } from "@/components/calls/decision-panel";
import { Rubric } from "@/components/calls/rubric";
import { Timeline } from "@/components/calls/timeline";
import { PageContainer } from "@/components/page-container";
import { Phone } from "@/components/phone";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getCall, recordingUrl } from "@/lib/api/client";
import type { CallDetail } from "@/lib/api/types";
import { formatDateTime, relativeFuture } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/vocab";

export default function CallDetailPage({
  params,
}: {
  params: Promise<{ id: string; callId: string }>;
}) {
  const { id, callId } = use(params);
  const campaignId = Number(id);
  const numericCallId = Number(callId);

  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["call", numericCallId],
    queryFn: () => getCall(numericCallId),
  });

  if (isPending) {
    return (
      <PageContainer title="Candidate">
        <LoadingState label="Loading the call" />
      </PageContainer>
    );
  }
  if (isError) {
    return (
      <PageContainer title="Candidate">
        <ErrorState error={error} onRetry={() => refetch()} />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title={data.candidate_name}
      description={data.requisition_title ?? undefined}
    >
      <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
        <Badge variant="outline">{STAGE_LABELS[data.stage] ?? data.stage}</Badge>
        <Phone value={data.candidate_phone} />
        <Link
          href={`/campaigns/${campaignId}`}
          className="text-xs underline underline-offset-4"
        >
          Back to the funnel
        </Link>
      </div>

      <div className="space-y-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Decision</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <DecisionPanel call={data} />
            {data.evaluation && <Rubric reasons={data.evaluation.reasons} />}
            <Settling call={data} onRefresh={() => refetch()} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Recording</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.recording_available ? (
              <>
                {/* Points at our backend, never at Hunar. The S3 URL is a raw
                    bucket link to a recording of a real person's phone call. */}
                <audio
                  controls
                  preload="none"
                  className="w-full"
                  src={recordingUrl(data.id)}
                >
                  Your browser cannot play audio.
                </audio>
                {data.recording_simulated && (
                  <p className="text-xs text-muted-foreground">
                    Demo mode: this is two seconds of generated silence, not a real
                    call. The player is real so the screen is not missing a control
                    that exists in production.
                  </p>
                )}
                {!data.recording_simulated && (
                  <p className="text-xs text-muted-foreground">
                    Streamed through the backend. The storage URL is never sent to the
                    browser.
                  </p>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No recording. It may still be uploading, or the call may never have
                connected.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">What the candidate said</CardTitle>
          </CardHeader>
          <CardContent>
            {data.result ? (
              <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs">
                {JSON.stringify(data.result, null, 2)}
              </pre>
            ) : (
              <p className="text-sm text-muted-foreground">
                No structured result. Hunar produces it in a second pass after the call
                ends, so this stays empty on a call that did not connect or engage.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Call</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
              {/* Both status fields, always. `status` is this attempt and
                  `lifecycle_status` is the call across retries; collapsing them
                  hides a call that failed once and is still running. */}
              <Fact label="Status (this attempt)" value={data.status} mono />
              <Fact label="Lifecycle (across retries)" value={data.lifecycle_status} mono />
              <Fact label="Engagement" value={data.engagement_status} mono />
              <Fact label="Answered by" value={data.answered_by} mono />
              <Fact label="Ended by" value={data.call_ended_by} mono />
              <Fact label="Redial" value={data.redial_status} mono />
              <Fact label="Attempts" value={attempts(data)} />
              <Fact
                label="Next retry"
                value={
                  data.next_retry_scheduled_at
                    ? `${formatDateTime(data.next_retry_scheduled_at)} (${relativeFuture(
                        data.next_retry_scheduled_at,
                      )})`
                    : null
                }
              />
              <Fact
                label="Duration"
                value={data.duration_seconds ? `${data.duration_seconds}s` : null}
              />
              <Fact
                label="Candidate spoke for"
                value={
                  data.user_speech_duration ? `${data.user_speech_duration}s` : null
                }
              />
              <Fact label="Started" value={formatDateTime(data.started_at)} />
              <Fact label="Ended" value={formatDateTime(data.ended_at)} />
              <Fact label="Hunar call id" value={data.hunar_call_id} mono />
              <Fact label="Last reconciled" value={formatDateTime(data.last_reconciled_at)} />
            </dl>
            {data.dispatch_error && (
              <p className="mt-4 text-sm text-destructive">{data.dispatch_error}</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Timeline</CardTitle>
          </CardHeader>
          <CardContent>
            <Timeline events={data.timeline} />
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}

/**
 * Why this call has no result, when it has none.
 *
 * The backend records the reason it stopped chasing one. Showing that reason
 * beats a generic "check again", because "not engaged, no result expected" is a
 * final answer and "still settling" is not.
 */
function Settling({ call, onRefresh }: { call: CallDetail; onRefresh: () => void }) {
  if (call.result) return null;

  if (call.reconcile_stopped_reason) {
    return (
      <p className="text-sm text-muted-foreground">
        No result will arrive: {call.reconcile_stopped_reason}. The backend stopped
        checking at {formatDateTime(call.reconcile_stopped_at)}.
      </p>
    );
  }

  return (
    <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
      Still waiting on a result. Hunar produces one in a second pass that can trail the
      call by several minutes.
      <button
        type="button"
        onClick={onRefresh}
        className="underline underline-offset-4 hover:text-foreground"
      >
        Check again
      </button>
    </p>
  );
}

function attempts(call: CallDetail): string {
  const total =
    call.retries_left !== null ? call.retry_count + 1 + call.retries_left : null;
  return total ? `${call.retry_count + 1} of ${total}` : String(call.retry_count + 1);
}

function Fact({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={mono ? "mt-0.5 font-mono text-xs" : "mt-0.5"}>{value || "—"}</dd>
    </div>
  );
}
