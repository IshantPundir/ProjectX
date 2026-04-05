@AGENTS.md

# ProjectX — Frontend (Admin)
## Claude Code Context (Admin App)

> Read the root `CLAUDE.md` first. This file contains admin-app-specific rules that extend it.

---

## What This Surface Is

The **Admin App** is an internal tool for ProjectX operators. It is NOT client-facing. Its sole purpose is to provision new client tenants and monitor their status.

This is a minimal internal surface — not a product UI. It does not need the same level of polish as the Client App.

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

**There is no `middleware.ts` or server-side auth guard.** Route protection is client-side only:
- The dashboard page checks `supabase.auth.getSession()` in `useEffect` and redirects to `/login` if no token
- This means an unauthenticated user briefly sees the admin shell before redirect

The admin account must have `is_projectx_admin: true` in their Supabase `app_metadata` to use the backend admin endpoints. This flag is set outside this app (e.g., via Supabase dashboard or CLI).

---

## API Endpoints Used

| Method | Endpoint | Used By |
|---|---|---|
| GET | `/api/admin/clients` | Dashboard page |
| POST | `/api/admin/provision-client` | Provision page |

Both require `is_projectx_admin` JWT claim.

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
npm run lint         # ESLint
npm run type-check   # tsc --noEmit
```

---

## Design Constraints

- No shared component library — all UI is inline within page files
- No Zustand, TanStack Query, or shadcn/ui — not needed for this surface
- Keep it simple — this is an internal tool, not a product
