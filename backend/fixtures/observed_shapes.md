# Observed Hunar payload shapes

Recorded from two real calls on 5 September 2026. Captures live in `raw/`, which
is gitignored; this file carries no phone numbers and no signatures.

| Run | Call id prefix | Outcome | Webhooks received |
| --- | --- | --- | --- |
| 1 | `a40559d0` | `SCHEDULED -> RINGING -> NOT_CONNECTED`, unanswered | `call_summary`, `call_status_updated` |
| 2 | `92b0b3ea` | `SCHEDULED -> INITIATED -> RINGING -> IN_PROGRESS -> COMPLETED`, answered by a human, ENGAGED, 30s | all four: `call_status_updated`, `call_recording_done`, `call_result_done`, `call_summary` |

Between them all four event types are covered. Note that `raw/` holds a mix of
both runs: run 2 overwrote the agent, create and poll files, and the webhook
counter restarted, so identify a file by the call id inside it.

## Signature verification: confirmed, no code changes needed

All six captured webhooks pass `verify_signature` in
`app/integrations/hunar/signature.py`.

| Observation | Value |
| --- | --- |
| Signing key | **The Hunar API key** |
| Signed payload | `f"{timestamp}." + raw_body_bytes`, exactly as implemented |
| `X-Hunar-Signature` | **2 comma-separated segments** on every delivery, 89 chars (two 44-char base64 SHA-256 digests) |
| Matching segment | Always the **first**; the second is a different key, so the rotation case is real |
| `X-Hunar-Timestamp` | Unix seconds |
| `Content-Length` vs body bytes | Identical, so nothing is re-encoded in transit |
| Wrong key | Correctly rejected |
| User agent | `Hunar-Voice-Agents/1.0` |

Settles the open question: live mode keys the HMAC on `HUNAR_API_KEY`, which is
what `Settings.webhook_signing_key()` already returns.

## The three findings that change other tasks

### 1. `call_status_updated` is not a per-transition feed

Run 2 moved through five statuses over ~90 seconds and delivered **exactly one**
`call_status_updated`, at the terminal transition. No webhook for
`SCHEDULED -> INITIATED`, `INITIATED -> RINGING`, or `RINGING -> IN_PROGRESS`.
Run 1 behaved the same way. The catcher was running throughout in both.

**Consequence for the live funnel (task 12): it cannot be driven by webhooks.**
Mid-call movement is only visible by polling `GET /calls/{id}/`. This promotes
`services/reconcile.py` from "repairs missed events" to "the only source of
intermediate funnel state".

### 2. `call_summary` is the complete record, but it lags by minutes

Run 2 delivery timeline, measured from `ended_at` (03:24:34Z):

| Offset | Event | Carries |
| --- | --- | --- |
| +12s | `call_status_updated` | status only, no result, no recording |
| +23s | `call_recording_done` | `recording_url` |
| +27s | `call_result_done` | `result` |
| **+372s** | `call_summary` | **status + result + recording_url, all populated** |

`call_summary` really is the combined terminal record, as documented. But **six
minutes** after the call ended. In run 1 the same event arrived at **+35s** —
because there was no result to compute. The summary evidently waits for the
maker-checker second pass, so its latency scales with whether there is a result
at all.

Two consequences:

- **Do not wait for `call_summary` to show a recruiter the outcome.**
  `call_result_done` had the same result 345 seconds earlier. The summary is the
  backstop, not the primary path.
- **Ordering is genuinely unstable across runs.** Run 1 delivered `call_summary`
  *before* `call_status_updated`; run 2 delivered it last. Both orders happen, so
  the monotonic ordering in the state machine is not a hypothetical safeguard.

Replaying run 2's four events in real delivery order through
`apply_call_update` ends in the correct state, and the trailing `call_summary`
correctly reports `changed=False` — by the time it lands it is entirely redundant.

### 3. The call detail API is eventually consistent after COMPLETED

At the moment the call reached `COMPLETED` (poll 19):

```
result        = {}
recording_url = null
```

The same call re-read minutes later:

```
result        = {"availability": "a week", "open_to_work": true, "identity_confirmed": true}
recording_url = <s3 url>
user_speech_duration = 7.46
```

The result is produced by a second pass after the call ends, and the recording is
uploaded afterwards. **Reaching a terminal status does not mean the record is
complete.** Reconciliation must re-poll terminal calls that still lack a result or
recording rather than treating terminal as finished.

This also makes the empty-result rule load-bearing rather than defensive. The real
sequence is: `call_result_done` delivers a result, then reconciliation polls and
gets `result: {}` back. Testing `update.result is not None` would overwrite the
real result with an empty dict every time. `apply_call_update` tests truthiness
instead, pinned by `test_empty_result_does_not_erase_a_real_one`.

## Webhook body shapes

Flat objects, no envelope. **Every event carries `call_id`, `agent_id`,
`request_id` and `event_type`, and nothing else is guaranteed.**

### `call_status_updated` and `call_summary` (the full shape)

On `call_summary` for a connected call, `result` and `recording_url` are
populated. On `call_status_updated` they are absent or null even at COMPLETED.

```
agent_id                 str
answered_by              str | null    "HUMAN" on the connected call
call_id                  str           <-- the id key. There is NO "id" field.
created_at               str  ISO8601 with Z
duration_minutes         float
duration_seconds         float
ended_at                 str  ISO8601 with Z
event_type               str
from_phone_number        str
lifecycle_status         str
max_retries              int           <-- response-side name, not max_retry_count
next_retry_scheduled_at  null
recording_url            str | null    (call_summary only; populated once uploaded)
request_id               str           <-- our tracking id, echoed back
result                   dict | {}     (call_summary only) -- EMPTY DICT, not null,
                                       when the call produced no result
retries_left             int
retry_count              int
retry_reason             null          <-- undocumented, not in our models
started_at               str  ISO8601 with Z
status                   str
timezone                 str
to_number                str           <-- NOT "mobile_number"
```

### `call_recording_done` — five fields only

```json
{
  "agent_id": "...",
  "call_id": "...",
  "event_type": "call_recording_done",
  "recording_url": "https://hunar-voice-prod-....s3.ap-south-1.amazonaws.com/call/recording/fde-hiring/<call_id>_0_plivo.wav",
  "request_id": "..."
}
```

A raw S3 URL. Confirms the decision to proxy recordings through the backend
rather than putting that URL in the browser.

### `call_result_done` — five fields only

```json
{
  "agent_id": "...",
  "call_id": "...",
  "event_type": "call_result_done",
  "request_id": "...",
  "result": {
    "availability": "a week",
    "identity_confirmed": true,
    "open_to_work": true
  }
}
```

The result keys are exactly the keys of the `result_schema` we sent, with the
declared types honoured: `"boolean"` produced real JSON booleans, `"string"`
produced a string. This is the maker-checker output, and it is structured — there
is no free text to parse anywhere in it.

**These two events carry no status and no `retry_count`.** The state machine
already handles that correctly: `should_apply` reads a missing `retry_count` as
the value on the record, and `result` / `recording_url` are applied additively
before the staleness gate. No change was needed.

## Fields the webhooks never carry

Confirmed on a **connected, engaged** call, so this is not an artefact of the
unanswered run:

| Field | Call detail API | Any webhook |
| --- | --- | --- |
| `engagement_status` | `ENGAGED` | **absent key** |
| `user_speech_duration` | `7.46` | **absent key** |
| `call_ended_by` | present | **absent key** |
| `redial_status` | present | **absent key** |
| `custom_data`, `system_data` | present | absent |
| `callback_config`, `retry_config`, `guardrails` | present | absent |
| `campaign_id`, `language`, `triggered_by`, `updated_at` | present | absent |
| call id | `id` | `call_id` |
| callee number | `mobile_number` | `to_number` |
| `event_type` | absent | present |
| `retry_reason` | absent | present |

So "engaged" is an API-only signal. Any funnel stage that depends on it must be
fed by reconciliation, not by the webhook stream.

Do not parse a webhook body with the `Call` model — verified, it fails on the
missing `id`.

## Call detail API: our model is correct

`app/integrations/hunar/types.py` validated against all 20 captured API responses
across both runs (call create plus 19 polls):

- **0 parse failures**
- **0 fields Hunar sent that we do not declare**
- **0 fields we declare that never appeared**

No change to `types.py` was needed.

## Agent creation

`custom_variables` came back **empty** both times, even though `introduction`
contains `{callee_name}`. `callee_name` and `mobile_number` are always in
`required_variables`, so they never become custom variables.

**Consequence for tasks 8 to 10:** the only way to get a `custom_data` key onto a
call is to put a `{placeholder}` token for it in the prompt text. There is no
separate field that declares them, so the agent builder has to generate prompts
containing the tokens it wants populated.

`result_variables` came back as the keys of the flat `result_schema`, order not
preserved.

## Corrections to earlier findings

**`from_phone_number` is available to us.** `GET /numbers/` returns `count: 0` for
this key and I previously concluded we had no outbound number. Wrong: both calls
were placed with `from_phone_number` populated automatically by Hunar (an Indian
number ending 9599). The org has a number; this key simply cannot list it.

**The guardrail floor is real and still stands:**
`400 {"message": "Minimum allowed earliest_call_time is 08:00."}`
