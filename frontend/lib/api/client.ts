/**
 * Typed fetch functions against our FastAPI backend.
 *
 * Plain functions rather than a class: there is no per-request state to hold,
 * and a class would only be a namespace with extra ceremony.
 *
 * The browser never talks to Hunar. Everything goes browser -> our backend ->
 * Hunar, which is what keeps the Hunar API key out of anything shipped to the
 * client.
 */

import type { Health } from "@/lib/api/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * 75 seconds, which looks absurd next to a normal API timeout and is not.
 *
 * Render's free tier spins the service down after 15 minutes idle and takes 30
 * to 60 seconds to wake. A conventional 10-second timeout would abort every
 * first request after an idle period and show the reviewer an error on a
 * perfectly healthy deployment.
 */
export const REQUEST_TIMEOUT_MS = 75_000;

/**
 * When to admit the backend is probably asleep. A spinner sitting silently for
 * 40 seconds reads as broken; the same 40 seconds with an explanation reads as
 * a known free-tier cost.
 */
export const COLD_START_HINT_MS = 8_000;

/**
 * Three outcomes, not one Error, because the UI treats them differently: a
 * network failure is "check your connection or the backend is down", a timeout
 * after 75s is "it did not wake up", and an HTTP error is the backend answering
 * with a reason worth showing.
 */
export type ApiErrorKind = "network" | "timeout" | "http";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;

  constructor(kind: ApiErrorKind, message: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
  }
}

export class ApiNetworkError extends ApiError {
  constructor(message = "Could not reach the backend.") {
    super("network", message);
  }
}

export class ApiTimeoutError extends ApiError {
  constructor(message = "The backend did not respond in time.") {
    super("timeout", message);
  }
}

export class ApiHttpError extends ApiError {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super("http", message);
    this.status = status;
    this.body = body;
  }
}

/** Narrowing helper. Checks the `kind` field rather than `instanceof`, which is
 * unreliable once a class crosses a bundle or a serialisation boundary. */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof Error && "kind" in error;
}

async function readBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function messageFor(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return `Request failed with status ${status}.`;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  // Tracked separately because an aborted fetch reports the same AbortError
  // whether we timed it out or the caller navigated away.
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (cause) {
    if (timedOut) {
      throw new ApiTimeoutError(
        `No response after ${REQUEST_TIMEOUT_MS / 1000}s. The backend may be asleep or down.`,
      );
    }
    throw new ApiNetworkError();
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    const body = await readBody(response);
    throw new ApiHttpError(response.status, messageFor(response.status, body), body);
  }

  return (await response.json()) as T;
}

export function getHealth(): Promise<Health> {
  return apiFetch<Health>("/health");
}
