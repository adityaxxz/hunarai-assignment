"use client";

import { useQuery } from "@tanstack/react-query";

import { getHealth } from "@/lib/api/client";

/**
 * Driven by the backend's /health rather than a NEXT_PUBLIC_ flag, so the banner
 * cannot claim one thing while the server does another.
 *
 * Worded as a design decision because it is one: a voice product cannot be
 * demonstrated by dialling live people, and the trial API key expires. Renders
 * nothing while loading or on error — an unreachable backend is the health
 * panel's problem to report, not something to say twice.
 */
export function DemoBanner() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: getHealth });

  if (!data?.demo_mode) return null;

  return (
    <div className="border-b bg-muted/50 px-6 py-2 text-center text-sm text-muted-foreground">
      Running in demo mode against a simulated voice provider, so the full call
      funnel is explorable without dialling real people or depending on a live
      API key.
    </div>
  );
}
