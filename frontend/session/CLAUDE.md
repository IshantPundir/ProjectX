# ProjectX — Frontend (Session)
## Claude Code Context (Candidate Interview Surface)

> Read the root `CLAUDE.md` first. This file contains rules specific to the
> candidate-interview surface (extracted from `frontend/app` in Phase 3b).

---

## What This Surface Is

`frontend/session` is the **candidate-facing** Next.js app. JWT-accessed
(no login), camera + mic required, branded per tenant. Pre-check flow →
live session → completion screen. Hosted separately from the recruiter
dashboard so candidate traffic never shares blast radius with operator
traffic.

---

## Coverage Gates

The thresholds in `vitest.config.ts` are **floors**, not aspirations. They
are calibrated just below current actual coverage so any future drop
fails the build, but they do **not** represent the standard the
candidate-session surface should ultimately meet.

### Aspirational Targets (per root CLAUDE.md "Test Coverage Gates")

The candidate-session surface is one of the four 100%-branch surfaces
called out in the root `CLAUDE.md`. The other three (auth, RLS,
candidate-session module) live in `backend/nexus`. Here, the three
files that must reach **100% branch coverage** are:

- `lib/api/candidate-session.ts` — also targets 100% function coverage
- `app/interview/[token]/OtpStep.tsx`
- `components/interview/app/app.tsx`

### Current Floors (Phase 3b extraction baseline)

| File | Branches floor | Functions floor | Aspiration |
|---|---|---|---|
| `lib/api/candidate-session.ts` | 39 | 23 | 100 / 100 |
| `app/interview/[token]/OtpStep.tsx` | 26 | — | 100 |
| `components/interview/app/app.tsx` | 0 | — | 100 |
| Global lines | 17 | — | 80 |
| Global statements | 17 | — | 80 |

The floors were set after the Phase 3b structural move from
`frontend/app` to `frontend/session`. The moved tests never had the
aspirational coverage on `main` — the gate was aspirational, not
enforced. Phase 3b is a **structural move, not a coverage uplift**, so
the gates establish the floor; follow-up PRs will backfill tests and
ratchet the gates upward toward the aspiration.

### Rules for Changing Coverage Gates

- **Ratchet up only.** When a follow-up PR backfills tests, raise the
  floor in the same PR — never leave a gap between actual coverage and
  the gate.
- **Never relax a floor without justification.** A drop in coverage on
  these three files needs a written reason in the PR description (e.g.
  "removed dead branch X", not "tests are flaky").
- **The aspiration is the target, not the ceiling.** Once a file
  reaches 100%, the gate stays at 100%.
