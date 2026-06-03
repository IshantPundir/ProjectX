# ProjectX — Recruiter Dashboard (frontend/app)

Next.js application for the ProjectX AI video interview platform. This app serves the
**recruiter dashboard ONLY** (rebranded **BinQle**). The candidate interview surface was
extracted to a separate app at `frontend/session/` on 2026-05-01 — do not add candidate
routes, LiveKit, or Supabase-free flows here.

## Tech Stack

- **Next.js 16.2.x** (App Router)
- **React 19** / **TypeScript** (strict mode)
- **Tailwind CSS v4** (design tokens in `app/theme.css`, mapped in `app/globals.css`)
- **`components/px/`** — in-house, hand-rolled primitives on plain React (NO shadcn/ui, NO `@base-ui-components/react`)
- **TanStack Query v5** (server state), **Zustand** (minimal client state), **React Hook Form + Zod** (forms)
- **Supabase Auth** (email/password) for session management only — all data goes through Nexus

## Prerequisites

- Docker & Docker Compose (recommended)
- Or: Node.js 22+ and npm

## Quick Start

### With Docker

```bash
# Production-like build
docker build -t projectx-frontend .
docker run -p 3000:3000 projectx-frontend

# Or via docker-compose — development (hot reload)
docker compose --profile dev up

# Or via docker-compose — production
docker compose --profile prod up --build
```

### Without Docker

```bash
npm install
npm run dev
```

Open **http://localhost:3000**.

## Project Structure

```
frontend/app/
├── app/                          # Next.js App Router
│   ├── (auth)/                   # Login, invite acceptance (email/password)
│   ├── onboarding/               # First-run wizard
│   └── (dashboard)/              # Recruiter dashboard: jobs, candidates, tracker,
│                                 #   pipeline, questions, reports/[sessionId], settings/{team,integrations,org-units}
├── components/
│   ├── px/                       # In-house hand-rolled primitives (no shadcn, no base-ui)
│   └── dashboard/                # Composite components: jd-panels, pipeline, question-bank,
│                                 #   candidates, tracker, reports (+ reports/theater), org-units, job
│   └── settings/integrations/    # ATS (Ceipal) connection UI
├── lib/
│   ├── brand.ts                  # Name/logo/tagline + active theme (single source — "BinQle")
│   ├── api/                      # Typed API namespaces (all calls to Nexus)
│   ├── auth/                     # Token retrieval + global 401 sink
│   ├── hooks/                    # 60+ TanStack Query hooks
│   └── supabase/                 # @supabase/ssr clients (auth only)
├── stores/                       # Zustand (job-edit only)
├── Dockerfile                    # Multi-stage standalone build
├── docker-compose.yml            # Dev & prod profiles
└── proxy.ts                      # Route protection + auth checks (this app's middleware file)
```

## Surface

| Surface       | Audience                                          | Access                         |
|---------------|---------------------------------------------------|--------------------------------|
| **Dashboard** | Recruiters, Hiring Managers, Interviewers, Admins | Supabase Auth (email/password) |

Candidates use the separate `frontend/session/` app (JWT invite link, no login).

## Available Commands

```bash
npm run dev          # Start dev server (localhost:3000)
npm run build        # Production build
npm run start        # Start production server
npm run lint         # ESLint — must pass with zero errors
npm run type-check   # tsc --noEmit — must pass with zero errors
npm run test         # Vitest unit tests
```

## Docker

The Dockerfile uses a multi-stage build with `output: "standalone"` for minimal production images:

1. **deps** — installs `node_modules`
2. **builder** — runs `next build`, produces standalone output
3. **runner** — copies only standalone artifacts, runs as non-root user (~197MB final image)

Build-time environment variables (e.g. `NEXT_PUBLIC_API_URL`) are passed via `--build-arg`:

```bash
docker build --build-arg NEXT_PUBLIC_API_URL=https://api.example.com -t projectx-frontend .
```

## Environment Variables

| Variable                | Description                     | Client-side |
|-------------------------|---------------------------------|-------------|
| `NEXT_PUBLIC_API_URL`   | Nexus backend URL               | Yes         |
| `NEXT_PUBLIC_LIVEKIT_URL` | LiveKit server URL            | Yes         |

Secrets (API keys, auth tokens) are **never** exposed via `NEXT_PUBLIC_*` — they stay in the backend.

## Architecture Notes

- **All API calls go through Nexus** (FastAPI backend). The frontend never queries Supabase directly for data.
- **Supabase client** is used only for Auth (session management, SSO redirects) on the dashboard surface.
- **Candidates** have no account — they enter via a JWT-signed link and verify with OTP.
- **State management**: TanStack Query for server state, Zustand for client-side UI state, React Hook Form for forms.

## Deployment

| Target     | Platform                       |
|------------|--------------------------------|
| MVP        | Railway (auto-deploy)          |
| Enterprise | AWS ECS Fargate + CloudFront   |
