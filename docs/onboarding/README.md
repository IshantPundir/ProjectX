# Onboarding — new engineer ramp

The fast path to being productive in ProjectX. Read the CLAUDE.md files first — they
are the authoritative, always-current source of truth; everything else is summary.

> Refreshed 2026-06-03.

## 1. What the product is (15 min)

- Root [`CLAUDE.md`](../../CLAUDE.md) — product, monorepo layout, two-tier philosophy,
  the **Phase Status table** (what's built), hard rules, enterprise standards.
- [`docs/tech-stack.md`](../tech-stack.md) — cross-cutting stack at a glance.
- [`docs/product/product-overview-simple.md`](../product/product-overview-simple.md) —
  plain-language product flow (read the 2026-06 corrections banner at the top).

## 2. The repo (read the CLAUDE.md for the area you'll touch)

| Surface | Path | Start here |
|---|---|---|
| Backend (FastAPI modular monolith) | `backend/nexus/` | [`backend/nexus/CLAUDE.md`](../../backend/nexus/CLAUDE.md) |
| Recruiter dashboard (BinQle) | `frontend/app/` | [`frontend/app/CLAUDE.md`](../../frontend/app/CLAUDE.md) |
| Candidate interview surface | `frontend/session/` | [`frontend/session/CLAUDE.md`](../../frontend/session/CLAUDE.md) |
| Internal operator console | `frontend/admin/` | [`frontend/admin/CLAUDE.md`](../../frontend/admin/CLAUDE.md) |

The backend is a **single image** that runs as several workers: `nexus` (API),
`nexus-worker` (Dramatiq), `nexus-engine` (live interview engine), and
`nexus-vision-worker` (reel render + vision proctoring).

## 3. Local dev (30 min)

```bash
# Postgres + auth (local Supabase): Postgres 54322, Studio 54323, Inbucket 54324
supabase start

# Backend (from backend/nexus/)
cp .env.example .env       # fill in keys; OpenAI/Deepgram/Sarvam/LiveKit/R2
docker compose up --build  # api + workers + redis
docker compose run nexus alembic upgrade head
docker compose run nexus pytest

# Recruiter app (frontend/app/, :3000) · Admin (frontend/admin/, :3001)
# Candidate session (frontend/session/, :3002)
npm install && npm run dev
```

## 4. The non-obvious load-bearing concepts

- **RLS is enforced at runtime** via the `nexus_app` role (`NOBYPASSRLS`). Every request
  runs `SET LOCAL ROLE nexus_app` + `SET LOCAL app.current_tenant`. The app aborts at
  boot if any tenant-scoped table is missing its policy pair. See backend CLAUDE.md →
  "RLS runtime role".
- **Provider-agnostic seams:** auth (`app/modules/auth/`), AI (`app/ai/`), storage
  (`app/storage/`), notifications. Business logic never imports a vendor SDK directly.
- **The interview engine** is three tiers — triage ∥ brain → mouth (`gpt-5.4-mini`).
  The mouth never sees the rubric (no-leak by construction). See backend CLAUDE.md →
  Phase 3D.engine and `backend/nexus/docs/onboarding/engine-redesign-phase-2-e2e.md`
  for the manual live E2E checklist.
- **Candidates are not Supabase users** — single-use JWT in the URL, atomic-consume on
  `/start`. The candidate app must never depend on `@supabase/*`; the recruiter app must
  never depend on `livekit-*`.
- **Borderline candidates are always human-held** — never auto-advanced or auto-rejected.

## 5. Operating docs

- Security: [`docs/security/`](../security/) — threat model (STRIDE per boundary), key
  rotation runbooks, vision-proctoring DPIA.
- DR: [`docs/dr/`](../dr/) — restore-drill runbook + logs.
- Incidents: [`docs/incidents/`](../incidents/) — blameless post-mortems.
- History: `docs/superpowers/specs/` + `plans/` — dated design records (the most-current
  spec per feature is the one with the latest date for that feature).
