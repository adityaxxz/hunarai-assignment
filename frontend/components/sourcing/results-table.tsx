"use client";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Phone } from "@/components/phone";
import type { SourcingProfile } from "@/lib/api/types";

/**
 * Search results, with the resolver badge on every row.
 *
 * The badge is the honest part of Module B and it is deliberately not tucked
 * into a tooltip. PDL's free tier returns the profile and withholds the number,
 * so most of these numbers come from a demo resolver rather than the vendor. A
 * recruiter about to call thirty strangers needs to know which is which before
 * they press dial, not after.
 */
export function ResultsTable({
  profiles,
  selected,
  onToggle,
}: {
  profiles: SourcingProfile[];
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-10" />
          <TableHead>Person</TableHead>
          <TableHead>Now</TableHead>
          <TableHead>Location</TableHead>
          <TableHead>Number</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {profiles.map((profile) => {
          const blocked = !profile.phone_e164 || profile.do_not_call || profile.already_a_candidate;
          return (
            <TableRow key={profile.dedupe_key}>
              <TableCell>
                <Checkbox
                  checked={selected.has(profile.dedupe_key)}
                  disabled={blocked}
                  aria-label={`Select ${profile.full_name}`}
                  onCheckedChange={() => onToggle(profile.dedupe_key)}
                />
              </TableCell>
              <TableCell>
                <p className="font-medium">{profile.full_name}</p>
                {profile.headline && (
                  <p className="text-xs text-muted-foreground">{profile.headline}</p>
                )}
                {profile.linkedin_url && (
                  <a
                    href={profile.linkedin_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-xs underline underline-offset-4"
                  >
                    Profile
                  </a>
                )}
              </TableCell>
              <TableCell className="text-sm">
                {profile.current_title ?? "—"}
                {profile.current_company && (
                  <span className="block text-xs text-muted-foreground">
                    {profile.current_company}
                  </span>
                )}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {profile.location ?? "—"}
              </TableCell>
              <TableCell>
                <div className="flex flex-col items-start gap-1">
                  {profile.phone_e164 ? (
                    <Phone value={profile.phone_e164} />
                  ) : (
                    <span className="text-xs text-muted-foreground">none</span>
                  )}
                  <Badge
                    variant={profile.resolver === "provider" ? "secondary" : "outline"}
                    title={profile.resolver_detail}
                  >
                    {profile.resolver_label}
                  </Badge>
                  {profile.do_not_call && (
                    <Badge variant="destructive">On the do-not-call list</Badge>
                  )}
                  {profile.already_a_candidate && (
                    <Badge variant="outline">Already a candidate</Badge>
                  )}
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
