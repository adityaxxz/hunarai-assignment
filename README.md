# Hunar.AI — Forward Deployed Engineer assignment

The brief asked for three things: an AI hiring assistant for inbound applicants, a people-search and reachout tool for outbound candidates, and a written design for tracking attendance without smartphones.

I built the first two as **one application with two entry points into one funnel**, and wrote the third as a document. Inbound and outbound only differ in how a candidate enters the system. After that it is the same agent builder, the same dispatch, the same webhooks, the same reconciliation and the same review screen — so building them separately would have meant maintaining two call pipelines to demonstrate one.

Everything below has been run against the real Hunar API, not just against my own mocks. Where the API behaved differently from its documentation, I wrote down what it actually did and built to that.

## Links

| | |
| --- | --- |
| **App** | https://hunarai-assignment.vercel.app |
| **Attendance design (item 3)** | https://hunarai-assignment.vercel.app/attendance |
| **API docs** | https://hunar-fde-backend.onrender.com/docs |


## A 90-second walkthrough

If you only click five things, click these.

1. **[The campaign list](https://hunarai-assignment.vercel.app/campaigns)** — four
   seeded batches plus my two live tests. Each card shows where its calls actually are.
2. **[A finished screening batch](https://hunarai-assignment.vercel.app/campaigns/41)** —
   the funnel, with a call that never connected sitting apart from calls that did.
3. **[One candidate](https://hunarai-assignment.vercel.app/campaigns/41/calls/220)** —
   the rubric question by question, and a recruiter override sitting *beside* the
   decision the system computed rather than replacing it.
4. **[My real call](https://hunarai-assignment.vercel.app/campaigns/45/calls/240)** —
   I rang myself through the deployed stack. Real recording, real webhook timeline.
5. **[Sourcing](https://hunarai-assignment.vercel.app/sourcing)** — paste a job
   description, edit the query the model wrote, search, and see the consent gate.

Two more worth a look: the
**[agent panel](https://hunarai-assignment.vercel.app/requisitions/26)**, which shows
what Hunar *derived* next to what I *sent*, and a
**[reachout batch](https://hunarai-assignment.vercel.app/campaigns/44)** with the
interest and objection aggregates.

## Screenshots

**The agent panel.** One list of criteria generates the prompt, the `result_schema`
and the rubric, so they cannot drift apart. After I create the agent, the panel
shows the variables Hunar derived beside the tokens I sent — Hunar computes
`custom_variables` by scanning the prompt text, and any disagreement is a 422 at
dispatch time.

![Agent panel](docs/screenshots/agent-panel.jpg)

**The live funnel.** Eight stages, derived from Hunar's two status fields. A call
waiting on its next retry is shown as retrying with the time, not as failed.

![Live funnel](docs/screenshots/live-funnel.jpg)

**Candidate detail.** What was asked, what the candidate said, whether it passed.

![Candidate detail](docs/screenshots/candidate-detail.jpg)

## The two live tests

### 1. I called myself through the deployed system

On 6 September I created a Delivery Rider requisition, put my own number in it, and launched it — Vercel to Render to Hunar and back. I answered, spoke in full sentences, and let the agent end the call. 40 seconds, 11.4 of them me talking.

Three things that had never been exercised before all worked on the first attempt: **all four webhooks reached Render** (previously I had only ever received them through a tunnel to my laptop), the **recording proxy streamed a real 1.2 MB file from S3**, and `call_summary` arrived at **+371 seconds** — against the +372s I had measured the day before on completely different infrastructure. One second apart. That is the single strongest piece of evidence here that the reconciliation design is answering a real property of the API and not a fluke.

It also found a bug, which is the entire reason to test against reality. Every criterion came back `unknown` and I scored UNDECIDED despite answering everything correctly. `result_schema` declared those fields `"boolean"` and Hunar returned the **quoted string** `"true"`. My earlier capture had returned real JSON booleans and I had written that down as settled — one sample was not enough. `_as_bool` now accepts both shapes and nothing else, and `observed_shapes.md` records which call disproved which.

### 2. I ran a real People Data Labs search

I pointed the deployed app at live PDL with demo mode left **on**, so real profiles came back and nothing could dial. It returned real engineers at Larsen & Toubro, Thermax and Stantec — and **PDL released not a single phone number**, so all eight records fell back to a demo number and every row is badged as such.

That is exactly why contact resolution is its own pipeline stage instead of a field read. One result was located in Broomfield, Colorado on an India-filtered search; the plan gates the person's own location, so that is their employer's head office, and the row says "company office, not theirs" rather than passing it off as where they are.

### The measurement everything else rests on

Before either of those, I placed two calls on 5 September and captured every webhook. Full findings in `backend/fixtures/observed_shapes.md`. This is the timing that changed the architecture, measured from `ended_at`:

| Offset | Event | Carries |
| --- | --- | --- |
| +12s | `call_status_updated` | status only, no result, no recording |
| +23s | `call_recording_done` | `recording_url` |
| +27s | `call_result_done` | `result` |
| **+372s** | `call_summary` | status, result and recording, all populated |

All six webhooks passed signature verification with no code changes. Two undocumented behaviours here are load-bearing: `call_summary` trails the call by **six minutes** because it waits for Hunar's maker-checker second pass, and the API returns `result: {}` at the moment a call reaches COMPLETED. Terminal is not finished, so the system keeps reconciling after a call ends — and says so on screen.

## How it works

The browser never talks to Hunar. Everything goes browser → my API → Hunar. That keeps the key off the client, lets me validate every response against shapes I control, and is what makes the recording proxy possible.

- **Frontend** — Next.js App Router on Vercel, TypeScript strict, Tailwind, shadcn/ui
- **Backend** — FastAPI on Render, SQLAlchemy 2 async, Alembic, uv
- **Database** — Neon serverless Postgres, Singapore
- **Scheduling** — cron-job.org: a keep-warm ping every 10 minutes, reconciliation every minute

**The one non-obvious decision: reconciliation drives the funnel, not webhooks.**
That is a measurement, not a preference. One of my captured calls moved through five statuses in about ninety seconds and delivered **exactly one** `call_status_updated`, at the terminal transition — nothing for `SCHEDULED → INITIATED`, `INITIATED → RINGING` or `RINGING → IN_PROGRESS`. The other behaved the same way with the catcher running throughout. A webhook-driven funnel would sit completely still until each call ended. So `services/reconcile.py` polls `GET /calls/{id}/` for anything not yet terminal, and the campaign endpoint reconciles before it responds — which is why watching the page is what makes it move. Webhooks still carry the result payload and the recording URL, but they repair the record rather than drive it.

## Key decisions

The reasoning for each of these is argued in full somewhere above; this is just the scannable version.

| Decision | Why |
| --- | --- |
| One app, two entry points, one funnel | Inbound and outbound only differ in how a candidate enters. Separating them would mean two call pipelines to demonstrate one. |
| Reconciliation drives the funnel, not webhooks | Measured: a call delivered exactly one `call_status_updated`, at the terminal transition only. A webhook-driven funnel would sit still until each call ended. |
| `DEMO_MODE` swaps only the `VoiceProvider` | Everything else — webhooks, signatures, dispatch, reconciliation — runs the same code path whether or not a real call is placed. |
| Contact resolution is its own pipeline stage | PDL can silently withhold a number. A badge naming which resolver produced it beats hiding the failure. |
| Recordings are proxied, never linked | Hunar returns a raw S3 URL. Putting that in a page makes a recording of someone's phone call permanently reachable by anyone who reads the HTML. |
| The computed decision is never overwritten | An override is stored beside it, not instead of it, so what the machine concluded stays visible after a human disagrees. |
| One list of criteria generates the prompt, `result_schema`, and the rubric | One source means they cannot drift apart from each other. |
| No user identity invented | Overrides are attributed to the constant `"recruiter"` rather than a fabricated user id. An honest gap is better than a fake one. |

## Project structure

```
backend/
  app/
    routers/          one file per resource — campaigns, requisitions, candidates, calls, sourcing, webhooks, internal
    services/         the actual logic — agent_builder, campaign (guardrails + dial-window notes), evaluation,
                       reconcile, recording, contact_resolution, jd_to_query, candidate_intake
    integrations/
      hunar/           the Hunar client, webhook signature verification, and the DEMO_MODE simulator
      people_search/   fixture and PDL providers behind one interface
    models.py, schemas.py, config.py, db.py, main.py
  alembic/             migrations
  fixtures/            observed_shapes.md (what the live API actually does) + captured raw payloads
  scripts/             seed_demo.py, capture_live_call.py, webhook_catcher.py, smoke_hunar.py
  tests/

frontend/
  app/
    page.tsx                                                     landing
    requisitions/, requisitions/[id]/, requisitions/[id]/launch/  build an agent, launch a campaign
    campaigns/, campaigns/[id]/, campaigns/[id]/calls/[callId]/    the funnel and one candidate's detail
    sourcing/, sourcing/campaigns/[id]/                            JD → query → search → consent → reachout
    attendance/                                                    renders docs/ATTENDANCE_DESIGN.md at build time
  components/, lib/

docs/
  ATTENDANCE_DESIGN.md   item 3, the design document itself
  screenshots/
```

## Running it locally

```bash
cd backend
cp .env.example .env          # fill in DATABASE_URL at minimum
uv sync
uv run alembic upgrade head
uv run python scripts/seed_demo.py     # optional: wipes and seeds the demo story
uv run uvicorn app.main:app --reload

cd ../frontend
cp .env.example .env.local
npm install && npm run dev
```

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | **yes** | The only one without a working default. |
| `DEMO_MODE` | no | Defaults to `true`. `false` places real, billable calls. |
| `PUBLIC_BASE_URL` | no | Where webhooks are delivered. Defaults to Render's own URL. |
| `HUNAR_API_KEY` | for live mode | Also the key inbound webhook signatures are verified against. |
| `DEMO_WEBHOOK_SIGNING_KEY` | no | Generated per process. Set it to survive restarts. |
| `INTERNAL_API_TOKEN` | for cron | Bearer token for `/internal/*`. |
| `PEOPLE_SEARCH_PROVIDER` | no | `fixture` or `pdl`. Defaults to `fixture`. |
| `PDL_API_KEY` | for live search | Free tier is 100 records a month; each result spends one. |
| `GEMINI_API_KEY` | no | JD parsing falls back to keyword extraction without it. |
| `NEXT_PUBLIC_API_BASE_URL` | **yes** (frontend) | The only public variable. No secret ever belongs in one. |

`GET /health` reports `demo_mode` and the live people-search provider, so you can
check what is actually wired up from outside instead of taking my word for it.

## Demo mode

The Hunar trial key expires three days after issue, and you cannot dial real people
to demonstrate a voice product in a review meeting. So `DEMO_MODE` swaps the
`VoiceProvider` implementation and nothing else.

The simulator is not a set of canned responses. It **signs real webhooks with
HMAC-SHA256 and POSTs them over HTTP to my own receiver**, which verifies them
through the same path a live Hunar callback takes. The timings are compressed from
the ones I measured, with the real values in the comments. The deployed instance
runs in demo mode, so nothing you click can place a call. Setting `DEMO_MODE=false`
with a valid key makes it dial for real — that is how I ran the live test.

## What does not work, and why

**PDL's free tier withholds phone numbers.** It also substitutes the literal boolean
`true` for a gated field rather than null, so `mobile_phone: true` means "we have
one, your plan does not include it". Contact resolution is therefore a pipeline
stage that is allowed to fail, and every record carries a badge naming which
resolver produced its number. Hiding that would make the demo a lie that only breaks
in production.

**A dispatched call cannot be recalled.** Hunar offers no cancel or delete for a
scheduled call, and a campaign launched outside the calling window is *accepted and
scheduled*, not rejected. I mitigate it by validating the window before dispatch —
08:00 to 21:00, both bounds discovered by hitting them — and by stating the real
dial start time on the confirmation screen. But once Hunar accepts a call, it will
place it.

**My demo numbers are synthetic in intent and real in format.** The fixture profiles
carry numbers in a live Indian mobile range, and nothing in the code stops one
reaching a real dialler. `PEOPLE_SEARCH_PROVIDER=fixture` reads as safe while
`DEMO_MODE=false` is the switch that actually matters, and that combination once
scheduled a call I could not cancel. A dispatch-time guard refusing fixture data in
live mode is the obvious fix and I have not built it.

**No authentication.** Recruiter overrides are attributed to the constant
`"recruiter"` in `audit_log` rather than to a user id I would have had to invent.
Identity is the largest single gap between this and something shippable.

**No WhatsApp transport.** The `NotificationChannel` interface exists with a logged
implementation; the Twilio adapter does not. A live integration is invisible to a
reviewer anyway — Twilio's sandbox needs each recipient to send a join code and
Meta's test number allows five — so a rendered outbox showing template, recipient,
trigger and timestamp is the better artifact.

**Frontend types are hand-maintained**, not generated from the OpenAPI schema. A
generated client is a large file nobody reviews plus a regeneration step that goes
stale silently. The cost is real and I state it in the file: rename a backend field
and the frontend still typechecks, still builds, and is `undefined` at runtime.

**The recording proxy has no Range support**, so you cannot seek within a long
recording. I proxy recordings rather than link them because Hunar returns a raw S3
URL, and putting that in a page makes a recording of someone's phone call
permanently reachable by anyone who reads the HTML.

**Nothing expires.** Recordings, extracted results and phone numbers persist until
the database is dropped. A real deployment needs a retention policy and a deletion
path before it touches anyone's data; the seed script's wipe is a development
convenience, not one.

## What I would do next

- **Authentication and real actor identity**, so `audit_log` records *who* overrode
  a decision instead of merely that someone did.
- **Range support on the recording proxy**, so a recruiter can skip to the part of a
  two-minute call they care about.
- **A dispatch guard** that refuses fixture data in live mode, and a pre-dispatch
  confirmation naming every number about to be called.
- **`reconcile_stopped_reason` in the funnel**, not only on the candidate detail.
  The call list already separates "still settling" from "we gave up"; the stage
  counters do not.
- **Interview booking and the messaging outbox.** Both are modelled in the schema
  and neither is built — they were the first things I cut once the live capture
  showed reconciliation needed more work than I had planned for.
