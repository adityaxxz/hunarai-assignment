# Hunar.AI FDE Assignment

An AI hiring assistant (inbound applicants) and a people-search reachout tool
(outbound sourced leads), built as **one funnel** on the Hunar Voice API, plus a
written design for offline attendance tracking.

- **Live demo:** _to be added_
- **Attendance design:** `docs/ATTENDANCE_DESIGN.md`

## Stack

| Layer    | Choice                                                        |
| -------- | ------------------------------------------------------------- |
| Backend  | Python 3.11, FastAPI, SQLAlchemy 2 (async), Alembic, uv        |
| Frontend | Next.js (App Router), TypeScript strict, Tailwind, shadcn/ui   |
| Database | Neon serverless Postgres (Singapore)                          |
| Hosting  | Render (backend), Vercel (frontend)                           |

## Running locally

Backend:

```bash
cd backend
cp .env.example .env      # fill in values
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Backend serves on `http://localhost:8000`, frontend on `http://localhost:3000`.
`GET /health` is the liveness check.

## Demo mode

`DEMO_MODE=true` (the default) swaps the Hunar API client for a simulator, so the
deployed app stays fully demonstrable after the trial API key expires and no real
phone calls are placed. Set it to `false` to dial for real.

## Deploying

Backend on Render (free, Singapore), frontend on Vercel. Both read from this
monorepo, so each needs its root directory set.

### 1. Backend — Render

`render.yaml` at the repo root is a Blueprint, chosen over dashboard-only
configuration so the deploy is reviewable in the diff and reproducible if the
service is recreated. Nothing secret is in it.

1. Render dashboard → **New → Blueprint**, point it at this repo.
2. It reads `render.yaml` and prompts for the four `sync: false` variables below.
3. Deploy. The first build takes a few minutes; migrations run at startup.

`rootDir: backend` is already set, so you do not configure it by hand.

**Environment variables**

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | **yes** | Neon pooled string, pasted exactly as Neon prints it. `app/db.py` rewrites `postgresql://` to `postgresql+asyncpg` and translates `sslmode`, so it needs no editing. |
| `FRONTEND_ORIGIN` | **yes** | The Vercel production URL, no trailing slash. CORS compares it exactly. |
| `DEMO_WEBHOOK_SIGNING_KEY` | **yes** | Any long random string. Set it explicitly — the generated default is per-process, so webhooks signed before a restart fail verification after one. |
| `INTERNAL_API_TOKEN` | **yes** | Bearer token for the `/internal/*` cron endpoints. |
| `DEMO_MODE` | defaults `true` | `true` keeps the app on the simulator, which is what makes the link work after the trial key expires. `false` places real, billable calls. |
| `PUBLIC_BASE_URL` | **leave unset** | Falls back to `RENDER_EXTERNAL_URL`, which Render injects with this service's own URL. The simulator POSTs webhooks here; a wrong value means demo mode delivers nothing, silently. Set it only behind a custom domain. |
| `HUNAR_API_KEY` | no | Not needed in demo mode. Leaving it unset means the deployed app cannot place a real call by accident. |
| `PEOPLE_SEARCH_PROVIDER` | defaults `fixture` | |
| `NOTIFICATION_CHANNEL` | defaults `logged` | |
| `PDL_API_KEY`, `GEMINI_API_KEY`, `TWILIO_*` | no | Empty is fine; those features degrade rather than fail. |

**Migrations run at application startup**, not as a pre-deploy step, because
Render's pre-deploy command is a paid feature. `app/startup.py` documents the
trade-off: it races if several instances boot at once, which is safe here because
free tier runs exactly one. A failed migration is deliberately fatal.

**The start command pins one uvicorn worker** and must stay that way. The
simulator holds demo calls in memory and schedules webhook deliveries as asyncio
tasks in-process, and the webhook rate-limit window is module-level state. A
second worker serves requests from a process that has never heard of half the
calls, which looks like flaky infrastructure rather than a config mistake.

### 2. Frontend — Vercel

1. Vercel → **Add New → Project**, import this repo.
2. Set **Root Directory** to `frontend`. Without it the build fails at install:
   there is no `package.json` at the repo root.
3. Framework preset auto-detects as Next.js. Leave the build command alone.
4. Add one environment variable:
   `NEXT_PUBLIC_API_BASE_URL` = your Render URL, e.g.
   `https://hunar-fde-backend.onrender.com` — **no trailing slash**.
5. Deploy, then go back to Render and set `FRONTEND_ORIGIN` to the Vercel URL.

### 3. Cron jobs (cron-job.org)

Render's free tier has no scheduler, so both jobs run externally. Two entries:

| Every | Method | URL | Header |
| --- | --- | --- | --- |
| 10 min | GET | `https://<render-url>/internal/health-ping` | none |
| 1 min | POST | `https://<render-url>/internal/reconcile` | `Authorization: Bearer <INTERNAL_API_TOKEN>` |

The ping exists only to stop the 15-minute spin-down, and deliberately touches no
database. The reconcile job returns counts (`examined`, `updated`, `gave_up`,
`errors`, `quota_exhausted`) so the cron history shows whether anything is
actually moving rather than just a bare 200. `quota_exhausted: true` means the
Hunar account is out of calling minutes and every call will fail until that is
resolved.

One minute is the finest granularity cron-job.org offers and is far too coarse
for a funnel someone is watching, which is why the campaign views also reconcile
on demand. The cron is the backstop for when nobody is looking.

### 4. Order of operations

The two services reference each other, so one value is unknown on each first
pass. Deploy the backend, deploy the frontend with the backend's URL, then update
`FRONTEND_ORIGIN` on Render and let it redeploy.

### Things that will waste your time if you skip them

- **Vercel preview deployments get a different origin every time**
  (`your-app-git-branch-you.vercel.app`). They will fail CORS against a
  `FRONTEND_ORIGIN` set to the production URL, and the browser reports it as a
  network error rather than a CORS error. Either test on the production URL, or
  add the preview origin to `FRONTEND_ORIGIN` when you need one.
- **No trailing slashes** on `FRONTEND_ORIGIN` or `NEXT_PUBLIC_API_BASE_URL`.
- The first request after 15 minutes idle takes 30-60 seconds. The UI says so
  after 8 seconds rather than spinning silently.

## Known constraints

**Frontend types are hand-maintained.** `frontend/lib/api/types.ts` mirrors the
backend response schemas by hand rather than being generated from the OpenAPI
document. A generated client is a large file nobody reviews and adds a
regeneration step that goes stale silently. The cost is real: a backend field
rename will typecheck, build, and then be `undefined` at runtime. Changing a
backend response schema means editing that file in the same commit.

**Cold starts.** The backend runs on Render's free tier, which sleeps after 15
minutes idle and takes 30-60 seconds to wake. The frontend request timeout is
75 seconds and the UI says it is waking the backend after 8, rather than showing
a spinner that looks broken. A cron ping every 10 minutes keeps it warm in
practice.

**Connection strings are normalised.** Neon and Render print `postgresql://`,
which SQLAlchemy resolves to the synchronous psycopg2 driver. `app/db.py`
rewrites the scheme to `postgresql+asyncpg` and translates libpq's `sslmode` to
asyncpg's `ssl`, so the provider's string can be pasted unedited.
