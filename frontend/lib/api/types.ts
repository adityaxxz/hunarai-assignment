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
