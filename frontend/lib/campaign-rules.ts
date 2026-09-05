/**
 * A mirror of `backend/app/services/campaign.py`, and only a mirror.
 *
 * The backend stays authoritative — it is what Hunar's money actually goes
 * through, and it re-checks everything. This copy exists so a recruiter sees the
 * 08:00 floor while typing 07:30 rather than after pressing launch, which on a
 * cold free-tier backend is a minute later.
 *
 * The duplication is a real cost, stated plainly: a rule changed in
 * `services/campaign.py` has to be changed here too, or this screen will quietly
 * permit something the backend rejects. It is worth it only because these
 * particular rules are stable vendor policy rather than our own logic.
 */

import { RETRY_INTERVALS, WEEKDAYS } from "@/lib/vocab";
import type { Guardrails, RetryConfig } from "@/lib/api/types";

/** Observed as a hard org policy floor: Hunar answers 400 "Minimum allowed
 * earliest_call_time is 08:00." and documents it nowhere. */
export const ORG_EARLIEST_CALL_TIME = "08:00";
const MIN_DISTINCT_DAYS = 3;
const MIN_WINDOW_HOURS = 3;

function minutes(value: string): number | null {
  if (!/^\d{2}:\d{2}$/.test(value)) return null;
  const [hours, mins] = value.split(":").map(Number);
  if (hours > 23 || mins > 59) return null;
  return hours * 60 + mins;
}

export function checkGuardrails(guardrails: Guardrails | null): string[] {
  if (!guardrails) return [];

  const problems: string[] = [];
  const missing = (["allowed_days", "earliest_call_time", "last_call_time"] as const).filter(
    (field) => {
      const value = guardrails[field];
      return Array.isArray(value) ? value.length === 0 : !value;
    },
  );
  if (missing.length > 0) {
    return [
      `guardrails are all-or-nothing: ${missing.join(", ")} must be set too, or leave ` +
        "guardrails off entirely to use the organisation default",
    ];
  }

  const unknown = guardrails.allowed_days.filter(
    (day) => !(WEEKDAYS as readonly string[]).includes(day),
  );
  if (unknown.length > 0) problems.push(`unknown day(s) ${unknown.join(", ")}; use MON to SUN`);
  if (new Set(guardrails.allowed_days).size < MIN_DISTINCT_DAYS) {
    problems.push(
      `allowed_days needs at least ${MIN_DISTINCT_DAYS} distinct days, got ` +
        `${new Set(guardrails.allowed_days).size}`,
    );
  }

  const earliest = minutes(guardrails.earliest_call_time);
  const latest = minutes(guardrails.last_call_time);
  if (earliest === null) problems.push("earliest_call_time must be HH:MM");
  if (latest === null) problems.push("last_call_time must be HH:MM");
  if (earliest === null || latest === null) return problems;

  if (earliest < minutes(ORG_EARLIEST_CALL_TIME)!) {
    problems.push(
      `earliest_call_time cannot be before ${ORG_EARLIEST_CALL_TIME}; the organisation ` +
        "does not permit calling earlier, and supplying a from_phone_number does not " +
        "lift that for this account",
    );
  }
  if (earliest >= latest) {
    problems.push("earliest_call_time must be before last_call_time");
  } else if (latest - earliest < MIN_WINDOW_HOURS * 60) {
    problems.push(`the calling window must be at least ${MIN_WINDOW_HOURS} hours wide`);
  }
  return problems;
}

export function checkRetryConfig(retry: RetryConfig | null): string[] {
  if (!retry) return [];
  const { max_retry_count: count, retry_interval_hours: interval } = retry;
  if (count === null || interval === null) {
    return [
      "retry_config is all-or-nothing: set both max_retry_count and " +
        "retry_interval_hours, or turn retries off entirely",
    ];
  }

  const problems: string[] = [];
  if (count < 0 || count > 10) problems.push(`max_retry_count must be between 0 and 10, got ${count}`);
  if (!(RETRY_INTERVALS as readonly number[]).includes(interval)) {
    problems.push(`retry_interval_hours must be one of ${RETRY_INTERVALS.join(", ")}`);
  }
  return problems;
}

/** Mirrors `services/campaign.estimate`, including its refusal to name a price.
 * No per-minute rate is exposed by the API, so a cost figure here would be an
 * invented number that looks authoritative. */
export const OBSERVED_CONNECTED_CALL_SECONDS = 30;

export function estimateLocally(dialable: number, retry: RetryConfig | null) {
  const retries = retry?.max_retry_count ?? 0;
  return {
    calls_to_place: dialable,
    worst_case_attempts: dialable * (1 + retries),
    estimated_talk_minutes_if_all_connect:
      Math.round((dialable * OBSERVED_CONNECTED_CALL_SECONDS) / 6) / 10,
    retries,
  };
}
