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

import type {
  AgentPayload,
  AgentVersion,
  Call,
  CampaignCreate,
  CampaignDetail,
  CampaignPage,
  Candidate,
  CandidatePage,
  Health,
  ImportSummary,
  MappingProposal,
  PreflightReport,
  Requisition,
  RequisitionInput,
} from "@/lib/api/types";

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

/**
 * FastAPI puts everything under `detail`, but ours is not always a string: the
 * campaign endpoint returns `{problems: [...]}`, candidate add returns
 * `{reasons: [...]}`, and agent creation returns `{message, fields}`. Those
 * lists ARE the useful part — collapsing them to "status 422" throws away the
 * sentences the backend wrote specifically for the recruiter.
 */
function messageFor(status: number, body: unknown): string {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : body;

  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(describeItem).join(" ");
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    for (const key of ["problems", "reasons", "message"]) {
      const value = record[key];
      if (typeof value === "string") return value;
      if (Array.isArray(value)) return value.map(describeItem).join(" ");
    }
  }
  return `Request failed with status ${status}.`;
}

function describeItem(item: unknown): string {
  if (typeof item === "string") return item;
  // Pydantic's own validation errors, which look like {loc, msg, type}.
  if (item && typeof item === "object" && "msg" in item) {
    const { loc, msg } = item as { loc?: unknown[]; msg?: string };
    const field = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
    return field ? `${field}: ${msg}` : String(msg);
  }
  return JSON.stringify(item);
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
      // Content-Type only when there is a body to describe. Setting it on a GET
      // makes the request non-simple, which forces a CORS preflight: the browser
      // sends an OPTIONS and waits for it before the real request. On Render's
      // free tier that means a cold start is paid TWICE, and it adds a failure
      // mode (a preflight that is rejected or times out) for no benefit.
      // FormData is left alone: the browser has to set its own Content-Type so
      // it can append the multipart boundary. Setting ours would corrupt the
      // upload.
      headers:
        init?.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json", ...init?.headers }
          : { ...init?.headers },
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

  // 204 has no body to parse, and DELETE is the only caller that returns one.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getHealth(): Promise<Health> {
  return apiFetch<Health>("/health");
}

function post<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) });
}

// --- requisitions ---------------------------------------------------------

export function listRequisitions(): Promise<Requisition[]> {
  return apiFetch<Requisition[]>("/requisitions");
}

export function getRequisition(id: number): Promise<Requisition> {
  return apiFetch<Requisition>(`/requisitions/${id}`);
}

export function createRequisition(input: RequisitionInput): Promise<Requisition> {
  return post<Requisition>("/requisitions", input);
}

export function deleteRequisition(id: number): Promise<void> {
  return apiFetch<void>(`/requisitions/${id}`, { method: "DELETE" });
}

/** The dry run. Returns the exact body that POST /agents/ would receive. */
export function previewAgent(id: number): Promise<AgentPayload> {
  return post<AgentPayload>(`/requisitions/${id}/agent/preview`, null);
}

/** `payload` is what gets sent, so the edited preview is what Hunar receives. */
export function createAgent(id: number, payload: AgentPayload): Promise<AgentVersion> {
  return post<AgentVersion>(`/requisitions/${id}/agent`, payload);
}

export function listAgentVersions(id: number): Promise<AgentVersion[]> {
  return apiFetch<AgentVersion[]>(`/requisitions/${id}/agent`);
}

// --- candidates -----------------------------------------------------------

export function uploadCandidates(id: number, file: File): Promise<MappingProposal> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<MappingProposal>(`/requisitions/${id}/candidates/upload`, {
    method: "POST",
    body: form,
  });
}

export function importCandidates(
  id: number,
  file: File,
  mapping: Record<string, string | null>,
): Promise<ImportSummary> {
  const form = new FormData();
  form.append("file", file);
  form.append("mapping", JSON.stringify(mapping));
  return apiFetch<ImportSummary>(`/requisitions/${id}/candidates/import`, {
    method: "POST",
    body: form,
  });
}

export function addCandidate(
  id: number,
  input: { name: string; phone: string; custom_fields: Record<string, string> },
): Promise<Candidate> {
  return post<Candidate>(`/requisitions/${id}/candidates`, input);
}

export function listCandidates(id: number): Promise<CandidatePage> {
  return apiFetch<CandidatePage>(`/requisitions/${id}/candidates?page_size=200`);
}

export function getPreflight(id: number): Promise<PreflightReport> {
  return apiFetch<PreflightReport>(`/requisitions/${id}/preflight`);
}

// --- campaigns ------------------------------------------------------------

export function createCampaign(input: CampaignCreate): Promise<CampaignDetail> {
  return post<CampaignDetail>("/campaigns", input);
}

/** Reconciles server-side before responding, which is why polling this is what
 * makes the funnel move: Hunar sends one status webhook per call, at the
 * terminal transition, so nothing else would advance it. */
export function getCampaign(id: number): Promise<CampaignDetail> {
  return apiFetch<CampaignDetail>(`/campaigns/${id}`);
}

export function listCalls(id: number): Promise<CampaignPage> {
  return apiFetch<CampaignPage>(`/campaigns/${id}/calls?page_size=200`);
}

/** Stages that can still change on their own. Used to decide whether to poll. */
const ACTIVE_STAGES = new Set(["queued", "dialling", "connected"]);

export function isActive(call: Call): boolean {
  // A retry that has been scheduled is still live even though the last attempt
  // reached a terminal stage, so it counts as active too.
  return ACTIVE_STAGES.has(call.stage) || call.next_retry_scheduled_at !== null;
}
