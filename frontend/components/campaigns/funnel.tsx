"use client";

import { cn } from "@/lib/utils";
import { FUNNEL_STAGES, STAGE_LABELS } from "@/lib/vocab";

/**
 * Stage counts across the top.
 *
 * Every stage is shown even at zero. A funnel that hides its empty stages
 * changes shape as calls move through it, and a recruiter cannot tell whether
 * "not connected" is absent because nobody failed or because the column is not
 * rendered until someone does.
 */
export function Funnel({
  counts,
  selected,
  onSelect,
}: {
  counts: Record<string, number>;
  selected: string | null;
  onSelect: (stage: string | null) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
      {FUNNEL_STAGES.map((stage) => {
        const active = selected === stage;
        return (
          <button
            key={stage}
            type="button"
            onClick={() => onSelect(active ? null : stage)}
            className={cn(
              "rounded-md border p-3 text-left transition-colors",
              active ? "border-foreground bg-muted" : "hover:bg-muted/50",
            )}
          >
            <p className="text-xl tabular-nums">{counts[stage] ?? 0}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{STAGE_LABELS[stage]}</p>
          </button>
        );
      })}
    </div>
  );
}
