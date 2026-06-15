# Candidate Pre-Check Redesign — Design

**Date:** 2026-06-15
**Surface:** `frontend/session` (candidate interview surface)
**Status:** Approved (brainstorming) → ready for implementation plan
**Author:** AI (co-authored)

---

## 1. Problem

The candidate pre-check today is a 5-click wizard:

> Begin → Consent (check a box, click Continue) → [OTP] → Test camera & mic → Continue → Start interview

This is slower and more clinical than it needs to be. We want the candidate's
first impression to be **simple, quick, reassuring, and a little futuristic** —
while keeping every legal/compliance guarantee intact.

### Hard constraints that shape the design (cannot be removed)

1. **Consent is AIVIA-mandated.** A timestamped consent event (`POST /consent`)
   must be recorded before `/start`. The backend session state machine enforces
   `created → pre_check → consented → active`; `/start` is rejected unless the
   session is `consented` (and OTP-verified when required). Consent can be made
   *low-friction*, but it cannot be skipped, and the full consent text must
   remain accessible to the candidate.
2. **OTP is conditionally required** (`otp_required` per JD). When required, the
   6-digit email code step must still gate `/start`.
3. **Single-use token.** `/start` atomically consumes the token. The candidate
   may revisit the pre-check page before starting, but once they start, the link
   is spent — this must be communicated clearly, not silently.
4. **Surface rules** (see `frontend/session/CLAUDE.md`): no Supabase, no new
   analytics, token never logged/stored, CSP/security headers unchanged, mobile
   must work, audio constraints read from `/start` (`audio_processing_hints`),
   accessibility on every step (keyboard nav, reduced-motion, aria-live errors).

**This is a pure front-end change.** No backend, no API contract change, no new
dependencies. `motion` and `lucide-react` are already installed; the app already
mounts a site-wide `AnimatedBackground` (WebGL) and ships the Aura orb visualizer.

---

## 2. Goals / Non-Goals

**Goals**
- Collapse the 5-click wizard into **2 visible stages** (Intro → Ready), with OTP
  as a conditional middle slide.
- A premium **scale + opacity + blur** transition between stages.
- A welcoming intro: hand-crafted SVG illustrations on one side; headline
  (screening title + estimated duration) + an **interactive instruction list** on
  the other. Make the candidate feel comfortable.
- Soft-fold consent into the primary CTA (compliant, auditable, one fewer click).
- Replace ProjectX branding with **BinQle.ai** (logo + name), matching
  `frontend/app`.
- Optimized for **mobile and desktop**.

**Non-Goals**
- No change to the in-session live interview UI (`components/interview/app/*`,
  `session/*`) beyond swapping the ProjectX logo/name default.
- No backend/API changes. No new third-party origins, deps, or analytics.
- No change to proctoring detection logic, audio-hints handling, or the
  candidate-session API client (`lib/api/candidate-session.ts`).

---

## 3. Flow architecture

Two visible stages, OTP inserted only when required:

```
STAGE 1 · INTRO            (→ optional VERIFY)        STAGE 2 · READY
┌────────────────────┐                              ┌────────────────────┐
│ SVG scene │ Headline│   ── scale+fade+blur ──►     │  Camera preview     │
│  + orb    │ + title │                              │  mic/cam auto-test  │
│           │ + ⏱ dur │                              │                     │
│           │ instrs  │                              │  [ Start ] (1 CTA)  │
│           │ [I'm    │                              └────────────────────┘
│           │  ready] │   POST /consent fires here
└────────────────────┘
```

### Stage → backend-state mapping (drives `WizardShell`)

| Pre-check `state` (+ flags)                    | Rendered                                   |
|------------------------------------------------|--------------------------------------------|
| `created` / `pre_check`                        | **Intro**                                  |
| `consented` + `otp_required` + !`otp_verified` | **Verify** (OTP)                           |
| `consented` (OTP done or not required)         | **Ready**                                  |
| `active`                                        | `<App mode="rejoin">` (existing)           |
| `completed`                                     | `CompletionScreen` (existing)              |
| `terminated`                                    | `ProctoringEndedScreen` (existing)         |
| `cancelled` / `error` / load error             | Error landing (existing copy)              |

Local UI state in `WizardShell`:
- `camMicPassed: boolean` — once true and on Ready, mount `<App mode="start">`.
- The Intro→next advance is driven by the **server state flip** after `POST /consent`
  succeeds, not ad-hoc local state. The existing `useConsent` hook already
  `setQueryData`-flips the cached `/pre-check` `state` to `'consented'`
  synchronously (then invalidates) — and `useVerifyOtp` stamps `otp_verified_at`
  the same way — specifically to avoid a subscriber-notify race that previously
  stranded the wizard. **Reuse these hooks verbatim.** The derived stage recomputes
  from the cached state and `AnimatePresence` animates the swap.

### Consent soft-fold (Stage 1)

- Primary CTA: **"I'm ready →"**. On click:
  1. Button enters pending state.
  2. Fires `POST /consent { consented: true, user_agent }` via the existing
     `useConsent` hook.
  3. On success → the session becomes `consented` → derived stage advances →
     transition plays. On error → toast + button re-enabled (no stage change).
- Directly under the CTA, persistent microcopy:
  *"By starting, you consent to this AI-led interview and its recording."*
- A **"Privacy & consent"** text button opens a dialog (radix-ui Dialog, already a
  dep) showing the full `consent_text` (`whitespace-pre-wrap`). Read-only;
  closing it does not consent — only the CTA does.
- Accessibility: dialog is focus-trapped + Esc-dismissable; CTA has an accessible
  busy state.

### Verify stage (only when `otp_required`)

- Reuses `useRequestOtp` / `useVerifyOtp` and all existing rules (60s cooldown
  restored from `otp_issued_at`, max-3-attempts messaging via `aria-live`,
  numeric inputMode). Re-themed into the new stage shell; logic unchanged.
- On successful verify, server state gains `otp_verified_at` → derived stage
  advances to Ready with the transition.

### Ready stage (Stage 2)

- Camera preview + **auto** mic/cam test (request `getUserMedia`, sample noise
  floor) — same logic as today's `CameraMicStep`, including the multi-display
  **warning** (non-blocking) for proctored sessions and the noisy-environment
  warning. `getUserMedia` constraints/handling are unchanged (Human-Review path).
- Single primary CTA **"Start"**, enabled only once devices pass. Click →
  `setCamMicPassed(true)` → `WizardShell` mounts `<App mode="start">` (which calls
  `POST /start`). No separate "Continue" step.

---

## 4. The transition (`StageTransition`)

A small `motion` wrapper around the active stage, keyed by stage id, inside
`AnimatePresence`:

- **Exit:** `scale 1 → 0.94`, `opacity 1 → 0`, `filter blur(0 → 6px)`, slight
  `y: 0 → -12`. Duration ≈ 0.28s.
- **Enter:** `scale 1.04 → 1`, `opacity 0 → 1`, `filter blur(8px → 0)`,
  `y: 12 → 0`. Spring/ease-out, duration ≈ 0.4s (exit ~70% of enter, per motion
  guidance).
- Transforms/opacity/filter only — **no layout reflow / CLS**.
- `prefers-reduced-motion` → plain crossfade (`opacity` only, ~0.15s), no
  scale/blur/translate. Honored via `useReducedMotion()` from `motion` (or the
  existing `use-prefers-reduced-motion` hook).
- The hero orb does a subtle shared "pulse" on hand-off (decorative, reduced-
  motion-safe).

---

## 5. Layout & responsive

- **Desktop (lg ≥ 1024px):** two-column split inside `WizardFrame`.
  - Left panel: BinQle.ai mark (top) + hero SVG scene (orb + calm setup).
  - Right panel: headline (`Fraunces` / `.px-serif`), duration chip, interactive
    instruction list, CTA + consent microcopy.
- **Mobile (< 1024px):** single column, `min-h-dvh`.
  - Compact hero illustration at top, screening title + duration, instruction
    list, **full-width CTA** at the bottom of the content flow.
  - Body text ≥ 16px (avoids iOS auto-zoom); all targets ≥ 44px; no horizontal
    scroll; safe-area aware.
- Spacing on the 4/8px rhythm; colors strictly from `--px-*` tokens (no raw hex
  in components); per-tenant `accent` override still applied via
  `--px-accent` as today.

---

## 6. Illustrations & interactive instruction list

Hand-crafted **inline SVG** (consistent 1.5px stroke, `--px-*` token fills,
subtle motion; all motion reduced-motion-safe):

- **Hero scene** (intro left panel): a calm "candidate + glowing Arjun orb"
  composition with gently floating particles. The orb visually rhymes with the
  in-session Aura.
- **Per-instruction mini-glyphs** for the interactive list. Each row = bespoke
  glyph + title; tap/hover (progressive disclosure) reveals a one-line "why".
  Rows stagger in (~40ms each).

Instruction content:

| # | Glyph            | Title                     | Detail (expand)                                                                 |
|---|------------------|---------------------------|---------------------------------------------------------------------------------|
| 1 | orb / spark      | Meet Arjun                | Your interview is led by Arjun, a friendly AI. Just talk naturally.             |
| 2 | quiet room       | Find a quiet spot, alone  | No one else in the room; background noise can disrupt the call.                  |
| 3 | single monitor   | One screen only           | Extra monitors aren't allowed and are flagged.                                  |
| 4 | shield           | Proctored environment     | Your camera + focus are monitored — for review, never an auto-reject.           |
| 5 | link (caution)   | One-time link             | You can revisit this page, but **once you start, this link is used up** — you'll need a fresh one from the recruiter. |

- Row #5 uses a distinct **reassuring-but-clear caution** treatment
  (`--px-caution*` tokens), not an alarming/error tone.
- The list communicates "you can come back later if your environment isn't
  ready" explicitly (the user's requirement).

---

## 7. Branding (BinQle.ai)

- Copy `binqle-mark.png` + `binqle-wordmark.png` from
  `frontend/app/public/brand/` → `frontend/session/public/brand/`.
- Add `frontend/session/lib/brand.ts` mirroring `frontend/app/lib/brand.ts`
  (name, shortName, logo assets) — per the two-app drift discipline, keep the
  shape compatible. (The session app does not need the full theme/density config;
  a minimal subset — `name`, `shortName`, `logo.{mark,wordmark}` — is sufficient.)
- New `BrandMark` component renders the BinQle mark (and wordmark where space
  allows). Header = **BinQle.ai mark + screening title** (`Company · Role`).
- Replace the `'ProjectX'` / `/projectx-logo.svg` defaults in:
  - `app-config.ts` (`companyName` default stays per-tenant, but `pageTitle`,
    `pageDescription`, `logo` move to BinQle).
  - `WizardFrame` brand fallback (`'ProjectX'` → BinQle).
  - `app/layout.tsx` metadata if it references ProjectX.
- Out of scope: redesigning the in-session shell; we only swap the logo/name so
  the platform identity is consistent.

---

## 8. Components / files

**New**
- `app/interview/[token]/IntroStage.tsx` — split intro (replaces `WelcomeStep` +
  `ConsentStep`); owns consent soft-fold + consent dialog.
- `app/interview/[token]/VerifyStage.tsx` — OTP (replaces `OtpStep`), re-themed.
- `app/interview/[token]/ReadyStage.tsx` — camera/mic + Start (replaces
  `CameraMicStep`).
- `app/interview/[token]/StageTransition.tsx` — `motion`/`AnimatePresence`
  wrapper; reduced-motion aware.
- `app/interview/[token]/illustrations/` — hero scene + instruction glyphs
  (inline SVG React components).
- `components/interview/BrandMark.tsx` — BinQle mark/wordmark.
- `lib/brand.ts` — minimal brand config.

**Rewritten**
- `WizardShell.tsx` — derive stage from state, drive `AnimatePresence`, preserve
  all edge-state branches (`active`/`completed`/`terminated`/`error`).
- `WizardFrame.tsx` — BrandMark + the new split layout; remove the old stepper
  chrome (the 2-stage flow doesn't need a 3-dot stepper; a minimal progress hint
  may live in the frame if useful).
- `app-config.ts` — BinQle defaults.

**Removed**
- `WelcomeStep.tsx`, `ConsentStep.tsx`, `OtpStep.tsx`, `CameraMicStep.tsx`,
  `WizardStepper.tsx` (logic folded into the new stages; `sampleNoiseFloorDbfs.ts`
  is retained and reused by `ReadyStage`).

**Unchanged (must not regress)**
- `lib/api/candidate-session.ts` (100%-branch gate), `lib/hooks/use-*`,
  `lib/proctoring/displays.ts`, audio-hints handling, `proxy.ts` CSP,
  `next.config.ts` headers, the live `App`.

---

## 9. Testing

(Vitest + Testing Library; mock at the API/hook boundary, never hit network.)

- **WizardShell state mapping** — each `state`/flag combo renders the right stage;
  edge states (`active`/`completed`/`terminated`/`cancelled`/`error`/load-error)
  render their existing screens.
- **IntroStage** — "I'm ready" fires `POST /consent` exactly once with
  `consented: true`; pending state shown; error path shows toast + re-enables CTA
  and does NOT advance; consent dialog opens/closes and does not itself consent;
  instruction rows expand/collapse; single-use warning copy present.
- **VerifyStage** — only rendered when `otp_required` && !`otp_verified_at`;
  send-code cooldown restored from `otp_issued_at`; attempts-remaining announced
  via `aria-live`. (Port existing OTP tests.)
- **ReadyStage** — Start disabled until devices pass; pass enables Start and
  mounts the start path; denied → retry; noisy + multi-display warnings render;
  `getUserMedia` permission states announced.
- **Transition / reduced-motion** — with `prefers-reduced-motion`, the transition
  is a crossfade (no transform/blur) and stages still swap.
- **Branding** — BrandMark renders BinQle assets; no `ProjectX` string remains in
  the pre-check surface.

Coverage: keep the candidate-session client at 100% branch (untouched); new
components covered to the surface's existing bar.

---

## 10. Risks & mitigations

- **Consent desync** if we advanced optimistically. → We advance on the
  server-state flip after `POST /consent`, not optimistically.
- **Transition jank on low-end mobile.** → transform/opacity/filter only;
  reduced-motion crossfade; one hero animation at a time; respect the bundle
  budget (pre-`/start` route < 180 KB gzip — illustrations are inline SVG, no new
  deps).
- **Bundle creep.** → No new dependencies; LiveKit stays lazy-loaded as today.
- **Two-app drift.** → `lib/brand.ts` mirrors `frontend/app` shape; brand assets
  copied, not forked in style.
- **Accessibility regressions on a Human-Review surface (consent/OTP/cam-mic).**
  → preserve aria-live error patterns, keyboard nav, focus management; call out
  the cam/mic `getUserMedia` change region in the PR/commit.

---

## 11. Out of scope / follow-ups

- In-session shell visual refresh (separate effort).
- Any backend change to consent/OTP/start semantics.
- Localization of instruction copy.
