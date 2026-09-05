"use client";

import { Check, Minus } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/**
 * What we sent, beside what Hunar derived from it.
 *
 * Hunar does not accept a variable list. It scans the prompt for `{token}`s and
 * computes `custom_variables` itself, and during the live capture it returned an
 * empty list for an agent we were certain had one. That silent disagreement is
 * the failure this component exists to make impossible: a token we believe in
 * that Hunar never saw produces a call rejected at dispatch, and the only place
 * the truth is visible is the read-back.
 */
export function VariableDiff({
  title,
  note,
  sent,
  derived,
  reserved = [],
}: {
  title: string;
  note: string;
  sent: string[];
  derived: string[];
  /** Names Hunar fills in itself, so their absence from `derived` is correct
   * rather than a problem. Without this the panel cries wolf on every agent. */
  reserved?: string[];
}) {
  const names = [...new Set([...sent, ...derived])].sort();

  return (
    <div className="rounded-md border">
      <div className="border-b bg-muted/40 px-3 py-2">
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>
      </div>
      {names.length === 0 ? (
        <p className="px-3 py-3 text-xs text-muted-foreground">Neither side declares any.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th className="px-3 py-1.5 text-left font-normal">Variable</th>
              <th className="px-3 py-1.5 text-left font-normal">In what we sent</th>
              <th className="px-3 py-1.5 text-left font-normal">Derived by Hunar</th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const inSent = sent.includes(name);
              const inDerived = derived.includes(name);
              return (
                <tr key={name} className="border-t">
                  <td className="px-3 py-1.5 font-mono text-xs">{name}</td>
                  <td className="px-3 py-1.5"><Mark on={inSent} /></td>
                  <td className="px-3 py-1.5">
                    <Mark on={inDerived} />
                    {inSent && !inDerived && reserved.includes(name) && (
                      <Badge variant="outline" className="ml-2">
                        Hunar fills this in itself
                      </Badge>
                    )}
                    {inSent && !inDerived && !reserved.includes(name) && (
                      <Badge variant="destructive" className="ml-2">
                        Hunar did not see this token
                      </Badge>
                    )}
                    {!inSent && inDerived && (
                      <Badge variant="secondary" className="ml-2">Hunar added this</Badge>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Mark({ on }: { on: boolean }) {
  return on ? (
    <Check className="size-4 text-foreground" aria-label="yes" />
  ) : (
    <Minus className="size-4 text-muted-foreground" aria-label="no" />
  );
}
