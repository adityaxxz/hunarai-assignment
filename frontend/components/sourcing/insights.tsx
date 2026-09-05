"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SourcingInsights } from "@/lib/api/types";

/**
 * What the reachout batch actually learned.
 *
 * The interest rate is over calls that produced a result, not over everyone
 * dialled — dividing by people who never picked up measures reachability, and
 * the two get confused constantly. The denominator is printed next to it so the
 * number cannot be read the wrong way.
 */
export function Insights({ data }: { data: SourcingInsights }) {
  return (
    <div className="grid gap-6 md:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Interest</CardTitle>
        </CardHeader>
        <CardContent>
          {data.interest_rate === null ? (
            <p className="text-sm text-muted-foreground">
              Nobody has answered yet, which is not the same as nobody being
              interested. No rate is shown until there is something to divide.
            </p>
          ) : (
            <>
              <p className="text-3xl tabular-nums">{data.interest_rate}%</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {data.interested} of {data.answered} answered calls said they are open
                to a move. {data.total_calls - data.answered} of {data.total_calls}{" "}
                produced no result.
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Notice period</CardTitle>
        </CardHeader>
        <CardContent>
          <Distribution
            rows={data.notice_period}
            empty="No interested candidate has given a notice period yet."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Why not</CardTitle>
        </CardHeader>
        <CardContent>
          <Distribution
            rows={data.objections}
            empty="Nobody has declined yet, so there is nothing to summarise."
          />
          {data.callback_times.length > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              Callback times asked for: {data.callback_times.slice(0, 5).join("; ")}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Distribution({
  rows,
  empty,
}: {
  rows: Record<string, number>;
  empty: string;
}) {
  const entries = Object.entries(rows);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  const max = Math.max(...entries.map(([, count]) => count));

  return (
    <ul className="space-y-2">
      {entries.map(([label, count]) => (
        <li key={label}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="truncate" title={label}>
              {label}
            </span>
            <span className="tabular-nums text-muted-foreground">{count}</span>
          </div>
          {/* A bar rather than a chart library: one dependency avoided, and the
              only question here is which row is biggest. */}
          <div className="mt-1 h-1.5 rounded-full bg-muted">
            <div
              className="h-1.5 rounded-full bg-foreground"
              style={{ width: `${(count / max) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
