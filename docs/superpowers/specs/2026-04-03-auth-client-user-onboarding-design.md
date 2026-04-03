# Auth + Client + User Onboarding — Design Spec

**Status:** Approved  
**Date:** 2026-04-03  
**Source of truth:** [Notion spec](https://www.notion.so/Auth-Client-User-onboarding-3367984a452a806c8097c9d7cf084f18)

---

## 1. Overview

Three auth/onboarding flows sharing the same infrastructure:

1. **Pipeline A: Product Owner → Company Admin** — provision a new client from the admin panel, send invite, Company Admin creates account via invite link
2. **Pipeline B: Company Admin → Team Members** — Company Admin invites Recruiters, HMs, Interviewers, Observers from the client dashboard
3. **Admin Panel Auth** — ProjectX team members use email/password + manual `is_projectx_admin` flag

All three flows share: the `user_invites` table, the Supabase Auth Custom Access Token Hook, RLS enforcement via `SET LOCAL app.current_tenant`, and `@supabase/ssr` middleware on both frontends.

---

## 2. Architecture Decisions

### Two Surfaces, One Supabase Project

| Surface | URL | Port | Audience | Auth |
|---|---|---|---|---|
| Client dashboard | `app.projectx.com` | 3000 | Enterprise client teams | Email/password via invite |
| Admin panel | `admin.projectx.com` | 3001 | ProjectX internal team | Email/password + `is_projectx_admin` |

Both use the same Supabase project — same database, same auth instance, same hook.

### MVP: Email/Password (Not OAuth)

Microsoft OAuth is dropped for MVP. Both surfaces use Supabase email/password. Rationale: no Azure app registration, no redirect dance, no sessionStorage juggling. OAuth is additive post-MVP via Supabase identity linking — zero rework to hook, endpoints, or JWT structure.

**Required Supabase config:** `enable_confirmations = false` — the invite token is the verification mechanism. A second confirmation email breaks the flow.

### ES256 JWT Signing

Both local (Supabase CLI v2.71+) and production use ES256 asymmetric signing. FastAPI verifies via JWKS endpoint using `PyJWKClient`. No HS256 branching, no `SUPABASE_JWT_SECRET`.

### JWT Claims: Only Slow-Changing Identity Data

The Auth Hook injects three custom claims: `tenant_id`, `app_role`, `is_projectx_admin`. Mutable state like `onboarding_complete` is NOT in the JWT — it goes stale until token expires. Onboarding status is checked via `GET /api/auth/me` at the layout level using `React.cache()` for render-pass deduplication.

### Frontend Auth: Middleware-First with `@supabase/ssr`

- `createServerClient` in middleware refreshes session cookies and validates via `getUser()`
- Custom JWT claims (`tenant_id`, `app_role`, `is_projectx_admin`) read by decoding the access token with `jwt-decode` — `getUser()` does not return custom claims
- `createBrowserClient` singleton for interactive auth (`signUp`, `signInWithPassword`)
- Middleware stays fast: checks JWT claims only, no `onboarding_complete` check

### Invite Verification via Backend Endpoint

`GET /api/auth/verify-invite` accepts raw token, hashes it, returns `{ email, role, company_name }` or 401. Catches expired/used invites before the user sees the signup form. Shows "You've been invited to join Accenture as a Recruiter" — better UX than including email in the URL.

### Notifications Dry-Run Mode

`notifications_dry_run: bool = True` in `config.py`. When true, `send_email()` logs the full email body and invite URL to stdout. Enables testing Pipeline A end-to-end without Resend credentials.

---

## 3. Database Schema

Three tables created in order due to FK dependencies. SQL from Notion spec verified — no changes needed.

```
public.companies  ──── tenant root (no FKs)
     │
     ├── public.users ──── one row per client team member
     │
     └── public.user_invites ──── invite tracking (FKs to both)
                │
                └── superseded_by ──── self-referencing FK
```

### Migration Placement

| What | Where | Why |
|---|---|---|
| 3 tables + trigger + RLS | `backend/supabase/migrations/<ts>_create_auth_tables.sql` | Needs `supabase_auth_admin` for grants, runs via `supabase db reset` |
| Auth hook function + grants | `backend/supabase/migrations/<ts>_auth_hook.sql` | Same — `supabase_auth_admin` exists in local Supabase Docker |
| Hook registration | `backend/supabase/config.toml` `[auth.hook.custom_access_token]` | Activates automatically on `supabase start` |
| Production deployment copy | `scripts/supabase_hook.sql` | Run manually against hosted Supabase, register in Dashboard |

Alembic (`backend/nexus/migrations/`) is reserved for application tables that don't require `supabase_auth_admin`.

### Auth Hook — Evaluation Order

1. **`is_projectx_admin` check** — early return with admin flag, empty tenant/role
2. **Existing user** — `public.users` row found by `auth_user_id` → inject `tenant_id` + `app_role`
3. **Token refresh with no user** — `authentication_method = 'token_refresh'`, skip invite lookup, return empty claims
4. **Pending invite** — `user_invites` row found by email + `status = 'pending'` + not expired → inject invite's `tenant_id` + `role`
5. **No match** — return empty claims (`tenant_id = ""`, `app_role = ""`). Frontend detects empty claims and routes to post-signup endpoint.

`EXCEPTION WHEN OTHERS` catches all errors and returns safe defaults — never blocks login.

### RLS Policies

Every tenant-scoped table has:
- `tenant_isolation`: `USING (tenant_id = (SELECT current_setting('app.current_tenant', true)::UUID))`
- `service_bypass`: `USING ((SELECT current_setting('app.bypass_rls', true)) = 'true')` + `WITH CHECK`

`(SELECT ...)` wrapper causes initPlan — evaluates once per statement, not per row.

---

## 4. Backend Endpoints

### Phase 1 — Auth Infrastructure (no new endpoints)

| Change | File | What |
|---|---|---|
| Config fields | `config.py` | `supabase_url`, `supabase_anon_key`, `supabase_jwks_url`, `notifications_dry_run` |
| TokenPayload | `auth/schemas.py` | `sub`, `tenant_id`, `app_role`, `email`, `is_projectx_admin`, `exp` |
| JWKS verification | `auth/service.py` | `PyJWKClient` + ES256, module-level singleton |
| Middleware update | `middleware/auth.py` | Read `app_role` (not `role`), attach `is_projectx_admin` to `request.state` |
| Bypass session | `database.py` | `get_bypass_session()` — `SET LOCAL app.bypass_rls = 'true'` |
| Admin guard | `auth/service.py` | `require_projectx_admin()` dependency |

### Phase 2 — Pipeline A Spine

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/auth/verify-invite?token=<raw>` | Public | Hash token → return `{ email, role, company_name }` or 401 |
| `POST` | `/api/auth/complete-invite` | Bearer JWT | Atomic claim (UPDATE-WHERE-RETURNING) + INSERT users → `{ redirect_to }` |
| `GET` | `/api/auth/me` | Bearer JWT | User profile + company info (incl. `onboarding_complete`) |
| `POST` | `/api/admin/provision-client` | `require_projectx_admin` | Create company + invite + send email |
| `GET` | `/api/admin/clients` | `require_projectx_admin` | List companies + invite statuses |

### Phase 3 — Pipeline B

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/settings/team/invite` | `require_roles("Company Admin")` | Create invite + send email |
| `GET` | `/api/settings/team/members` | `require_roles("Company Admin")` | List users + pending invites |
| `POST` | `/api/settings/team/resend/{invite_id}` | `require_roles("Company Admin")` | Supersede + resend |
| `POST` | `/api/settings/team/revoke/{invite_id}` | `require_roles("Company Admin")` | Revoke pending invite |
| `POST` | `/api/settings/team/deactivate/{user_id}` | `require_roles("Company Admin")` | Set `is_active = false` on accepted user |

### Phase 4 — Onboarding

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| `PUT` | `/api/onboarding/company-profile` | `require_roles("Company Admin")` | Update company details |
| `POST` | `/api/onboarding/complete` | `require_roles("Company Admin")` | Set `onboarding_complete = true` |

### Phase 5 — Scheduled Reminders

| Type | Trigger | What |
|---|---|---|
| Dramatiq task | 48h after invite created | Reminder email if still `pending` |
| Dramatiq task | 24h before `expires_at` | Expiring-soon email if still `pending` |

### Two Database Session Types

- **`get_tenant_session(request)`** — most routes. Sets `SET LOCAL app.current_tenant = :tid` from JWT.
- **`get_bypass_session()`** — admin routes, `complete-invite`, onboarding completion. Sets `SET LOCAL app.bypass_rls = 'true'`. Never used on endpoints reachable by regular client users.

---

## 5. Notifications

### Two Email Pathways

| Pathway | Owner | Provider | Use |
|---|---|---|---|
| Auth emails | Supabase Auth service | Default mailer (Mailpit locally) | Admin panel signup confirmation, password reset |
| Transactional emails | FastAPI `notifications` module | Resend API (dry-run locally) | All invite emails, candidate invites, reports |

### Provider-Agnostic Architecture

`EmailProvider` protocol with `ResendProvider` at MVP. Business logic calls `send_email()` only — never imports Resend directly. Swap to SES by writing `SESProvider` and updating one config value.

### Dry-Run Mode

When `notifications_dry_run = True`: `send_email()` logs full email body + invite URL to stdout. Copy the invite URL from terminal to test Pipeline A without Resend credentials.

### Email Templates (Phase 2 MVP)

- `company_admin_invite.html` — "You've been invited to set up {company} on ProjectX"
- `team_invite.html` — "You've been invited to join {company} on ProjectX as {role}" (Phase 3)

---

## 6. Frontend — Client Dashboard (`frontend/app/`)

### File Structure

```
frontend/app/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx               # Email/password sign-in (returning users)
│   │   ├── invite/page.tsx              # Invite claim: verify → signup → complete
│   │   └── layout.tsx                   # Centered card layout, redirect if authed
│   ├── (dashboard)/
│   │   ├── layout.tsx                   # Sidebar + header, GET /api/auth/me check
│   │   │                                #   Company Admin + !onboarding_complete → /onboarding
│   │   ├── page.tsx                     # Dashboard home
│   │   └── settings/
│   │       └── team/page.tsx            # Phase 3: team management
│   ├── onboarding/
│   │   ├── page.tsx                     # Phase 2: placeholder → Phase 4: wizard
│   │   └── layout.tsx                   # Minimal layout, no sidebar
│   ├── layout.tsx                       # Root: fonts, globals
│   └── globals.css
├── lib/
│   ├── supabase/
│   │   ├── client.ts                    # createBrowserClient singleton
│   │   └── server.ts                    # createServerClient factory
│   └── api/
│       └── client.ts                    # Typed fetch wrapper → Nexus
├── middleware.ts                         # Session refresh + route protection
└── package.json
```

### Middleware Logic

```
1. Create Supabase server client (cookie handlers)
2. supabase.auth.getUser() — validate session, refresh token
3. Decode access token with jwtDecode() for custom claims
4. Route checks:
   /invite, /login        → public, pass through
   /(dashboard)/*         → require tenant_id + app_role → else /login
   /onboarding            → require tenant_id → else /login
```

Middleware does NOT check `onboarding_complete` — that lives in `(dashboard)/layout.tsx`.

### Invite Claim Flow (`/invite?token=...`)

1. Page loads → `GET /api/auth/verify-invite` with token
2. If invalid/expired → show error screen with clear message
3. If valid → show "Set up your account" form: company name, role, locked email, password field
4. On submit → `supabase.auth.signUp({ email, password })`
5. Hook enriches JWT with `tenant_id` + `app_role`
6. `POST /api/auth/complete-invite` with `{ raw_token }` + Bearer JWT
7. Response `{ redirect_to }` → navigate to `/onboarding` (Company Admin) or `/(dashboard)` (team member)

### Onboarding Routing

- `POST /api/auth/complete-invite` returns `{ redirect_to: "/onboarding" | "/(dashboard)" }` — Company Admin always goes to `/onboarding`, team members to `/(dashboard)`
- `(dashboard)/layout.tsx` calls `GET /api/auth/me` (wrapped in `React.cache()`) — if Company Admin + `!onboarding_complete`, redirect to `/onboarding`
- After wizard completion, navigate to `/(dashboard)` — `GET /api/auth/me` returns `onboarding_complete: true`

### Login (`/login`)

- Email/password form for returning users
- `supabase.auth.signInWithPassword({ email, password })`
- No "Sign up" link — users can only join via invite
- On success → `/(dashboard)`

### Design

- **Green primary** (`#16a34a`) — visually distinct from admin panel's dark neutral
- Desktop-first (1280px min per CLAUDE.md)
- No dark mode in MVP

---

## 7. Frontend — Admin Panel (`frontend/admin/`)

### File Structure

```
frontend/admin/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx               # Email/password sign-in
│   │   ├── signup/page.tsx              # Create account (requires manual approval)
│   │   └── layout.tsx
│   ├── (admin)/
│   │   ├── layout.tsx                   # Minimal sidebar
│   │   ├── page.tsx                     # Redirect to /dashboard
│   │   └── dashboard/
│   │       ├── page.tsx                 # Client list table
│   │       └── provision/page.tsx       # Provision form
│   ├── pending-approval/page.tsx        # Shown when !is_projectx_admin
│   ├── layout.tsx
│   └── globals.css
├── lib/
│   ├── supabase/
│   │   ├── client.ts
│   │   └── server.ts
│   └── api/
│       └── client.ts
├── middleware.ts
└── package.json
```

### Middleware Logic

```
1. Create Supabase server client (cookie handlers)
2. supabase.auth.getUser() — validate session
3. Decode access token with jwtDecode() for is_projectx_admin
4. Route checks:
   /login, /signup         → public, pass through
   /pending-approval       → require user (any), pass through
   /(admin)/*              → require is_projectx_admin = true
                              no user → /login
                              user but !is_projectx_admin → /pending-approval
```

### Pages

- **`/login`** — email/password, link to `/signup`
- **`/signup`** — email/password, note "requires manual approval"
- **`/pending-approval`** — "Pending Approval" message + sign out link (per Notion spec: minimal)
- **`/dashboard`** — table of clients: company name, admin email, plan, invite status, created date. "+ Provision Client" button
- **`/dashboard/provision`** — form: company name, domain, industry, plan dropdown, admin email. "Provision & Send Invite" button

### Design

- **Dark neutral primary** (`#0f172a`) — visually distinct from client dashboard
- Internal tool aesthetic — minimal, functional

---

## 8. Dependencies to Install

### Backend (`backend/nexus/`)

Already in `pyproject.toml`:
- `PyJWT[crypto]` — JWKS verification
- `httpx` — HTTP client

To add:
- `resend` — email provider (MVP)
- `jinja2` — email template rendering

### Frontend (both `frontend/app/` and `frontend/admin/`)

```bash
npm install @supabase/supabase-js @supabase/ssr jwt-decode
```

Phase 3+ will add: React Hook Form, Zod, shadcn/ui components.

---

## 9. Environment Variables

### `backend/nexus/.env`

```
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_ANON_KEY=sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH
SUPABASE_SERVICE_ROLE_KEY=sb_secret_N7UND0UgjKTVK-Uodkm0Hg_xSvEMPvz
SUPABASE_JWKS_URL=http://127.0.0.1:54321/auth/v1/.well-known/jwks.json
NOTIFICATIONS_DRY_RUN=true
```

### `frontend/app/.env.local` and `frontend/admin/.env.local`

```
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

---

## 10. Phase Breakdown

### Phase 0 — Supabase CLI Setup (COMPLETE)

- [x] Local Supabase running (`supabase start`)
- [x] JWKS endpoint returns EC P-256 key
- [x] `supabase_auth_admin` role exists
- [x] Email confirmations disabled
- [x] Mailpit accessible at :54324
- [x] Studio accessible at :54323

### Phase 1 — Database + Auth Overhaul

**Backend:**
- Supabase migration: `companies`, `users`, `user_invites` + trigger + RLS
- Supabase migration: auth hook function + grants
- `config.toml`: uncomment + configure `[auth.hook.custom_access_token]`
- `config.toml`: add `http://127.0.0.1:3001` to `additional_redirect_urls`
- `config.py`: add `supabase_url`, `supabase_anon_key`, `supabase_jwks_url`, `notifications_dry_run`
- `auth/schemas.py`: updated `TokenPayload`
- `auth/service.py`: JWKS/ES256 via `PyJWKClient`, `require_projectx_admin()`
- `middleware/auth.py`: read `app_role`, attach `is_projectx_admin`
- `database.py`: `get_bypass_session()`

**Acceptance:**
- `supabase db reset` runs migrations without error
- All 3 tables visible in Studio with RLS enabled
- XOR constraint rejects invalid invite origins
- Hook registered, enriches JWT with custom claims on signup
- `verify_access_token()` decodes ES256 token via JWKS
- `alembic upgrade head` still succeeds (no conflicts with Supabase migrations)

### Phase 2 — Pipeline A Spine (end-to-end testable)

**Backend:**
- `admin` module: `POST /api/admin/provision-client`, `GET /api/admin/clients`
- `auth` module: `GET /api/auth/verify-invite`, `POST /api/auth/complete-invite`, `GET /api/auth/me`
- `notifications/service.py`: `EmailProvider` protocol, `ResendProvider`, dry-run mode
- Email template: `company_admin_invite.html`
- `scripts/supabase_hook.sql`: copy with header comment for production deployment

**Frontend — Admin Panel:**
- `lib/supabase/` (client.ts, server.ts), `lib/api/client.ts`
- `middleware.ts` (`is_projectx_admin` check + `jwt-decode`)
- Pages: `/login`, `/signup`, `/pending-approval`, `/dashboard`, `/dashboard/provision`

**Frontend — Client Dashboard:**
- `lib/supabase/` (client.ts, server.ts), `lib/api/client.ts`
- `middleware.ts` (`tenant_id` + `app_role` check + `jwt-decode`)
- Pages: `/login`, `/invite` (3 states), `/onboarding` (placeholder)
- `(dashboard)/layout.tsx` with `GET /api/auth/me` + `React.cache()`

**Acceptance (full Pipeline A test):**
- Log into admin panel → provision "Accenture" with `cto@accenture.com`
- Invite URL appears in terminal stdout (dry-run mode)
- Paste URL → `/invite` → verify → signup form → create account
- JWT contains `tenant_id`, `app_role = Company Admin`
- `complete-invite` claims token, creates user row
- Redirected to `/onboarding` placeholder
- Second click on same invite URL → "Invite Invalid or Expired"
- Return via `/login` → `signInWithPassword` → dashboard

### Phase 3 — Pipeline B (Team Invites)

**Backend:**
- `settings` module: invite, list members, resend, revoke, deactivate

**Frontend:**
- `/(dashboard)/settings/team/page.tsx`: member list + invite form + resend/revoke/deactivate actions

**Acceptance:**
- Company Admin invites Recruiter → invite URL in terminal
- Recruiter completes flow → sees dashboard scoped to same tenant
- Company Admin can resend (old invite superseded), revoke (pending only), deactivate (accepted user)

### Phase 4 — Onboarding Wizard

**Backend:**
- `onboarding` module: company profile update + completion

**Frontend:**
- Multi-step wizard: company profile → (ATS config placeholder) → notification prefs
- Replaces Phase 2 placeholder

**Acceptance:**
- Company Admin completes wizard → `onboarding_complete = true`
- Dashboard loads normally, no redirect back to onboarding
- Returning Company Admin who hasn't completed onboarding → redirected to wizard

### Phase 5 — Email Polish + Scheduled Reminders

**Backend:**
- Branded HTML templates with company logo, plain-text fallbacks, unsubscribe placeholder
- Dramatiq tasks: 48h reminder + 24h expiring-soon

**Acceptance:**
- Pending invite triggers reminder at 48h
- Expiring-soon notification at 24h before `expires_at`
- Emails render correctly with company branding

---

## 11. Security Invariants

- Raw invite token NEVER stored in database — only SHA-256 hex digest
- Invite claim is atomic UPDATE-WHERE-RETURNING — no SELECT-then-UPDATE (TOCTOU)
- Email match enforced: invite email must equal OAuth/signup email
- `SET LOCAL` (not `SET`) for RLS context — scoped to transaction only
- `get_bypass_session()` only on admin routes, `complete-invite`, and onboarding completion
- `getUser()` for session validation, `jwtDecode()` for custom claims — never `getSession()` alone for security
- `app_role` for RBAC — never `role` (which is always `"authenticated"`)
- No `SECURITY DEFINER` on auth hook — use explicit grants
- `is_projectx_admin` set manually in Supabase dashboard, not via any API
