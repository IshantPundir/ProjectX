# ProjectX — Frontend

Next.js application for the ProjectX AI video interview platform. Serves two distinct surfaces: a **recruiter dashboard** and a **candidate interview UI**.

## Tech Stack

- **Next.js 16** (App Router)
- **React 19** / **TypeScript** (strict mode)
- **Tailwind CSS v4**
- **shadcn/ui** (component primitives)

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
│   ├── (dashboard)/              # Recruiter/admin dashboard surface
│   ├── (interview)/              # Candidate interview surface
│   └── (auth)/                   # Login, SSO, invite acceptance
├── components/
│   ├── ui/                       # shadcn/ui primitives (auto-generated)
│   ├── dashboard/                # Dashboard composite components
│   ├── interview/                # Candidate session components
│   ├── shared/                   # Shared across both surfaces
│   └── copilot/                  # AI Copilot panel components
├── lib/
│   ├── api/                      # Typed API client (all calls to Nexus)
│   ├── auth/                     # Auth helpers, token management
│   ├── hooks/                    # Custom React hooks
│   └── utils/                    # Pure utility functions
├── stores/                       # Zustand stores
├── types/                        # Shared TypeScript types
├── Dockerfile                    # Multi-stage standalone build
├── docker-compose.yml            # Dev & prod profiles
└── middleware.ts                 # Route protection, auth checks
```

## Two Surfaces

| Surface             | Audience                                | Access                          |
|---------------------|------------------------------------------|---------------------------------|
| **Dashboard**       | Recruiters, Hiring Managers, Admins      | Supabase Auth (SSO / magic link)|
| **Candidate Interview** | Job candidates                       | JWT invite link (no login)      |

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
