"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ApiHttpError } from "@/lib/api/client";

export function Providers({ children }: { children: React.ReactNode }) {
  // Created in state, not at module scope: a module-level client is shared
  // across requests on the server and would leak one user's cache into another.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Off because the polling views refetch on their own interval, and
            // refocus refetching on top of that produces a request every time
            // the recruiter alt-tabs back.
            refetchOnWindowFocus: false,
            staleTime: 10_000,
            retry: (failureCount, error) => {
              // A 4xx will fail identically the second time; only retry things
              // that might genuinely be transient, which on the free tier is
              // mostly a cold start.
              if (error instanceof ApiHttpError && error.status < 500) return false;
              return failureCount < 1;
            },
          },
        },
      }),
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
