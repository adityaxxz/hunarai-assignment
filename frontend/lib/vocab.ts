/**
 * Hunar's enumerations, copied by hand.
 *
 * These are the vendor's vocabulary, not ours: no endpoint lists them, so the
 * only alternative to copying them is a free-text field that fails at dispatch.
 * Mirrors `backend/app/integrations/hunar/types.py` — a value added there needs
 * adding here too.
 */

export const LANGUAGES = [
  "ENGLISH", "HINDI", "TAMIL", "TELUGU", "KANNADA", "MARATHI",
  "MALAYALAM", "GUJARATI", "BENGALI", "TURKISH", "ARABIC", "SPANISH",
] as const;

export const VOICE_PERSONAS = ["NEHA", "ROY", "ZOE", "SAM", "MIRA", "EESHA"] as const;

export const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] as const;

/** Mirrors `services/campaign.VALID_RETRY_INTERVALS`. Hunar rejects anything
 * else, so a free number input would only produce a 400 after the fact. */
export const RETRY_INTERVALS = [0, 3, 6, 9, 12, 24] as const;

/** Funnel stages in the order `routers/campaigns.FUNNEL_STAGES` defines them. */
export const FUNNEL_STAGES = [
  "queued", "dialling", "connected", "engaged", "completed",
  "not_connected", "failed", "dispatch_failed",
] as const;

export const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  dialling: "Dialling",
  connected: "Connected",
  engaged: "Engaged",
  completed: "Completed",
  not_connected: "Not connected",
  failed: "Failed",
  dispatch_failed: "Not dispatched",
};
