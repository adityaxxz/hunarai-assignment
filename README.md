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

## Known constraints

_Filled in as the build progresses._
