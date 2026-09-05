"use client";

import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ErrorState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { importCandidates, uploadCandidates } from "@/lib/api/client";
import type { ImportSummary, MappingProposal } from "@/lib/api/types";

/** Sentinel for "not mapped". Radix Select forbids an empty-string item value,
 * and the backend wants null, so the translation happens at the boundary. */
const UNMAPPED = "__none__";

/**
 * Upload, then confirm, then import.
 *
 * Two steps on purpose. A silently mis-mapped column dials the wrong people
 * about the wrong job, and that is invisible in "imported 400 candidates" but
 * obvious in a preview whose name column is full of phone numbers.
 */
export function CsvImport({ requisitionId }: { requisitionId: number }) {
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const upload = useMutation({
    mutationFn: (chosen: File) => uploadCandidates(requisitionId, chosen),
    onSuccess: (proposal) => setMapping(proposal.mapping),
  });

  const runImport = useMutation({
    mutationFn: () => importCandidates(requisitionId, file as File, mapping),
    onSuccess: (result) => {
      setSummary(result);
      queryClient.invalidateQueries({ queryKey: ["candidates", requisitionId] });
      queryClient.invalidateQueries({ queryKey: ["preflight", requisitionId] });
    },
  });

  function reset() {
    setFile(null);
    setMapping({});
    setSummary(null);
    upload.reset();
    runImport.reset();
    if (inputRef.current) inputRef.current.value = "";
  }

  const proposal = upload.data;
  const unresolved = proposal
    ? Object.entries(mapping).filter(([, column]) => !column).map(([field]) => field)
    : [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="text-sm file:mr-3 file:rounded-md file:border file:bg-background file:px-3 file:py-1.5 file:text-sm"
          onChange={(e) => {
            const chosen = e.target.files?.[0] ?? null;
            setFile(chosen);
            setSummary(null);
            runImport.reset();
            if (chosen) upload.mutate(chosen);
          }}
        />
        {upload.isPending && <span className="text-sm text-muted-foreground">Reading…</span>}
        {(proposal || summary) && (
          <Button variant="ghost" size="sm" onClick={reset}>
            Start over
          </Button>
        )}
      </div>

      {upload.isError && <ErrorState error={upload.error} />}

      {proposal && !summary && (
        <MappingStep
          proposal={proposal}
          mapping={mapping}
          onChange={setMapping}
        />
      )}

      {runImport.isError && <ErrorState error={runImport.error} />}

      {proposal && !summary && (
        <div className="flex items-center gap-3">
          <Button onClick={() => runImport.mutate()} disabled={runImport.isPending}>
            {runImport.isPending
              ? "Importing…"
              : `Import ${proposal.row_count} row${proposal.row_count === 1 ? "" : "s"}`}
          </Button>
          {unresolved.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {unresolved.join(", ")} not mapped. Rows will be rejected with that reason
              rather than imported half-filled.
            </p>
          )}
        </div>
      )}

      {summary && <ImportResult summary={summary} />}
    </div>
  );
}

function MappingStep({
  proposal,
  mapping,
  onChange,
}: {
  proposal: MappingProposal;
  mapping: Record<string, string | null>;
  onChange: (next: Record<string, string | null>) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <Label className="text-sm">Which column is which</Label>
        <p className="mt-1 text-xs text-muted-foreground">
          Anything the backend could not identify is left blank rather than guessed. A
          wrong guess here calls the wrong person.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {Object.keys(mapping).map((field) => (
            <div key={field} className="space-y-1">
              <Label className="font-mono text-xs">
                {field}
                {(field === "name" || field === "phone" ||
                  proposal.required_variables.includes(field)) && (
                  <span className="ml-1 text-destructive">*</span>
                )}
              </Label>
              <Select
                value={mapping[field] ?? UNMAPPED}
                onValueChange={(value) =>
                  onChange({ ...mapping, [field]: value === UNMAPPED ? null : value })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Not mapped" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={UNMAPPED}>Not mapped</SelectItem>
                  {proposal.headers.map((header) => (
                    <SelectItem key={header} value={header}>
                      {header}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      </div>

      <div>
        <Label className="text-sm">
          First {proposal.preview.length} of {proposal.row_count} rows
        </Label>
        <div className="mt-2 overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                {proposal.headers.map((header) => (
                  <TableHead key={header} className="whitespace-nowrap">
                    {header}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {proposal.preview.map((row, index) => (
                <TableRow key={index}>
                  {proposal.headers.map((header) => (
                    <TableCell key={header} className="whitespace-nowrap text-xs">
                      {row[header] || "—"}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}

function ImportResult({ summary }: { summary: ImportSummary }) {
  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap gap-2">
        <Badge variant="secondary">{summary.imported} imported</Badge>
        {summary.rejected > 0 && (
          <Badge variant="destructive">{summary.rejected} rejected</Badge>
        )}
        {summary.duplicates_in_file > 0 && (
          <Badge variant="outline">{summary.duplicates_in_file} duplicated in the file</Badge>
        )}
        {summary.duplicates_existing > 0 && (
          <Badge variant="outline">{summary.duplicates_existing} already imported</Badge>
        )}
        {summary.do_not_call > 0 && (
          <Badge variant="outline">{summary.do_not_call} on the do-not-call list</Badge>
        )}
      </div>

      {summary.problems.length === 0 ? (
        <p className="text-sm text-muted-foreground">Every row imported cleanly.</p>
      ) : (
        <div>
          <p className="mb-2 text-sm">
            Every row that did not import, with every reason it did not:
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-20">Row</TableHead>
                <TableHead>Reasons</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.problems.map((problem) => (
                <TableRow key={problem.row}>
                  <TableCell className="tabular-nums">{problem.row}</TableCell>
                  <TableCell className="text-sm">{problem.reasons.join("; ")}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
