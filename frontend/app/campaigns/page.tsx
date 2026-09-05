"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { PageContainer } from "@/components/page-container";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * There is no `GET /campaigns`, so this cannot be a list.
 *
 * Rather than fake one from local state — which would show a different set of
 * campaigns per browser and disagree with the database — the page says what is
 * missing and offers the two routes that do work: launch from a requisition, or
 * open a campaign by id.
 */
export default function CampaignsPage() {
  const [id, setId] = useState("");
  const router = useRouter();

  return (
    <PageContainer
      title="Campaigns"
      description="Call batches, the live funnel and per-candidate results."
    >
      <Card>
        <CardContent className="space-y-4 pt-6">
          <p className="text-sm text-muted-foreground">
            The backend has no endpoint that lists campaigns, only{" "}
            <code className="font-mono text-xs">GET /campaigns/&#123;id&#125;</code>. A list
            built here from browser state would show a different set of campaigns on every
            machine and disagree with the database, so it is not built.
          </p>
          <p className="text-sm text-muted-foreground">
            Launch a campaign from its requisition, or open one directly:
          </p>
          <form
            className="flex items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (id) router.push(`/campaigns/${id}`);
            }}
          >
            <div className="space-y-1.5">
              <Label className="text-sm">Campaign id</Label>
              <Input
                value={id}
                inputMode="numeric"
                className="w-32"
                onChange={(e) => setId(e.target.value.replace(/\D/g, ""))}
              />
            </div>
            <Button type="submit" disabled={!id}>
              Open
            </Button>
          </form>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
