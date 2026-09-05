"use client";

import { use, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { AlertCircle } from "lucide-react";

import { ScheduleFields, type Schedule } from "@/components/campaigns/schedule-fields";
import { PageContainer } from "@/components/page-container";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createCampaign, getPreflight, getRequisition } from "@/lib/api/client";
import type { CampaignDetail } from "@/lib/api/types";
import { checkGuardrails, checkRetryConfig, estimateLocally } from "@/lib/campaign-rules";
import { formatDateTime } from "@/lib/format";

const TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "UTC"];

const DEFAULT_SCHEDULE: Schedule = {
  useGuardrails: true,
  days: ["MON", "TUE", "WED", "THU", "FRI"],
  earliest: "09:00",
  latest: "19:00",
  useRetries: true,
  maxRetries: 2,
  interval: 3,
};

export default function LaunchPage({ params }: { params: Promise<{ id: string }> }) {
  const requisitionId = Number(use(params).id);

  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("Asia/Kolkata");
  const [schedule, setSchedule] = useState<Schedule>(DEFAULT_SCHEDULE);

  const requisition = useQuery({
    queryKey: ["requisition", requisitionId],
    queryFn: () => getRequisition(requisitionId),
  });
  const preflight = useQuery({
    queryKey: ["preflight", requisitionId],
    queryFn: () => getPreflight(requisitionId),
  });

  const guardrails = schedule.useGuardrails
    ? {
        allowed_days: schedule.days,
        earliest_call_time: schedule.earliest,
        last_call_time: schedule.latest,
      }
    : null;
  // Zero and zero rather than null: Hunar treats an omitted retry_config as its
  // own default, and "no retries" has to be said explicitly.
  const retryConfig = schedule.useRetries
    ? { max_retry_count: schedule.maxRetries, retry_interval_hours: schedule.interval }
    : { max_retry_count: 0, retry_interval_hours: 0 };

  const problems = [...checkGuardrails(guardrails), ...checkRetryConfig(retryConfig)];
  const estimate = estimateLocally(preflight.data?.dialable ?? 0, retryConfig);

  const launch = useMutation<CampaignDetail>({
    mutationFn: () =>
      createCampaign({
        requisition_id: requisitionId,
        name: name || `${requisition.data?.title ?? "Campaign"} screening`,
        guardrails,
        retry_config: retryConfig,
        timezone,
      }),
  });

  if (requisition.isPending || preflight.isPending) {
    return (
      <PageContainer title="Launch a campaign">
        <LoadingState label="Loading" />
      </PageContainer>
    );
  }

  if (launch.data) return <Launched campaign={launch.data} />;

  return (
    <PageContainer title="Launch a campaign" description={requisition.data?.title}>
      <div className="space-y-6">
        {preflight.data && !preflight.data.ready && (
          <Card>
            <CardContent className="space-y-2 pt-6">
              {preflight.data.blockers.map((blocker) => (
                <Problem key={blocker} text={blocker} />
              ))}
              <Button asChild variant="outline" size="sm">
                <Link href={`/requisitions/${requisitionId}`}>Back to the requisition</Link>
              </Button>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Campaign</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-1.5">
              <Label className="text-sm">Name</Label>
              <Input
                value={name}
                placeholder={`${requisition.data?.title ?? "Campaign"} screening`}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">Timezone</Label>
              <Select value={timezone} onValueChange={setTimezone}>
                <SelectTrigger className="w-64">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIMEZONES.map((zone) => (
                    <SelectItem key={zone} value={zone}>
                      {zone}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <ScheduleFields schedule={schedule} onChange={setSchedule} />
          </CardContent>
        </Card>

        <Estimate
          estimate={estimate}
          excluded={preflight.data?.excluded.length ?? 0}
        />

        {problems.length > 0 && (
          <Card>
            <CardContent className="space-y-2 pt-6">
              {problems.map((problem) => (
                <Problem key={problem} text={problem} />
              ))}
            </CardContent>
          </Card>
        )}

        {launch.isError && <ErrorState error={launch.error} />}

        <Button
          size="lg"
          onClick={() => launch.mutate()}
          disabled={
            launch.isPending || problems.length > 0 || !(preflight.data?.ready ?? false)
          }
        >
          {launch.isPending
            ? "Dispatching…"
            : `Launch ${estimate.calls_to_place} call${
                estimate.calls_to_place === 1 ? "" : "s"
              }`}
        </Button>
      </div>
    </PageContainer>
  );
}

function Estimate({
  estimate,
  excluded,
}: {
  estimate: ReturnType<typeof estimateLocally>;
  excluded: number;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">What this will do</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Calls to place" value={estimate.calls_to_place} />
          <Stat label="Worst case attempts" value={estimate.worst_case_attempts} />
          <Stat
            label="Talk time if all connect"
            value={`${estimate.estimated_talk_minutes_if_all_connect} min`}
          />
        </div>
        <ul className="space-y-1 text-xs text-muted-foreground">
          <li>
            30 seconds per connected call, from the single call captured during
            development. The connect rate is unknown, so real talk time will be lower.
          </li>
          <li>
            No cost is shown. This account has no per-minute rate exposed on the API, and
            a number invented here would look authoritative.
          </li>
          {excluded > 0 && (
            <li>
              {excluded} candidate{excluded === 1 ? " is" : "s are"} excluded and will not
              be called.
            </li>
          )}
        </ul>
      </CardContent>
    </Card>
  );
}

/**
 * The success screen says when dialling starts, not just that it launched.
 *
 * A call placed outside the allowed window is accepted and SCHEDULED, not
 * rejected. A recruiter who sees "launched", then hears nothing and is not told
 * why, concludes the product is broken.
 */
function Launched({ campaign }: { campaign: CampaignDetail }) {
  return (
    <PageContainer title={campaign.name} description="Campaign dispatched.">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={campaign.status === "RUNNING" ? "secondary" : "destructive"}>
              {campaign.status}
            </Badge>
            <span className="text-sm">
              {campaign.total_calls} call{campaign.total_calls === 1 ? "" : "s"} created
            </span>
          </div>

          {campaign.dispatch_error && (
            <p className="text-sm text-destructive">{campaign.dispatch_error}</p>
          )}

          <p className="text-sm">{campaign.dial_window_note}</p>
          {campaign.dial_starts_at && !campaign.dialling_now && (
            <p className="text-sm text-muted-foreground">
              First call at {formatDateTime(campaign.dial_starts_at)}.
            </p>
          )}

          <Button asChild>
            <Link href={`/campaigns/${campaign.id}`}>Watch the funnel</Link>
          </Button>
        </CardContent>
      </Card>
    </PageContainer>
  );
}

function Problem({ text }: { text: string }) {
  return (
    <p className="flex items-start gap-2 text-sm">
      <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      {text}
    </p>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg tabular-nums">{value}</p>
    </div>
  );
}
