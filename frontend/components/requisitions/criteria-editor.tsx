"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import type { Criterion } from "@/lib/api/types";

export function emptyCriterion(): Criterion {
  return { key: "", question: "", type: "boolean", knockout: false, weight: 1, expected: null };
}

/** Dropped when deriving a key. Screening questions almost all open with these,
 * and keeping them produces `do_you_have_a` — which then becomes a
 * result_schema field name and a rubric label that says nothing. */
const STOP_WORDS = new Set([
  "do", "does", "did", "are", "is", "was", "were", "can", "could", "will",
  "would", "have", "has", "had", "you", "your", "the", "a", "an", "any", "to",
  "of", "in", "on", "at", "for", "with", "and", "or",
]);

/** Backend enforces `^[a-z][a-z0-9_]*$`. Deriving it from the question means the
 * recruiter types one field instead of two and never sees that regex. */
export function keyFromQuestion(question: string): string {
  const words = question
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const meaningful = words.filter((word) => !STOP_WORDS.has(word));
  // Falls back to the raw words when a question is nothing but stop words, so
  // the field is never left empty and unsubmittable.
  return (meaningful.length > 0 ? meaningful : words)
    .slice(0, 3)
    .join("_")
    .replace(/^[^a-z]+/, "");
}

export function CriteriaEditor({
  criteria,
  onChange,
}: {
  criteria: Criterion[];
  onChange: (next: Criterion[]) => void;
}) {
  function update(index: number, patch: Partial<Criterion>) {
    onChange(criteria.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  return (
    <div className="space-y-3">
      {criteria.map((criterion, index) => (
        <div key={index} className="grid gap-3 rounded-md border p-3 sm:grid-cols-12">
          <div className="sm:col-span-5">
            <Label className="text-xs text-muted-foreground">Question the agent asks</Label>
            <Input
              value={criterion.question}
              placeholder="Do you have a valid driving licence?"
              onChange={(e) => {
                // Key follows the question only while it has not been edited by
                // hand, so a deliberate key is never overwritten by a typo fix.
                const key =
                  criterion.key === "" || criterion.key === keyFromQuestion(criterion.question)
                    ? keyFromQuestion(e.target.value)
                    : criterion.key;
                update(index, { question: e.target.value, key });
              }}
            />
          </div>
          <div className="sm:col-span-3">
            <Label className="text-xs text-muted-foreground">Key</Label>
            <Input
              value={criterion.key}
              className="font-mono text-xs"
              onChange={(e) => update(index, { key: e.target.value })}
            />
          </div>
          <div className="sm:col-span-2">
            <Label className="text-xs text-muted-foreground">Answer</Label>
            <Select
              value={criterion.type}
              onValueChange={(value) =>
                update(index, {
                  type: value as Criterion["type"],
                  // A string criterion cannot carry a boolean `expected`, and a
                  // boolean one defaults to true server-side.
                  expected: value === "boolean" ? true : null,
                })
              }
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="boolean">Yes / no</SelectItem>
                <SelectItem value="string">Free text</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-1">
            <Label className="text-xs text-muted-foreground">Weight</Label>
            <Input
              type="number" min={0} max={100} value={criterion.weight}
              onChange={(e) => update(index, { weight: Number(e.target.value) })}
            />
          </div>
          <div className="flex items-end justify-between gap-3 sm:col-span-1">
            <label className="flex items-center gap-1.5 text-xs">
              <Checkbox
                checked={criterion.knockout}
                onCheckedChange={(v) => update(index, { knockout: v === true })}
              />
              <span title="A failed knockout rejects the candidate outright">Knock-out</span>
            </label>
            <Button
              type="button" variant="ghost" size="icon"
              aria-label="Remove criterion"
              onClick={() => onChange(criteria.filter((_, i) => i !== index))}
            >
              <Trash2 className="size-4" aria-hidden />
            </Button>
          </div>
        </div>
      ))}
      <Button
        type="button" variant="outline" size="sm"
        onClick={() => onChange([...criteria, emptyCriterion()])}
      >
        <Plus className="size-4" aria-hidden /> Add criterion
      </Button>
    </div>
  );
}
