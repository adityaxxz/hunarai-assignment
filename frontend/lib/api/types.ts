/**
 * Wire shapes for the backend responses the frontend actually consumes.
 *
 * These are hand-maintained, not generated from the backend's OpenAPI schema.
 * A generated client is a large file nobody reviews, and it adds a regeneration
 * step that goes stale silently on a three-day build — the failure mode is a
 * type file that confidently describes an API that has moved on.
 *
 * The trade-off, stated plainly because it is a real cost: a backend field
 * rename will NOT be caught by the compiler. It will typecheck, build, and then
 * be `undefined` at runtime. Any change to a backend response schema requires a
 * matching edit in this file. Also noted in the README's known constraints.
 *
 * Field names are kept identical to the backend's. No camelCase translation: a
 * rename is a mapping layer you then have to remember in both directions, and
 * every future debugging session pays for it.
 */

export interface Health {
  status: string;
  demo_mode: boolean;
  version: string;
}

// --- requisitions ---------------------------------------------------------

/** Mirrors backend `schemas.Criterion`. One screening question, and the unit
 * that drives the agent prompt, the result_schema and the rubric at once. */
export interface Criterion {
  key: string;
  question: string;
  type: "boolean" | "string";
  knockout: boolean;
  weight: number;
  expected: boolean | string | null;
}

export interface RequisitionInput {
  title: string;
  location: string;
  language: string;
  voice_persona: string;
  shift: string | null;
  pay_min: number | null;
  pay_max: number | null;
  openings: number;
  criteria: Criterion[];
  candidate_variables: string[];
}

export interface Requisition extends RequisitionInput {
  id: number;
}

/** The exact body sent to Hunar's POST /agents/. Editable in the agent panel,
 * which is why it is a request shape rather than a display shape. */
export interface AgentPayload {
  name: string;
  language: string;
  voice_persona: string;
  persona_name: string | null;
  agent_prompt: string;
  objective: string;
  introduction: string;
  result_prompt: string;
  result_schema: Record<string, unknown>;
}

export interface AgentVersion extends AgentPayload {
  id: number;
  requisition_id: number | null;
  version: number;
  hunar_agent_id: string | null;
  // Read back from Hunar after creation. Deliberately separate from what we
  // sent: Hunar derives custom_variables from {token}s in the prompt, and the
  // difference between the two is the thing the agent panel exists to show.
  custom_variables: string[];
  required_variables: string[];
  result_variables: string[];
}

// --- candidates -----------------------------------------------------------

export interface MappingProposal {
  headers: string[];
  row_count: number;
  /** null means the backend could not tell which column this is. */
  mapping: Record<string, string | null>;
  required_variables: string[];
  preview: Record<string, string>[];
}

export interface ImportProblem {
  row: number;
  phone: string | null;
  reasons: string[];
}

export interface ImportSummary {
  total: number;
  imported: number;
  rejected: number;
  duplicates_in_file: number;
  duplicates_existing: number;
  do_not_call: number;
  problems: ImportProblem[];
}

export interface Candidate {
  id: number;
  requisition_id: number | null;
  name: string;
  phone_e164: string;
  source: string;
  status: string;
  custom_fields: Record<string, unknown>;
}

export interface CandidatePage {
  total: number;
  page: number;
  page_size: number;
  results: Candidate[];
}

export interface PreflightReport {
  candidates: number;
  dialable: number;
  excluded: { candidate_id: number; name: string; reasons: string[] }[];
  agent_version_id: number | null;
  hunar_agent_id: string | null;
  required_variables: string[];
  ready: boolean;
  blockers: string[];
}

// --- campaigns ------------------------------------------------------------

export interface Guardrails {
  allowed_days: string[];
  earliest_call_time: string;
  last_call_time: string;
}

export interface RetryConfig {
  max_retry_count: number | null;
  retry_interval_hours: number | null;
}

export interface CampaignCreate {
  requisition_id: number;
  name: string;
  /** SOURCING marks a batch that reaches people who did not apply. It changes
   * nothing about dispatch — same endpoint, same rows — and exists so the funnel
   * can be reported on separately. */
  kind?: "SCREENING" | "SOURCING";
  agent_version_id?: number | null;
  guardrails?: Guardrails | null;
  retry_config?: RetryConfig | null;
  timezone: string;
}

export interface CampaignEstimate {
  calls_to_place: number;
  worst_case_attempts: number;
  estimated_talk_minutes_if_all_connect: number;
  assumptions: string[];
  unknown: string[];
}

export interface CampaignDetail {
  id: number;
  name: string;
  requisition_id: number | null;
  agent_version_id: number;
  request_id: string | null;
  status: string;
  dispatch_error: string | null;
  dispatched_at: string | null;
  total_calls: number;
  funnel: Record<string, number>;
  dialling_now: boolean;
  dial_starts_at: string | null;
  dial_window_note: string;
  estimate: CampaignEstimate;
}

export interface Call {
  id: number;
  candidate_id: number;
  candidate_name: string;
  hunar_call_id: string | null;
  stage: string;
  status: string | null;
  lifecycle_status: string | null;
  engagement_status: string | null;
  retry_count: number;
  retries_left: number | null;
  next_retry_scheduled_at: string | null;
  dispatch_error: string | null;
  has_result: boolean;
  // Terminal is not the same as finished: Hunar is eventually consistent after
  // COMPLETED. A reason here means the backend stopped chasing a result.
  reconcile_stopped_at: string | null;
  reconcile_stopped_reason: string | null;
}

export interface CampaignPage {
  total: number;
  page: number;
  page_size: number;
  results: Call[];
}

// --- call detail ----------------------------------------------------------

export interface CriterionOutcome {
  key: string;
  question: string;
  knockout: boolean;
  weight: number;
  value: unknown;
  status: "pass" | "fail" | "unknown";
  reason: string;
}

export interface Evaluation {
  decision: string;
  score: number;
  reasons: CriterionOutcome[];
}

export interface CallEvent {
  id: number;
  event_type: string;
  received_at: string;
  processed_at: string | null;
  processing_error: string | null;
  /** False for an event that arrived before its call row existed. */
  linked: boolean;
}

export interface Override {
  decision: string;
  reason_code: string;
  reason_label: string;
  note: string | null;
  at: string;
}

export type OverrideReasonCode =
  | "SPOKE_TO_CANDIDATE"
  | "AGENT_MISHEARD"
  | "RESULT_INCOMPLETE"
  | "REQUIREMENTS_CHANGED"
  | "OTHER";

export interface OverrideInput {
  decision: "QUALIFIED" | "REJECTED" | "UNDECIDED";
  reason_code: OverrideReasonCode;
  note: string | null;
}

export interface CallDetail {
  id: number;
  campaign_id: number;
  hunar_call_id: string | null;
  candidate_id: number;
  candidate_name: string;
  candidate_phone: string;
  candidate_custom_fields: Record<string, unknown>;
  requisition_id: number | null;
  requisition_title: string | null;

  stage: string;
  status: string | null;
  lifecycle_status: string | null;
  engagement_status: string | null;
  answered_by: string | null;
  call_ended_by: string | null;
  redial_status: string | null;
  retry_count: number;
  retries_left: number | null;
  next_retry_scheduled_at: string | null;
  duration_seconds: number | null;
  user_speech_duration: number | null;
  started_at: string | null;
  ended_at: string | null;
  dispatch_error: string | null;
  last_reconciled_at: string | null;
  reconcile_stopped_at: string | null;
  reconcile_stopped_reason: string | null;

  result: Record<string, unknown> | null;
  evaluation: Evaluation | null;
  override: Override | null;
  /** The override where one exists, otherwise the computed decision. */
  effective_decision: string | null;

  recording_available: boolean;
  /** True when the proxy serves generated silence rather than a real call. */
  recording_simulated: boolean;

  timeline: CallEvent[];
}

// --- sourcing (Module B) --------------------------------------------------

export interface SourcingSearch {
  id: number;
  jd_text: string;
  query: Record<string, unknown>;
  /** "gemini" or "fallback". Shown, because a keyword-extracted query should
   * not be mistaken for one a model wrote. */
  query_source: string;
  note: string;
  titles: string[];
  locations: string[];
  provider: string;
}

export interface SourcingProfile {
  full_name: string;
  headline: string | null;
  current_title: string | null;
  current_company: string | null;
  location: string | null;
  linkedin_url: string | null;
  dedupe_key: string;
  phone_e164: string | null;
  /** "provider" | "fixture" | "unresolved". Which resolver produced the number. */
  resolver: string;
  resolver_label: string;
  resolver_detail: string;
  already_a_candidate: boolean;
  do_not_call: boolean;
}

export interface SearchRun {
  search_id: number;
  provider: string;
  total_available: number | null;
  notes: string[];
  dialable: number;
  profiles: SourcingProfile[];
}

export interface SourcingImportResult {
  requisition_id: number;
  imported: number;
  skipped: { name: string; reason: string }[];
}

export interface SourcingInsights {
  campaign_id: number;
  kind: string;
  total_calls: number;
  answered: number;
  interested: number;
  /** Null rather than zero when nothing has been answered: 0% interest and
   * "nobody picked up" are different facts. */
  interest_rate: number | null;
  notice_period: Record<string, number>;
  objections: Record<string, number>;
  callback_times: string[];
}
