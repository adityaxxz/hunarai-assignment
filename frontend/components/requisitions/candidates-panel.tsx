"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Phone } from "@/components/phone";
import { CsvImport } from "@/components/requisitions/csv-import";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { addCandidate, listCandidates } from "@/lib/api/client";
import type { Requisition } from "@/lib/api/types";

export function CandidatesPanel({ requisition }: { requisition: Requisition }) {
  const candidates = useQuery({
    queryKey: ["candidates", requisition.id],
    queryFn: () => listCandidates(requisition.id),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Candidates</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          Every candidate needs a value for each declared variable. Hunar rejects a call
          whose custom_data is missing one, so that has to fail here rather than at
          dispatch.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        <CsvImport requisitionId={requisition.id} />

        <Separator />
        <ManualAdd requisition={requisition} />

        <Separator />
        {candidates.isPending && <LoadingState label="Loading candidates" />}
        {candidates.isError && (
          <ErrorState error={candidates.error} onRetry={() => candidates.refetch()} />
        )}
        {candidates.data?.total === 0 && (
          <EmptyState
            title="No candidates yet"
            description="Upload a CSV or add one by hand. Both go through the same validation."
          />
        )}
        {candidates.data && candidates.data.total > 0 && (
          <div>
            <p className="mb-2 text-sm font-medium">
              {candidates.data.total} candidate{candidates.data.total === 1 ? "" : "s"}
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Phone</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  {requisition.candidate_variables.map((variable) => (
                    <TableHead key={variable} className="font-mono text-xs">
                      {variable}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.data.results.map((candidate) => (
                  <TableRow key={candidate.id}>
                    <TableCell className="font-medium">{candidate.name}</TableCell>
                    <TableCell>
                      <Phone value={candidate.phone_e164} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {candidate.source}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          candidate.status === "DO_NOT_CALL" ? "destructive" : "outline"
                        }
                      >
                        {candidate.status}
                      </Badge>
                    </TableCell>
                    {requisition.candidate_variables.map((variable) => {
                      const value = candidate.custom_fields[variable];
                      return (
                        <TableCell key={variable} className="text-xs">
                          {value ? (
                            String(value)
                          ) : (
                            <span className="text-destructive">missing</span>
                          )}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ManualAdd({ requisition }: { requisition: Requisition }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const queryClient = useQueryClient();

  const add = useMutation({
    mutationFn: () =>
      addCandidate(requisition.id, { name, phone, custom_fields: fields }),
    onSuccess: () => {
      setName("");
      setPhone("");
      setFields({});
      queryClient.invalidateQueries({ queryKey: ["candidates", requisition.id] });
      queryClient.invalidateQueries({ queryKey: ["preflight", requisition.id] });
    },
  });

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <Label className="text-sm">Add one by hand</Label>
      <div className="grid gap-3 sm:grid-cols-4">
        <Input
          required
          value={name}
          placeholder="Name"
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          required
          value={phone}
          placeholder="Phone"
          onChange={(e) => setPhone(e.target.value)}
        />
        {requisition.candidate_variables.map((variable) => (
          <Input
            key={variable}
            value={fields[variable] ?? ""}
            placeholder={variable}
            onChange={(e) => setFields((f) => ({ ...f, [variable]: e.target.value }))}
          />
        ))}
      </div>
      {add.isError && <ErrorState error={add.error} />}
      <Button type="submit" variant="outline" size="sm" disabled={add.isPending}>
        {add.isPending ? "Adding…" : "Add candidate"}
      </Button>
    </form>
  );
}
