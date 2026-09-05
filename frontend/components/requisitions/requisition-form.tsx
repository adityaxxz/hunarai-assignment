"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { CriteriaEditor, emptyCriterion } from "@/components/requisitions/criteria-editor";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { createRequisition } from "@/lib/api/client";
import type { Criterion, RequisitionInput } from "@/lib/api/types";
import { LANGUAGES, VOICE_PERSONAS } from "@/lib/vocab";

const BLANK: RequisitionInput = {
  title: "", location: "", language: "HINDI", voice_persona: "NEHA",
  shift: "", pay_min: null, pay_max: null, openings: 1,
  criteria: [emptyCriterion()], candidate_variables: [],
};

export function RequisitionForm({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState<RequisitionInput>(BLANK);
  const [variables, setVariables] = useState("");
  const router = useRouter();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: createRequisition,
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["requisitions"] });
      onDone();
      router.push(`/requisitions/${created.id}`);
    },
  });

  function set<K extends keyof RequisitionInput>(key: K, value: RequisitionInput[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    mutation.mutate({
      ...form,
      shift: form.shift || null,
      // Dropped rather than sent blank: the backend rejects an empty key, and a
      // half-typed row the recruiter abandoned is not an error worth showing.
      criteria: form.criteria.filter((c: Criterion) => c.key && c.question),
      candidate_variables: variables.split(",").map((v) => v.trim()).filter(Boolean),
    });
  }

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm font-medium">New requisition</CardTitle></CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Title">
              <Input required value={form.title} placeholder="Delivery Rider"
                onChange={(e) => set("title", e.target.value)} />
            </Field>
            <Field label="Location">
              <Input required value={form.location} placeholder="Bengaluru"
                onChange={(e) => set("location", e.target.value)} />
            </Field>
            <Field label="Language">
              <Select value={form.language} onValueChange={(v) => set("language", v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {LANGUAGES.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Voice">
              <Select value={form.voice_persona} onValueChange={(v) => set("voice_persona", v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {VOICE_PERSONAS.map((v) => <SelectItem key={v} value={v}>{v}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Shift">
              <Input value={form.shift ?? ""} placeholder="Morning, 7am to 3pm"
                onChange={(e) => set("shift", e.target.value)} />
            </Field>
            <Field label="Openings">
              <Input type="number" min={1} value={form.openings}
                onChange={(e) => set("openings", Number(e.target.value))} />
            </Field>
            <Field label="Pay from (₹ / month)">
              <Input type="number" min={0} value={form.pay_min ?? ""}
                onChange={(e) => set("pay_min", e.target.value ? Number(e.target.value) : null)} />
            </Field>
            <Field label="Pay to (₹ / month)">
              <Input type="number" min={0} value={form.pay_max ?? ""}
                onChange={(e) => set("pay_max", e.target.value ? Number(e.target.value) : null)} />
            </Field>
          </div>

          <Field
            label="Candidate variables"
            hint="Comma separated, snake_case. Each becomes a {token} in the agent prompt, and every candidate must have a value for it or the call is rejected."
          >
            <Input value={variables} placeholder="applied_role, city"
              className="font-mono text-xs"
              onChange={(e) => setVariables(e.target.value)} />
          </Field>

          <div>
            <Label className="text-sm">Screening criteria</Label>
            <p className="mb-3 mt-1 text-xs text-muted-foreground">
              One definition drives three things: the questions in the agent prompt, the
              result schema Hunar fills in, and the rubric the answers are scored against.
            </p>
            <CriteriaEditor criteria={form.criteria} onChange={(c) => set("criteria", c)} />
          </div>

          {mutation.isError && <ErrorState error={mutation.error} />}

          <div className="flex gap-2">
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Creating…" : "Create requisition"}
            </Button>
            <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-sm">{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
