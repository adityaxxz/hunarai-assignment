"use client";

import { Badge } from "@/components/ui/badge";
import type { CallEvent } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

/**
 * What arrived, when, and whether it was processed.
 *
 * This is the one place the webhook and reconciliation behaviour stops being
 * theoretical. Hunar sends a single status webhook at the terminal transition
 * and the result trails it by minutes, and that gap is visible here as two rows
 * with different timestamps rather than as a paragraph in a README.
 *
 * The raw bodies are deliberately not fetched: they carry the phone number and
 * the S3 recording URL, and this is a browser.
 */
export function Timeline({ events }: { events: CallEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No webhooks have been recorded for this call. Everything known about it came
        from reconciliation polling the API.
      </p>
    );
  }

  const first = new Date(events[0].received_at).getTime();

  return (
    <ol className="divide-y rounded-md border">
      {events.map((event) => {
        const offset = Math.round(
          (new Date(event.received_at).getTime() - first) / 1000,
        );
        return (
          <li key={event.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3">
            <span className="font-mono text-xs">{event.event_type}</span>
            <span className="text-xs text-muted-foreground">
              {formatDateTime(event.received_at)}
            </span>
            {/* Relative to the first event, because the interesting fact is the
                gap between them, not the wall clock. */}
            {offset > 0 && (
              <span className="text-xs tabular-nums text-muted-foreground">+{offset}s</span>
            )}
            {!event.linked && (
              <Badge
                variant="outline"
                title="Arrived before the call row existed and was adopted afterwards"
              >
                arrived early
              </Badge>
            )}
            {event.processing_error ? (
              <Badge variant="destructive">{event.processing_error}</Badge>
            ) : !event.processed_at ? (
              <Badge variant="outline">not processed</Badge>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
