# fixtures/

Ground truth captured from the live Hunar API, used to build the simulator in
task 4 against observed reality rather than the docs, which we already know are
wrong in several places (see `current-status.md`).

## `raw/` — gitignored, never commit

Written by the two capture scripts in `../scripts/`. It is excluded in the root
`.gitignore` because every file in it contains **a real phone number** and, for
the webhook captures, **real HMAC signatures computed with the live API key**.

| File | Written by | Contents |
| --- | --- | --- |
| `01-agent-create.json` | `capture_live_call.py` | Raw `POST /agents/` response |
| `02-agent-detail.json` | `capture_live_call.py` | Raw `GET /agents/{id}/`, showing the `custom_variables` / `required_variables` / `result_variables` Hunar computes from the prompts |
| `03-call-create.json` | `capture_live_call.py` | Raw `POST /calls/` response |
| `poll-NNN.json` | `capture_live_call.py` | Raw `GET /calls/{id}/` on every 5s poll, in order, so the real status progression is captured and not just the final state |
| `webhook-NNN-<path>.json` | `webhook_catcher.py` | One inbound webhook: `path`, verbatim `headers` (including `X-Hunar-Signature` and `X-Hunar-Timestamp`), the raw `body` string, and `received_at` |

The four callback paths map to the four event types:
`/hunar/status`, `/hunar/recording`, `/hunar/result`, `/hunar/summary`.

## `observed_shapes.md` — committed

Written after the capture. For each webhook event type and for the call detail
response: the exact field list, observed types, which fields came back null, and
every difference from what `app/integrations/hunar/types.py` models. This file
carries no phone numbers and no signatures.

## How to run a capture

```bash
# terminal 1
cd backend && uv run python -m scripts.webhook_catcher

# terminal 2
cloudflared tunnel --url http://localhost:8787

# terminal 3 — SPENDS REAL CALLING MINUTES, places exactly one call
cd backend && uv run python -m scripts.capture_live_call \
    --mobile +91XXXXXXXXXX --name "Name" --tunnel https://<id>.trycloudflare.com
```

Retries are pinned to `max_retry_count: 0, retry_interval_hours: 0`, so a missed
call cannot silently redial and spend more minutes than the one call requested.

### Timing: run this after 08:00 IST

The org's guardrail policy has a floor, discovered from a live 400:

    {"success": false, "message": "Minimum allowed earliest_call_time is 08:00.", "details": []}

We have no outbound number, so `from_phone_number` is unavailable and our
guardrails cannot be looser than the org default. More importantly, a call placed
outside the allowed window is **not rejected, it is scheduled** — it would dial
hours later with the tunnel dead, spending minutes and capturing nothing. The
script refuses to place a call outside the window; `--force` overrides.

### Useful flags

| Flag | Why |
| --- | --- |
| `--agent-id <uuid>` | Reuse an ARFDE-CAPTURE agent from a previous attempt instead of adding another to an org that already has 100+ |
| `--yes` | Skip the "type YES" confirmation |
| `--force` | Place the call even though now is outside the allowed window |
