@AGENTS.md

# ProjectX — Frontend (Admin)
## Claude Code Context (Admin App)

> Read the root `CLAUDE.md` first. This file contains admin-app-specific rules that extend it.

---

## What This Surface Is

The **Admin App** is an internal-only console for ProjectX operators. It provisions, blocks, and deletes client tenants — every action has tenant-wide blast radius. It is held to the **same enterprise quality bar** as the customer-facing surfaces. "Internal" describes the audience, not a license to skip security, accessibility, audit, or test rigor.

Surface scope is intentionally narrow: tenant lifecycle (provision / block / unblock / soft-delete / hard-delete) and operator account management. Anything beyond that belongs in the recruiter app or the backend.

> Read the root `CLAUDE.md` first — Enterprise Operating Standards (reliability, supply chain, secrets, logging/PII, audit, code review, incident response) apply to this surface in full.

---

## Tech Stack

- **Framework:** Next.js 16.2.2 with App Router
- **Language:** TypeScript (strict mode)
- **Styling:** Tailwind CSS v4 (utility-first)
- **Auth:** @supabase/ssr v0.10 (email/password only)
- **HTTP client:** `apiFetch` wrapper in `lib/api/client.ts` — all calls go to Nexus
- **Hosting MVP:** Railway

---

## Directory Structure

```
frontend/admin/
├── app/
│   ├── layout.tsx                    ← Root layout (Geist fonts, zinc-50 bg)
│   ├── globals.css                   ← Tailwind v4 import only
│   ├── (auth)/
│   │   ├── layout.tsx                ← Centered card container
│   │   ├── login/page.tsx            ← Email+password login via Supabase
│   │   └── signup/page.tsx           ← Admin account creation
│   ├── pending-approval/
│   │   └── page.tsx                  ← Post-signup waiting screen
│   └── (admin)/
│       ├── layout.tsx                ← Side nav + sign out
│       ├── page.tsx                  ← Redirect to /dashboard
│       └── dashboard/
│           ├── page.tsx              ← Client list table
│           └── provision/
│               └── page.tsx          ← New client provisioning form
├── lib/
│   ├── api/client.ts                 ← apiFetch() utility (identical to client app)
│   └── supabase/
│       ├── client.ts                 ← Browser Supabase client
│       └── server.ts                 ← Server Supabase client (cookies)
├── next.config.ts                    ← output: "standalone"
├── tsconfig.json                     ← strict mode, @/* path alias
├── package.json
└── .env.local.example
```

---

## Current Functionality

| Route | Purpose |
|---|---|
| `/login` | Email/password sign-in for ProjectX admins |
| `/signup` | New admin account creation → redirects to `/pending-approval` |
| `/pending-approval` | Waiting screen (admin approval is handled outside this app) |
| `/dashboard` | Lists all provisioned clients with invite/onboarding status |
| `/dashboard/provision` | Form to create a new client tenant and send Company Admin invite |

---

## Auth Pattern

**Route protection is enforced server-side via Next.js middleware.** The middleware handler lives in `frontend/admin/proxy.ts` (exported as `proxy`, matched by the `config.matcher` at the bottom of that file). On every non-static request it:

1. Calls `supabase.auth.getUser()` to cryptographically validate the session (also refreshes the token cookie if needed).
2. Redirects unauthenticated users away from any non-public route to `/login`.
3. Checks `user.app_metadata.is_projectx_admin`. Non-admin authenticated users are redirected to `/pending-approval`. Only admins reach `/dashboard` and `/dashboard/provision`.
4. Redirects already-authenticated users away from `/login` and `/signup`.

This means the unauthenticated admin shell is never rendered — the redirect happens before the React tree mounts.

The `is_projectx_admin: true` claim must be set in the admin user's Supabase `app_metadata` outside this app (via Supabase dashboard, CLI, or a backend provisioning script). The frontend does not grant or modify this flag.

---

## API Endpoints Used

| Method | Endpoint | Used By |
|---|---|---|
| GET | `/api/admin/clients` | Dashboard page |
| POST | `/api/admin/provision-client` | Provision page |

Both require `is_projectx_admin` JWT claim.

**Available but not yet wired into the admin UI** — backend exposes the full tenant lifecycle:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/admin/clients/{id}/block` | Set `clients.blocked_at` (reversible) |
| POST | `/api/admin/clients/{id}/unblock` | Clear `clients.blocked_at` |
| DELETE | `/api/admin/clients/{id}` | Soft-delete (sets `clients.deleted_at` + cascades to users) |
| POST | `/api/admin/clients/{id}/hard-delete` | Hard-delete via PG cascade (audit history is preserved — see migration 0023) |

Wire these into the dashboard when tenant suspension / deletion lands as an admin task.

---

## Environment Variables

```bash
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<from supabase status>
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

---

## Dev Commands

```bash
npm run dev          # Start dev server (localhost:3001)
npm run build        # Production build
npm run lint         # ESLint — must pass with zero errors
npx tsc --noEmit     # Type check — must pass with zero errors (no dedicated script yet)
npm run test         # Vitest (add when test suite lands)
```

CI fails on lint or type-check errors. Fix before pushing.

---

## Human Review Required For

This surface has the highest blast radius in the platform. Senior reviewer + explicit sign-off in the PR description for any change to:

- `proxy.ts` (route protection, `is_projectx_admin` gating)
- `app/(auth)/` (login, signup, session establishment)
- `app/(admin)/dashboard/provision/` (tenant provisioning form)
- Any UI wiring for tenant block/unblock, soft-delete, or hard-delete (when those land)
- Any change to how `is_projectx_admin` is read or surfaced
- Any change to API client error handling that affects how operator failures are surfaced

---

## Absolute Rules

### Security — Same Bar as the Recruiter App
- **Security headers** in `next.config.ts` `headers()`: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()` (admin surface needs none of those). CSP is a planned follow-up.
- **No `dangerouslySetInnerHTML`** for any backend-returned string.
- **No `localStorage` for auth tokens.** Only non-sensitive UI preferences.
- **Post-auth and post-mutation redirects must be allowlisted** — validate that any URL controlled by a backend response starts with `/` and not `//` before navigation.
- **No direct Supabase data calls.** The Supabase client is auth-only. All data goes through `apiFetch` to Nexus.
- **No PII in browser logs or Sentry** — tenant names are OK; emails, full tokens, and raw request bodies are not.

### Audit & Authorization
- **Every operator action is audit-logged backend-side.** Provision, block, unblock, soft-delete, hard-delete, and any role mutation must produce an audit record (`actor_id`, `action`, `resource_type`, `resource_id`, `correlation_id`). The frontend's job is to send the request and surface failures clearly; the backend writes the audit row.
- The `is_projectx_admin` claim is set **out-of-band** (Supabase dashboard / CLI / backend script). The frontend never grants or modifies it. Treat it as a load-bearing claim — verify on every protected route via middleware.
- Destructive operations (hard-delete, unblock-after-block) MUST use `DangerConfirmDialog` semantics (typed-confirmation, not a one-click button) when wired into the UI.

### TypeScript & Forms
- `"strict": true` — no `any`. Use `unknown` + type narrowing.
- Forms use React Hook Form + Zod when added. No raw `useState` forms beyond the current Phase 1 login/signup/provision pages — migrate them when touching them.

### Testing
- The admin surface is **not exempt** from tests. Provisioning, blocking, deletion, and middleware route protection must have tests before they ship. Vitest is the runner of record (add via `npm install -D vitest @testing-library/react jsdom` if not yet present).
- Composition tests (parent + child rendered together, mocking at the API boundary) are the convention — see the recruiter app's `tests/` directory.

### Accessibility
- All interactive elements keyboard-navigable. Semantic HTML, not `div` soup.
- Dialogs and drawers move focus on open. Confirm dialogs trap focus.
- ARIA labels on icon-only buttons.

---

## Design Constraints

- Surface stays narrow — tenant lifecycle + operator accounts only. Push anything else to the recruiter app or the backend.
- UI is currently inline within page files because the surface is small. **If a component is reused across ≥2 pages, lift it into `components/`** and add an index barrel.
- No Zustand or TanStack Query at present — surface is small enough for raw `useState` + `apiFetch`. Revisit when surface grows.
- Tailwind v4 utility-first. Same design tokens as the recruiter app — when shared design tokens are extracted, this app consumes them.
