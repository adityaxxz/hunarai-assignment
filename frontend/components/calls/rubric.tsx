"use client";

import { Check, CircleHelp, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { CriterionOutcome } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The rubric, criterion by criterion.
 *
 * A bare REJECTED is not something a recruiter can act on. What they need is
 * which question failed, what the candidate actually said, and whether that
 * question was a knock-out — because a failed knock-out is final and a low score
 * is an argument.
 *
 * "Unknown" is rendered as its own third state, never folded into a fail. The
 * agent may simply never have reached the question, and a 30-second call that
 * ended early is a different fact from a candidate who said no.
 */
export function Rubric({ reasons }: { reasons: CriterionOutcome[] }) {
  if (reasons.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        This requisition has no screening criteria, so there is nothing to score.
      </p>
    );
  }

  return (
    <ul className="divide-y rounded-md border">
      {reasons.map((outcome) => (
        <li key={outcome.key} className="flex gap-3 p-3">
          <StatusIcon status={outcome.status} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium">{outcome.question}</p>
              {outcome.knockout && (
                <Badge variant="outline" title="Failing this rejects the candidate outright">
                  Knock-out
                </Badge>
              )}
              {!outcome.knockout && outcome.weight > 0 && (
                <span className="text-xs text-muted-foreground">
                  weight {outcome.weight}
                </span>
              )}
            </div>
            <p
              className={cn(
                "mt-1 text-sm",
                outcome.status === "unknown" ? "text-muted-foreground" : "",
              )}
            >
              {outcome.reason}
            </p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{outcome.key}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function StatusIcon({ status }: { status: CriterionOutcome["status"] }) {
  if (status === "pass") {
    return <Check className="mt-0.5 size-4 shrink-0" aria-label="pass" />;
  }
  if (status === "fail") {
    return <X className="mt-0.5 size-4 shrink-0 text-destructive" aria-label="fail" />;
  }
  return (
    <CircleHelp
      className="mt-0.5 size-4 shrink-0 text-muted-foreground"
      aria-label="not established"
    />
  );
}
