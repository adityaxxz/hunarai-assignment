/** Small display helpers shared across the ops screens. */

/**
 * Masked by default, with the country code and last two digits kept.
 *
 * These are real people's mobile numbers on a screen a recruiter will have open
 * in an office, and full numbers are the one field here that is worth anything
 * to someone reading over a shoulder. The last two digits are enough to tell
 * two rows apart, which is all the list view actually needs.
 */
export function maskPhone(phone: string): string {
  if (phone.length < 6) return "•".repeat(phone.length);
  const head = phone.startsWith("+") ? phone.slice(0, 3) : "";
  const tail = phone.slice(-2);
  return `${head}${"•".repeat(phone.length - head.length - 2)}${tail}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "in 12 min" reads better than a timestamp for a retry that has not happened
 * yet, because the only question about it is how long the wait is. */
export function relativeFuture(value: string | null): string | null {
  if (!value) return null;
  const minutes = Math.round((new Date(value).getTime() - Date.now()) / 60_000);
  if (Number.isNaN(minutes)) return null;
  if (minutes <= 0) return "due now";
  if (minutes < 60) return `in ${minutes} min`;
  return `in ${Math.round(minutes / 60)} h`;
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
