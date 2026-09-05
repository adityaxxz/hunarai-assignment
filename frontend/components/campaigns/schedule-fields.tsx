"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ORG_EARLIEST_CALL_TIME } from "@/lib/campaign-rules";
import { RETRY_INTERVALS, WEEKDAYS } from "@/lib/vocab";

export interface Schedule {
  useGuardrails: boolean;
  days: string[];
  earliest: string;
  latest: string;
  useRetries: boolean;
  maxRetries: number;
  interval: number;
}

/** When and how often Hunar may dial. Split out of the launch page purely for
 * length: it is form markup, and the page is easier to read without it. */
export function ScheduleFields({
  schedule,
  onChange,
}: {
  schedule: Schedule;
  onChange: (next: Schedule) => void;
}) {
  const set = <K extends keyof Schedule>(key: K, value: Schedule[K]) =>
    onChange({ ...schedule, [key]: value });

  return (
    <>
      <div className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={schedule.useGuardrails}
            onCheckedChange={(v) => set("useGuardrails", v === true)}
          />
          Set calling hours for this campaign
        </label>
        {!schedule.useGuardrails && (
          <p className="text-xs text-muted-foreground">
            The organisation default applies, which permits no calling before{" "}
            {ORG_EARLIEST_CALL_TIME}.
          </p>
        )}
        {schedule.useGuardrails && (
          <div className="space-y-3 rounded-md border p-4">
            <div>
              <Label className="text-xs text-muted-foreground">Days</Label>
              <div className="mt-2 flex flex-wrap gap-3">
                {WEEKDAYS.map((day) => (
                  <label key={day} className="flex items-center gap-1.5 text-sm">
                    <Checkbox
                      checked={schedule.days.includes(day)}
                      onCheckedChange={(v) =>
                        set(
                          "days",
                          v === true
                            ? [...schedule.days, day]
                            : schedule.days.filter((d) => d !== day),
                        )
                      }
                    />
                    {day}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Earliest</Label>
                <Input
                  type="time"
                  value={schedule.earliest}
                  onChange={(e) => set("earliest", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Latest</Label>
                <Input
                  type="time"
                  value={schedule.latest}
                  onChange={(e) => set("latest", e.target.value)}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={schedule.useRetries}
            onCheckedChange={(v) => set("useRetries", v === true)}
          />
          Retry candidates who do not pick up
        </label>
        {schedule.useRetries && (
          <div className="grid gap-3 rounded-md border p-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">
                Attempts after the first
              </Label>
              <Input
                type="number"
                min={0}
                max={10}
                value={schedule.maxRetries}
                onChange={(e) => set("maxRetries", Number(e.target.value))}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">
                Hours between attempts
              </Label>
              <Select
                value={String(schedule.interval)}
                onValueChange={(v) => set("interval", Number(v))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RETRY_INTERVALS.map((hours) => (
                    <SelectItem key={hours} value={String(hours)}>
                      {hours}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Hunar accepts only these values.
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
