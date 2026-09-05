"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { COLD_START_HINT_MS, isApiError } from "@/lib/api/client";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  const [slow, setSlow] = useState(false);

  // The cold-start message lives in the shared loading state rather than in each
  // screen, so every future view gets it for free instead of someone having to
  // remember. Render's free tier sleeps after 15 minutes idle and takes 30-60s
  // to wake, and a silent spinner for that long is indistinguishable from a bug.
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), COLD_START_HINT_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">{label}…</p>
      {slow && (
        <p className="max-w-sm text-sm text-muted-foreground">
          Waking the backend. This takes up to a minute on the free tier, which
          sleeps after 15 minutes of inactivity.
        </p>
      )}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const { title, detail } = describe(error);

  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <h2 className="text-base font-medium">{title}</h2>
      <p className="max-w-md text-sm text-muted-foreground">{detail}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
          Try again
        </Button>
      )}
    </div>
  );
}

/** A network failure and a 500 need different words: one is "nothing answered",
 * the other is "something answered and it went wrong". Collapsing them into
 * "Something went wrong" throws away the only useful half of the message. */
function describe(error: unknown): { title: string; detail: string } {
  if (!isApiError(error)) {
    return {
      title: "Something went wrong",
      detail: error instanceof Error ? error.message : "An unexpected error occurred.",
    };
  }

  if (error.kind === "network") {
    return {
      title: "Cannot reach the backend",
      detail:
        "No response from the API. Check that it is running and that NEXT_PUBLIC_API_BASE_URL points at it.",
    };
  }

  if (error.kind === "timeout") {
    return {
      title: "The backend did not wake up",
      detail:
        "No response within 75 seconds. On the free tier a cold start takes 30-60 seconds, so this usually means the service is down rather than asleep.",
    };
  }

  return { title: "The backend returned an error", detail: error.message };
}

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <h2 className="text-base font-medium">{title}</h2>
      {description && (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
