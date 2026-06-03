# ProjectX — Product Overview for UI/UX Redesign

> **Audience:** The UI/UX designer leading the next visual + interaction overhaul.
> **Purpose:** A complete picture of what ProjectX is, who uses it, how it works today, and where it is going — so the redesign starts from understanding, not guesswork.
> **Date:** 2026-05-15 (snapshot) · **Corrections appended 2026-06-03**
> **Status:** Reflects the codebase as of branch `feature/tracker-page`.
>
> ⚠️ **Shipped since this snapshot (treat the "placeholder / coming soon" notes below as
> historical):** the **Reports** surface is now fully built — a reports hub + per-session
> report viewer with verdict-driven scoring, signal audit, **session recording playback**,
> the **ReviewTheater** glass player, an optional **Candidate Reel** highlight video, and a
> **proctoring integrity panel**. Vision proctoring (client deterrent + server gaze analysis)
> shipped as a POC. The backend `reporting`, `reel`, and `vision` modules are implemented.
> The always-on *in-session* AI Copilot remains unbuilt. Audio path is **Deepgram STT →
> OpenAI LLM → Sarvam TTS** (not Sarvam STT).

---

## 1. What ProjectX Is

ProjectX is an **enterprise-grade B2B SaaS platform that replaces the recruiter phone-screen with structured, AI-led video interviews**.

A hiring team uploads (or syncs) a job description, the system pulls out the signals that matter, generates a structured interview question bank, sends candidates a branded invite link, runs the entire first-round interview as a live AI-led video session, and produces an evaluation report. Recruiters keep oversight at every step — they confirm signals, edit questions, advance candidates, and review reports — but the AI does the heavy lifting between those checkpoints.

### The wedge

Replace the recruiter phone-screen for Fortune 500 hiring teams running high-volume pipelines. Scale to 500+ simultaneous interview sessions without adding headcount.

### What it is *not*

- Not a chatbot. The candidate is on a real video call with an AI interviewer running on LiveKit, with proper audio (**Deepgram STT → OpenAI LLM → Sarvam TTS**) and a structured rubric per question.
- Not an ATS. ProjectX *integrates* with ATS systems (Ceipal, Greenhouse, Workday). It sits *between* the ATS and the candidate, doing the interview work that recruiters do today.
- Not a sourcing tool. Candidates arrive either via manual entry, resume upload, or ATS sync.
- Not a one-size-fits-all template. Every JD generates its own question bank from a 4-layer context stack (JD + company profile + candidate brief + project brief).

---

## 2. Who Uses It

ProjectX has **three distinct user surfaces**, each with its own deployable web app:

| Surface | URL pattern | Who | Auth |
|---|---|---|---|
| **Recruiter Dashboard** (`frontend/app`) | `app.projectx.com` | Recruiters, Hiring Managers, Interviewers, Observers, Admins | Email + password (Supabase) |
| **Admin Console** (`frontend/admin`) | `admin.projectx.com` | ProjectX-internal operators (provision tenants) | Email + password |
| **Candidate Session** (`frontend/session`) | `interview.projectx.com` | Candidates only | Single-use JWT in URL — no account |

**This document is primarily about the Recruiter Dashboard** — that is what's being redesigned. The Candidate Session is covered because (a) it shapes what the recruiter sees in session-monitoring views and (b) it's part of the same brand expression. The Admin Console is internal-only and out of scope.

### Recruiter Dashboard personas

| Role | What they do | Permission tier |
|---|---|---|
| **Super Admin** | Tenant owner. Sets up the company profile, manages billing (future), invites team, configures org units. There is exactly one per tenant. | Highest |
| **Admin** | Manages an org unit (division / region / client account / team). Can invite members within their unit, manage pipeline templates, see all jobs under their unit. | Per-unit |
| **Recruiter** | Day-to-day operator. Creates job postings, runs the JD → signals → pipeline → questions flow, invites candidates, advances them through stages, reviews reports. | Per-unit |
| **Hiring Manager** | Reviews candidate reports, makes advancement decisions, can be assigned as a reviewer on `human_interview` stages. | Per-unit, read-leaning |
| **Interviewer** | Participates in Round 2 panel sessions with AI Copilot support. Future scope. | Per-unit, action on assigned stages |
| **Observer** | Read-only view of pipelines, candidates, reports. Often a hiring leader who wants visibility without action capability. | Per-unit, read-only |

**Important:** Permissions are scoped to *org units* and walk the ancestry. A Recruiter with Admin on the "Engineering" division can manage all teams underneath, but cannot see anything in the "Sales" division. The permission system never trusts the JWT — every check is a fresh DB lookup per request.

### The candidate

The candidate is not a tenant user. They are a person who clicks an emailed link, completes a consent/OTP/device-check wizard, and joins a live video call with the AI interviewer. They have no account, no password, no dashboard. The interview is the entire surface.

---

## 3. Core Domain Concepts

Before walking the journeys, the designer needs these vocabulary anchors:

| Concept | What it means |
|---|---|
| **Tenant** | A company using ProjectX (e.g. "Acme Corp"). Every row in the database is scoped to a tenant. Tenants are isolated at the database level (RLS). |
| **Org Unit** | A node in the company's internal structure: `company` (root, exactly one), `division`, `region`, `client_account`, `team` (leaf). Forms a hierarchical tree. Permissions and visibility flow through this tree. |
| **Company Profile** | A small structured blob (about / industry / hiring_bar / location) attached to an org unit. **Critical:** every JD enrichment, signal extraction, and question generation reads this profile via an ancestry walk. Without it, the AI pipeline cannot run. |
| **Job Posting (Role)** | A specific job opening. Belongs to one org unit. Has a status state machine: `draft → signals_extracting → signals_extracted → signals_confirmed → pipeline_built → active`. |
| **Signal** | A specific hiring requirement extracted from the JD by AI (e.g. "5+ years Python", "Led team of 8+", "Series B startup experience"). Each signal has type (must-have / nice-to-have), provenance (where it came from), and confidence. Recruiter confirms signals before the pipeline can be built. |
| **Pipeline** | The ordered sequence of stages a candidate goes through for a specific role. Built from a template or starter pack. Editable per-role. |
| **Stage** | One step in the pipeline. Six types exist: `intake`, `phone_screen`, `ai_screening`, `human_interview`, `debrief`, `take_home`. Each stage type implies different config and capabilities. |
| **Question Bank** | Per-stage set of 8–10 interview questions, AI-generated from the stage config + signals + company context. Has its own state machine: `not_generated → draft → generating → reviewing → confirmed`. Confirming the bank is what makes the stage interview-ready. |
| **Candidate** | A person being considered for one or more roles. Has profile fields, resume, and a list of `assignments`. PII can be redacted (GDPR) — irreversibly. |
| **Assignment** | The link between a candidate and a job. Tracks which stage the candidate is currently in. |
| **Session** | A specific instance of a live AI-led video interview. State machine: `created → pre_check → consented → active → completed | cancelled | error`. |
| **AI Copilot Panel** | (Future) An always-on side panel during live human-involved sessions showing transcript, signal cards, next planned probe, and coverage tracker. Not built yet. |
| **Borderline** | A candidate scored ambiguously by the AI. **Never auto-advances or auto-rejects.** Must go to a human. This is a non-negotiable product invariant. |
| **Knockout signal** | A signal so important that failing it ends the interview early (e.g. "must have work authorization in US"). Each knockout signal must be probed by at least one mandatory question. |
| **Correlation ID** | Every session carries one ID end-to-end through WebRTC, STT, LLM, scoring, and reporting. The designer doesn't see this, but it matters for debugging. |

---

## 4. The Recruiter Journey — End to End

This is the **golden path** a recruiter walks the first time they use the product. The redesign should make this journey feel coherent.

### Phase A — Tenant setup (first login only)

1. **Tenant is provisioned by a ProjectX operator** (admin console action). The Super Admin receives an invite email.
2. **Invite acceptance** (`/invite?token=...`) — they set a password, accept the invite, land in the dashboard.
3. **Onboarding wizard** (`/onboarding`) — *one screen, three questions*:
   - "What does your company actually build or do?" (max 500 chars, conversational tone)
   - "Industry" (e.g. "SaaS / Enterprise Software, Fintech, Healthcare")
   - "What does a strong hire look like here?" (max 280 chars, hints at non-obvious cultural traits)

   This populates the **root company profile** that every downstream AI call uses. It's not optional — the entire pipeline is blocked until this is complete.

4. **Land on dashboard home** (`/`) — the recruiter's "what needs attention" view.

### Phase B — Setting up the first role

1. **Click "Roles" in sidebar** (`/jobs`) — empty state encourages "Create your first role."
2. **Click "New role"** (`/jobs/new`) — a single form collecting:
   - Role title (required)
   - Org unit (required — anchors the role to a part of the org tree, drives company-profile inheritance)
   - Employment type, work arrangement, location (if onsite/hybrid), salary range, travel, start date, headcount

   On save, the role exists as a `draft`. No AI has run yet.

3. **Land on role detail** (`/jobs/[jobId]`) in **JD draft state**.
   The screen is empty except for a paste area and call-to-action. The recruiter pastes the raw JD text.
4. **Trigger AI enrichment** (optional but recommended). This rewrites the raw JD into a polished, structured version using the company profile as context. Returns 202 — actual work runs in a Dramatiq worker (10–30 seconds). The page subscribes to an SSE status stream and updates live.
5. **Trigger signal extraction**. The AI pulls out must-haves, nice-to-haves, and a snapshot summary. Job transitions to `signals_extracting`, the page shows a loading view, then transitions to `signals_extracted`.

### Phase C — JD review (the 3-panel layout)

This is the **single most complex screen in the product** (`/jobs/[jobId]` after extraction).

Three columns, left-to-right:

- **Left (220px, sticky):** A "Sections Rail" — a navigation aid listing "Must-haves", "Nice-to-haves", "Snapshot" with counts. Click-to-scroll the center panel.
- **Center (flex):** A tabbed canvas. Default tab is "Signal details" (the editor). Alternate tabs: "Raw JD" (the original paste) and "Enriched JD" (the AI-rewritten version). The signal editor lists each extracted signal as a row with: signal label, confidence indicator, source badge ("which sentence of the JD did this come from?"), and an inline edit affordance.
- **Right (380px):** Signal Inspector. When no signal is selected, shows tips. When a signal is selected, shows the full editor: name, confidence, the source snippet *highlighted in context*, provenance chips, type and weight editors, and a remove button.

At the bottom of the center column, a sticky **ConfirmBar** with two actions:
- **Save** — persist current edits as a new snapshot version (uses `SELECT FOR UPDATE` server-side to avoid concurrent-edit chaos)
- **Save and confirm signals** — the **gate**. This locks the signal set, transitions the job to `signals_confirmed`, and auto-applies a default pipeline.

**SSE-driven freshness:** `useJobStatusStream(jobId)` keeps a persistent stream open. When the worker finishes re-enrichment or signal updates, the UI re-renders without polling.

**Snapshot versioning:** Every signal edit creates a new immutable snapshot (versions `1, 2, 3, …`). The shell remounts on version change to discard stale draft state.

### Phase D — Pipeline editing

After confirming signals, the user is auto-routed to the **pipeline editor** (`/jobs/[jobId]/pipeline`).

If no pipeline exists yet, they see a **source picker**: choose from a saved template, a starter pack, or build from scratch.

Once a pipeline exists, the layout is two columns:

- **Left column:** A vertical drag-to-reorder list of stages. Each stage card shows: position, name, stage type badge, duration, participant count. Drag handles use `@dnd-kit` (with `KeyboardSensor` for accessibility). Between stages, visual connector arrows. At the bottom: "+ Add stage" and an **Activation Gate**.
- **Right column:** Stage Inspector (when a stage is selected). Two tabs:
  - **Configuration:** name, type, duration slider, pass criteria editor, signal filter editor, difficulty slider
  - **Participants:** Assigned interviewers / reviewers / observers (drawn from users who hold the right role in the job's org-unit ancestry)

**Stage types and what they do:**

| Type | Role in pipeline | Special rules |
|---|---|---|
| `intake` | Structural first stage. Recruiter screen, paperwork. | No question bank. No duration. Cannot be paused. |
| `phone_screen` | Quick human recruiter screen. | Has question bank. Can send invite from this stage. |
| `ai_screening` | The flagship: AI-led video interview. | Has question bank. Can send invite. Most automation here. |
| `human_interview` | Panel interview with humans. | Has question bank. Has participant slots. Future Copilot panel target. |
| `debrief` | Structural last stage. Final decision. | No question bank. No duration. Cannot be paused. |
| `take_home` | Async coding/written assignment. | Future-leaning. |

**The Activation Gate** at the bottom of the pipeline column is the *go-live moment*. It checks predicates — every stage has a question bank, signals are confirmed, `human_interview` stages have interviewers assigned. When all green, **"Activate role"** flips the job to `active`. The activation API returns 422 with structured `predicates_failed` if any check fails, and the UI renders each failure as a specific actionable chip.

**Stage pause / unpause:** Individual stages can be paused (e.g. "the hiring manager is on PTO this week"). Pausing increments the pipeline version. Banks track which pipeline version they were generated against — when the version drifts, banks are marked `is_stale` and the UI prompts regeneration.

### Phase E — Question banks (per stage)

`/jobs/[jobId]/questions` — the per-stage question bank editor.

A horizontal pill strip at the top lists every eligible stage (intake and debrief excluded). Each pill shows the stage's bank status with a dot:
- `✓` (green) — confirmed
- `•••` (pulsing accent) — generating
- `◆` (caution) — awaiting review
- `○` (muted) — empty / draft
- `✗` (red) — failed

Two view modes (toggle at top-right):

**Review mode (default):** A two-column master-detail layout.

The **left panel** is sticky (pinned via GSAP ScrollTrigger, not CSS — when pinned, the panel's left edge animates outward to meet the nav rail). It shows:
- Stage header (type label, name)
- Three live meters: Question count, Mandatory count, Minutes used vs. duration budget
- Scrollable list of questions, each as a numbered row ("01", "02", …) with the first two lines of text, "MUST" badge if mandatory, time and probe count
- Footer: "+ Add question" ghost button and the **"Confirm bank"** primary button (the gate)

The **right pane** shows the selected question's full detail:
- Position number in large monospace + MANDATORY badge (if applicable)
- Estimated minutes + probe count meta
- "Refine" button (opens a dialog where the recruiter writes instructions for the AI to rewrite the question)
- "Regenerate" button (full re-roll of the question slot)
- Question text in 24px serif
- Signal chips (which signals this question probes)
- Evaluation hint box
- "Listen for" (green box, 3 bullet points) + "Red flags" (red box, 3 bullet points)
- Numbered follow-up probes in italic
- Rubric: three tier cards (Exceeds / Meets / Below) each with explanatory text

**Interviewer mode:** A focused full-card flipthrough designed for use *during a live human interview*. Single question per screen, big serif text, side-by-side "Listen for / Red flags", collapsible follow-up probes, scoring footer with three buttons (Exceeds / Meets / Below). Currently a thoughtful future-leaning view; it pairs with the unbuilt Copilot panel for human-led stages.

**SSE-driven freshness:** `useQuestionsStatusStream(jobId, selectedStageId)` keeps the pill statuses and detail panel live as the worker completes generation jobs.

**Confirm-bank validators:** Confirming a bank fires a server-side check. Every knockout signal must have at least one mandatory question probing it (409 `KnockoutUnprobedError` if violated). The sum of mandatory question durations must not exceed the stage's session budget (409 `MandatoryOverrunError`). Both errors give the recruiter specific guidance.

### Phase F — Adding candidates

`/candidates` is the **search/triage** entry. It is intentionally a flat list, not a board.

The header shows the count, filter chips (All / Active / Archived / Hired / Rejected / Withdrawn), a job filter, and a debounced search input. The table columns are Name, Email, Current title, Location, Created, Assignments.

**Adding a candidate** opens `AddCandidateDialog` with fields for name, email, phone, current title, location, LinkedIn, plus a resume upload that uses an S3 pre-signed URL flow (frontend gets a presigned PUT URL from the backend, uploads the file directly to S3, then notifies the backend the upload succeeded). The duplicate-email rule is partial-unique: if a candidate with the same email exists and PII has not been redacted, the create fails with 409.

### Phase G — Tracker (the kanban board)

This is the operational workhorse — the surface a recruiter lives in day-to-day.

**`/tracker` landing page:** A role picker. Card grid of all active roles, plus filter chips (All / Active / In setup). Click any card.

**`/tracker/[jobId]`:** The board.

The kanban container fills the viewport below the header. Width per column is fixed at 320px; the container scrolls horizontally if there are more stages than fit.

**Columns:** One per pipeline stage. Each column header shows the stage name in all-caps and the candidate count. For AI Screening columns, a per-column **Auto-Invite Toggle** (persisted to localStorage per `job+stage`, defaults on). Empty columns show "Drop candidates here" centered.

**Cards:** Each candidate's assignment is a card. Card body shows:
- Avatar with initials in a seeded color (one of six palette tones)
- Name (links to `/candidates/[id]`) and email
- Source chip (if imported from ATS) + Ceipal submission status (if present)
- Two status badges side by side: assignment status (active / archived / hired / rejected / withdrawn) and session status (invited / pre-check / consented / in-progress / completed / etc.)
- Kebab menu (⋮) with "Resend invite (with OTP)" as the single action

**Drag interactions:**
- Pointer sensor with 6px activation distance (prevents accidental drags on click)
- Keyboard sensor wired for accessibility
- Hover state: card lifts 1px with a two-layer shadow
- Drag state: card goes to 35% opacity (placeholder), with a high-elevation `DragOverlay` portaled to `<body>` showing the dragged card at 1.03× scale, 2° rotation, three-layer shadow
- Drop animation: 220ms `cubic-bezier(0.2, 0, 0, 1)` — clean ease-out, no overshoot. A `flushSync` forces the optimistic cache update before the animation, so the overlay lerps to the destination, not back to the source.

**The auto-invite magic:** When a card is dropped into an AI Screening column AND the candidate has no existing session AND the column's auto-invite toggle is on, the system *automatically sends the interview invite with OTP enabled*. Toast: "Invite sent (OTP enabled)". This is the operational shortcut that lets recruiters work at scale — they don't have to click "Send invite" for every candidate.

**One-time dismissible tip banner** appears for first-timers: "Drag a card across columns to advance a candidate. Click a card to open their profile."

### Phase H — Live session monitoring (today: limited)

Once a candidate clicks their invite link and starts their interview, the recruiter currently has only **read-only polling visibility**. The kanban card's session badge updates from "invited" → "in progress" → "completed" via the `latest_session_state` field on the kanban response. There is no live transcript view, no real-time signal feed, and no recruiter intervention capability today.

**The unbuilt AI Copilot panel** is intended to fill this gap — always-on for any human in a session, showing live transcript, signal cards per exchange, next planned bot probe, and a question coverage tracker. The recruiter dashboard has *no* `components/copilot/` directory yet. The static `CopilotBrief` widget on the home page is a *design placeholder*, not a functional surface.

### Phase I — Post-interview review (today: limited)

After the session ends, the engine writes a transcript, the list of questions asked/skipped, total probes fired, knockout failures, and audio tuning data to the `sessions` table. Today there is **no reporting UI** that consumes this. `/reports` is a 4-card placeholder showing what's coming:
- Hiring funnel
- Time-to-hire
- Interviewer calibration
- Offer-accept rate

This is Phase 3D / 4B scope.

### Phase J — Team management & org structure (admin only)

Two admin-only surfaces in `/settings`:

**`/settings/team`** — the people side. Three categories of rows in the members table:
1. **Active users** with role chips per assignment
2. **Pending invites** with Resend / Revoke link-style actions
3. **ATS-imported users** without auth accounts — show a "Send invite" link

Super admins can invite new members via an "Invite team member" card. Each row has a kebab/link area for deactivation (uses `DangerConfirmDialog`).

**`/settings/org-units`** — the structure side. A custom SVG canvas (built on `@dagrejs/dagre` layout, with bespoke pan/zoom and direction toggle) shows the entire org tree. Each node is color-coded by type with a "pressure" indicator (hot/steady/cool, derived from open-role count rolled up the tree). Right-click a node for a context menu (add sub-unit, delete). Below the canvas, a 3-column detail panel for the selected node showing: unit info, metrics, access.

Drilling into a specific unit (`/settings/org-units/[unitId]`) opens a unit-specific detail layout with members, sub-units, profile editing, and delete action. The unit-detail page uses a different sidebar layout (`OrgUnitDetailSidebar`) than the rest of the app — this asymmetry should be evaluated during redesign.

### Phase K — Power-user shortcuts (cross-job views)

Two pages exist as alternate entry points to per-job surfaces:

- **`/pipeline`** — pick any active role, jump directly to its pipeline editor (same component as `/jobs/[id]/pipeline`)
- **`/questions`** — pick any active role, pick any stage, jump to its question bank

These are for recruiters who want to bulk-edit pipelines or questions across multiple roles without navigating through the role detail page each time.

---

## 5. The Candidate Journey

The candidate experience lives at `frontend/session/` (separate app, separate origin). The designer should understand it because:

1. The recruiter sees its outcomes (session state, transcript, scores).
2. Round 2 "panel" interviews bring humans into this surface alongside the AI — the Copilot panel will overlay on this UI.
3. The brand identity must be coherent across both surfaces.

### A. Invite email

The candidate receives a transactional email with:
- Personalised greeting
- Company name + job title + stage name + duration in minutes
- One large CTA button: **"Start pre-check"** → `https://interview.projectx.com/interview/{token}`
- Note that the link is personal and expires (72h)
- One-sentence preview of the consent / OTP / device-check flow

### B. Landing & state machine

The candidate clicks the link. The route is `/interview/[token]`. The shell immediately fires `GET /api/candidate-session/{token}/pre-check`, which loads the session state and routes to one of:

- **Wizard (consent → OTP if required → camera/mic)** — for any non-terminal pre-active state
- **Welcome view ("You're ready to begin" or "Rejoin")** — once cam/mic passes
- **Live session** — once the LiveKit connection is up
- **Completion screen** — when the engine publishes the `session_outcome` attribute
- **Error page (typed by code)** — for invalid / expired / superseded tokens

If the token is invalid before any progress, the candidate sees an inline error in the wizard frame: "This link isn't valid." There is no retry — they must contact the recruiter.

### C. Wizard frame

A minimal centered single-column layout, max 640px wide. A fixed header bar shows the company name and job title. A step progress strip shows the wizard's stages with colored pill segments. The stage label and duration show in all-caps above the heading.

**Step 1 — Consent.** The recruiter-configured consent text renders verbatim in a card. The candidate ticks "I have read and understood…" and clicks **Continue →**. The server records the consent timestamp + user agent (AIVIA compliance — applies to Illinois-based candidates regardless of where the company is incorporated).

**Step 2 — OTP (conditional).** Only when the stage has `otp_required: true`. The card has two phases: (a) "Send code" outline button → triggers email dispatch with a 6-digit code, 60-second resend cooldown (timer survives page reload via `otp_issued_at` from the server). (b) Monospace 6-digit input → "Verify" button. Three attempts; failures show "Invalid code. 2 attempts remaining." in an `aria-live` region.

**Step 3 — Camera & Mic.** A 16:9 video preview, dark background. States: idle → "Test camera & mic" button → prompting (waiting for browser permission) → sampling (live stream + brief noise-floor measurement) → ready ("Camera and mic are working ✓") or denied (with retry button). If the ambient noise is above -30 dBFS, a non-blocking amber warning appears: "Your environment sounds noisy. The interview will still work, but for the cleanest call, find a quieter spot."

### D. Welcome view

After cam/mic passes, the wizard fades and a full-screen welcome view appears. Heading: **"You're ready to begin"** (or "Rejoin your interview" for returning candidates). Subtext: company · job · duration. A pill-shaped monospace uppercase button: **"BEGIN INTERVIEW"**. Click → `POST /start`, which atomically: provisions a LiveKit room, dispatches the AI agent worker, consumes the single-use token, and transitions state to `active`.

### E. Live session

The browser connects to the LiveKit room. The view becomes `AgentSessionView_01`, full viewport.

Three regions:

**1. Tile area (top portion):**
- Agent tile: an animated `AudioVisualizer` (five styles available: bar, wave, grid, radial, aura — currently `bar` default) tied to the agent's audio track. When the chat panel opens, the visualizer shrinks to a 90×90px pip.
- Candidate tile: 90×90px self-view if camera is on. Absent if camera and screen-share are both off.

**2. Transcript panel (animated, behind the tile area):**
Toggled via the chat button in the control bar. When open, shows the running conversation: agent messages left-aligned, candidate messages in right-aligned bubbles, locale-formatted timestamps. While the agent is "thinking", a three-dot indicator animates. Auto-scrolls on new candidate messages.

**3. Control bar (floating pill, bottom):**
Rounded pill (radius 31px) with subtle drop shadow. Left to right:
- **Microphone:** compound toggle (mute/unmute) + device picker chevron. Muted state: red destructive color.
- **Camera:** same compound pattern.
- **Screen share:** simple toggle. Active state: blue-tinted.
- **Transcript:** chat icon. Active state: blue-tinted.
- **END CALL:** far right, red-tinted, monospace uppercase. On mobile reads "END".

Before the first message arrives, a shimmer-animated pre-connect message pulses: **"Agent is listening, ask it a question."**

**Reconnection:** If the connection drops, a backdrop-blurred overlay appears with a spinner and "Reconnecting… Please don't close this tab." 30-second timeout, after which the candidate routes to the `RECONNECT_FAILED` error page.

### F. Session end

The engine publishes a `session_outcome` attribute when the interview ends — from completing all questions, time expiry, candidate clicking END CALL, or a knockout condition firing. The `OutcomeWatcher` observes this attribute and disconnects from the room.

**CompletionScreen:** Plain background, centered:
- **"Thanks for completing your interview."**
- **"You can close this tab. We'll be in touch soon."**

No confetti. No score. No next-steps link. Deliberate restraint.

### G. Error paths

If the candidate hits any error during the live session, they route to a `DisconnectError` screen with a full-screen card showing:
- Title (e.g. "Connection lost", "Interviewer didn't connect", "We didn't hear from you")
- Body explaining what happened
- Error code in small muted text below

No retry buttons. All errors tell the candidate to contact the recruiter.

**Common codes:** `AGENT_NO_SHOW`, `CANDIDATE_UNRESPONSIVE`, `SESSION_ALREADY_STARTED`, `RECONNECT_FAILED`, `DUPLICATE_SESSION`, `TOKEN_EXPIRED`, `REJOIN_RATE_LIMITED`.

### Visual and technical design notes

- **Typography:** Headings in `Fraunces` serif (44px wizard title, 24px step headings, 40px error pages). Body and UI in Inter.
- **Color tokens:** Same `--px-*` token system as the dashboard via duplicated `globals.css`. `data-px-theme="warm-light"` and `data-px-density="comfortable"` on `<html>`.
- **Motion:** Heavy use of `motion/react` (Framer Motion). Control bar slides up from the bottom (0.3s ease-out, 0.5s delay). Transcript fades in (0.3s, 0.2s delay). Pre-connect shimmer pulses (2s loop). The visualizer scales down to its pip with a spring (`stiffness: 675, damping: 75`).
- **Mobile-first:** 640px max wizard width, full-bleed on mobile. Control bar nearly full width (`inset-x-3`). "END CALL" → "END" below md breakpoint.
- **Security:** Different origin from the dashboard. `Referrer-Policy: no-referrer` (prevents JWT-in-URL leakage). `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`. `Permissions-Policy` restricts camera/mic to same-origin. **No analytics, no session replay, no third-party scripts at all.**

---

## 6. Current State — What's Built vs. What's a Gap

### Built and production-quality

| Surface | State |
|---|---|
| Login / invite acceptance / onboarding | ✅ Done. Simple, clean. |
| Roles list with grouping (Blocked / Needs you / In motion / Quiet) | ✅ Done. Three view modes (table / card grid / kanban) — the kanban tab is a "coming next phase" placeholder. |
| New role form | ✅ Done. Lightweight on purpose. |
| JD 3-panel review (signals + raw + enriched) | ✅ Done. The single richest screen in the app. |
| Pipeline editor with drag-to-reorder + stage inspector + participants | ✅ Done. Production-grade dnd-kit usage. |
| Question bank generation + per-question refine/regenerate + interviewer mode | ✅ Done. SSE-driven. |
| Candidates list (search/triage) | ✅ Done. Filters, search, pagination, ATS source chips. |
| Candidate detail (profile / assignments / sessions tabs) | ✅ Done. Read-leaning. |
| Tracker kanban (per-role drag-to-advance with auto-invite) | ✅ Done. Recently shipped. Polished drag animations. |
| Team & access settings (invite / resend / revoke / deactivate) | ✅ Done. Note: uses raw Tailwind zinc colors rather than `px/` tokens — visual inconsistency vs. rest of dashboard. |
| Org units canvas (custom SVG dagre layout, pan/zoom, direction toggle) | ✅ Done. Visually distinctive. |
| Candidate session app: wizard, OTP, cam/mic check, LiveKit live session, completion, error pages | ✅ Done. |
| Real-time SSE on JD status and question bank status | ✅ Done. |
| RBAC nav gating via `isAnyAdmin` predicate + `<AccessDenied />` fallback | ✅ Done. (Backend enforces independently — frontend is UX-only.) |

### Built but not yet wired to real data

| Surface | Issue |
|---|---|
| Dashboard home (`/`) | The entire screen — attention cards, active roles table, today's pipeline chart, activity feed, Copilot brief — is **hardcoded sample data**. The design is complete; the data plumbing is not. **This is the most-visible gap.** |

### Known UX gaps in built surfaces

1. **No "Forgot password" link** on login. Required for production.
2. **No profile editing** — the `/profile` page is read-only. Users cannot change their own name, email, or password from the dashboard.
3. **Top-bar `⌘K` "Search or jump to…" button** is a visible placeholder with no command palette behind it.
4. **Top-bar notification bell** always shows a dot but has no notification center behind it.
5. **`/jobs` kanban tab** is a stub ("Cross-role kanban — coming next phase").
6. **`/settings/integrations`** sidebar link goes to a route that has not been built out yet — the ATS connection management surface is incomplete.
7. **Borderline candidate visual treatment** does not exist anywhere yet — no `StatusBadge` variant, no blocking overlay, no review-required indicator. The product invariant ("never auto-advance / auto-reject") is enforced at the backend, but the recruiter has no UI prompt to act on borderline candidates today.
8. **Team settings table** uses raw `zinc-*` Tailwind colors instead of the `--px-*` tokens — needs design system unification.

### Future scope (planned but unbuilt)

| Surface | Phase | What it is |
|---|---|---|
| **AI Copilot Panel** (`components/copilot/`) | 3D | Always-on side panel during live human-involved sessions: live transcript with speaker labels, real-time signal cards per exchange, the bot's next planned probe (before it fires), question coverage tracker. Must be visually distinct from the main video grid — secondary panel, not overlay. |
| **Reports** (`/reports`) | 3D / 4B | Four planned dashboards: Hiring funnel, Time-to-hire, Interviewer calibration, Offer-accept rate. Backend `reporting` module is stubbed; data exists in the `sessions` table (transcript, questions asked/skipped, probes fired, knockout failures, audio tuning summary). |
| **ATS Integrations management UI** | Concurrent | Surface to connect Ceipal / Greenhouse / Workday accounts, configure polling schedules, view sync logs. Backend `ats` module is stubbed; recruiter dashboard has a "Sync jobs/users from ATS" affordance but no settings page for the connection itself. |
| **Tenant settings / engine config** | Post-arc | Per-tenant configuration of the AI interviewer (agent name, knockout policy: `record_only` vs. `close_polite`). Backend exists; recruiter-side editing UI is post-scope. |
| **Real-time recruiter monitoring during sessions** | Concurrent w/ Copilot panel | Today the recruiter only sees "in progress" via polling. The Copilot panel could double as a live session monitor for the recruiter even when no human is in the room. |
| **Borderline candidate UX** | 3D | Visual treatment in cards/lists/badges. Review prompt in the home dashboard's attention cards. Cannot be designed in isolation from scoring UI. |
| **Command palette** (`⌘K`) | Polish | The placeholder exists. Should support: jump to role, jump to candidate, jump to org unit, recent items. |
| **Notification center** | Polish | The bell exists. Should support: invite accepted, session completed, candidate advanced, ATS sync error. |
| **Profile editing** | Polish | Self-service name change, password change. |
| **"Forgot password"** | Polish | Standard reset email flow. |
| **OAuth / SAML SSO** (Google, Microsoft, Okta, Azure AD) | Phase 4+ | Additive to email/password. |
| **Audit log surface** | Phase 4+ | The backend writes audit rows for `user.invited/invite_resent/invite_revoked/invite_claimed/deactivated`, `org_unit.created/updated/deleted/member_added/member_removed`, `job_posting.status_changed`, `client.onboarding_completed`. Pipeline and question-bank operations are *not* in the audit list — gaps exist if a future "activity" surface is built. |

---

## 7. Design System & Visual Language

### Component library — `components/px/`

The dashboard uses a **hand-rolled primitive library** at `components/px/` built on `@base-ui-components/react`. No shadcn enclave on this surface. The library is intentionally small and consistent.

Primitives in use:
- `Button` — `variant: primary | outline | ghost | destructive`, `size: xs | sm | md`
- `Input`, `Textarea`, `Label`
- `Select` family (Base UI)
- `Dialog` family — `Dialog`, `DialogContent`, `DialogTitle`, `DialogDescription`, `DialogFooter`
- `DangerConfirmDialog` — wraps Dialog with destructive-action confirmation
- `Alert`, `AlertTitle`, `AlertDescription` — `variant: default | destructive | caution`
- `Badge`, `Skeleton`, `Separator`
- `Tooltip` family
- `Tabs`
- `Toaster` (sonner wrapper)

### Color tokens

CSS custom properties on `:root` with `data-px-theme="warm-light"` and `data-px-density="comfortable"`:

| Token | Use |
|---|---|
| `--px-fg`, `--px-fg-2`, …, `--px-fg-5` | Foreground tiers (highest contrast to lowest) |
| `--px-bg`, `--px-bg-2` | Background tiers |
| `--px-surface`, `--px-surface-2` | Card / panel surfaces |
| `--px-accent`, `--px-accent-tint` | Primary brand (warm orange-ish) |
| `--px-hairline` | Subtle dividers and borders |
| `--px-ok`, `--px-caution`, `--px-danger` | Semantic colors + `*-bg` and `*-line` variants for chips |

**Semantic chip classes:** `px-chip` with modifiers: `ok`, `caution`, `danger`, `ai`, `soft`, `human`. Used for role badges, status badges, hiring pressure indicators.

### Typography

- **Serif** (`Fraunces` via `next/font/google`) — used for page titles, section headings, dialog titles, question text (24–44px). Conveys the "considered, professional" tone.
- **Sans-serif** (`Inter`) — body, UI labels, small text.
- **Monospace** (system mono) — numbers, positions, code-like data (e.g. "Q01 of 09 · 14 min remaining"), button labels in the candidate session ("BEGIN INTERVIEW", "END CALL").

### Motion

Used sparingly on the dashboard. GSAP for the question bank's pinned left panel (margin/width animation when ScrollTrigger pins). `motion/react` (Framer Motion) is the candidate session's primary motion library — bar/control-bar entrance, transcript fade, visualizer-to-pip spring.

### Layout primitives

- **AppShell** — sidebar (220px → 54px collapsed, 180ms transition) + 48px sticky top bar + content area. The L-shaped seam between the sidebar and content uses a CSS radial-gradient concave-corner painter — a deliberate design detail.
- **Page max-widths:** 800px (profile, narrow forms), 820px (new role form), 1200px (dashboard home), 1400px (roles list, settings, tracker landing), 1600px (candidates list, tracker board). 3xl breakpoint (1440px) added explicitly for the 3-panel JD review layout.
- **3-panel JD layout:** 220px | 1fr | 380px columns.
- **2-panel pipeline / question bank:** ~380px sidebar | 1fr main.
- **Dialogs:** centered overlay, focus-trapped, focus moved to a target on open (input or close button).
- **Drawers:** slide-in panels for deeper config (e.g. `StageConfigDrawer`).

### Accessibility (current standards)

- All interactive elements keyboard-navigable.
- Semantic HTML (`button`, `nav`, `main`, `section`).
- ARIA labels on icon-only buttons.
- Dialogs and drawers move focus on open via `useEffect` + `ref.current?.focus()`.
- Drag-and-drop has keyboard alternatives via `@dnd-kit`'s `KeyboardSensor` + `sortableKeyboardCoordinates`.
- `aria-live="polite"` for dynamic error messages (e.g. OTP attempt counter).
- Desktop-first viewport target (1280px). Candidate session is mobile-friendly.

---

## 8. Backend-Driven UX Behavior (What the Designer Must Account For)

The designer doesn't write backend code, but the UI must reflect what the backend enforces. The most important behaviors:

### Asynchronous AI work (10–30 seconds per task)

Three operations return `202 Accepted` immediately and run in the background:
1. JD enrichment (`POST /api/jobs/{id}/enrich`)
2. Signal extraction (`POST /api/jobs/{id}/extract-signals`)
3. Question bank generation per stage (`POST /api/jobs/{id}/pipeline/stages/{stage_id}/questions/generate`)

The UI **must** show progress via SSE-driven status updates. Spinners alone are not enough — a 30-second spinner with no feedback is the worst UX in the product.

The two SSE streams currently wired:
- `/api/jobs/{id}/status/stream` — emits `event: status` with `{status, enrichment_status, ...}`. Used on JD review page.
- `/api/jobs/{id}/pipeline/questions/status-stream` — emits three event types:
  - `bank.status_changed` — `{job_id, bank_id, stage_id, status, question_count, total_minutes}`
  - `bank.question_updated` — for in-place edits
  - `pipeline.generation_complete` — `{job_id, succeeded, failed, total}`

### State machine gates

Three state machines drive the UX. Each transition has gates the UI must respect:

**Job posting state machine:**
```
draft → signals_extracting → signals_extracted → signals_confirmed → pipeline_built → active
                          ↓ (failure)
            signals_extraction_failed → signals_extracting (retry)
```
- Edits to JD text are only allowed in `draft`. Editing in any later state returns 409.
- Activating a role requires the activation predicates to all pass — the failure response carries structured `predicates_failed` with `code`, `message`, and optional `stage_id` for each failure.

**Question bank state machine:**
```
not_generated → draft → generating → reviewing → confirmed
                     ↓ (failure)
                    failed → generating (retry)
```
- Editing a confirmed bank auto-reverts it to `reviewing`.
- Confirming requires: every knockout signal has at least one mandatory question, and mandatory question durations fit within the session budget.

**Session state machine:**
```
created → pre_check → consented → active → completed (terminal)
       ↘           ↘           ↘          ↘ error (terminal)
        cancelled   cancelled  cancelled
        (terminal)  (terminal) (terminal)
```
- The recruiter cannot push a session forward or back. The candidate drives all state transitions on the session surface. Recruiter dashboard views are read-only.

### Validation surfacing

- **422** = field-level Pydantic validation failure. Response has `{detail: [{loc: [...], msg, type}]}`. The frontend maps `loc` arrays to React Hook Form field paths via `applyApiErrorToForm(err, form)`. **Every form must do this** — do not just toast a 422.
- **409** = state-machine violation (illegal transition, duplicate, already-started). Response has `{detail: <string>}` or `{detail: {code, message}}`. Surface as an actionable inline message.
- **422 `company_profile_incomplete`** = the org unit (or its ancestry) is missing the company profile. Response includes `{org_unit_id}` so the UI can deep-link to "Edit company profile."
- **422 `activation_predicates_failed`** = pipeline activation gate failed. Each predicate has `{code, message, stage_id?}` — render as a chip-per-failure list so the recruiter can fix each individually.
- **401** = session expired. Global handler signs out, toasts, redirects to `/login`. Concurrent 401s are deduped.

### Real-time vs. polling vs. cache

- **SSE:** JD status (signal extraction, enrichment). Question bank status. Both used on detail pages where progress matters.
- **TanStack Query refetch + invalidation on mutation:** Most pages. Kanban polls naturally via cache `staleTime`.
- **No WebSocket** anywhere today. No live session monitoring (the Copilot panel is the planned home for that).

### Permission UX

- The frontend uses `isAnyAdmin(me)` to gate sidebar items and admin actions. This is **UX only** — the backend re-validates every request.
- Permission strings live in the backend (`app/modules/auth/permissions.py`) — 16 canonical constants like `jobs.view`, `jobs.manage`, `candidates.manage`, `org_units.manage`.
- When a permission check fails server-side, the response is 403 with `{detail: {code: "...", message: "..."}}` — surface as `<AccessDenied />` for whole-page denials or a toast for action-level.

---

## 9. Product Invariants — Do Not Break

These are non-negotiable. The redesign can change the *look*, but never the *behavior*:

1. **Borderline candidates can never be auto-advanced or auto-rejected.** They must go to human review. Today there is no UI for this — the redesign needs to introduce it.
2. **Candidate consent is timestamped before recording.** AIVIA compliance, applies to any Illinois-based candidate regardless of where the company is incorporated. The candidate must check a consent checkbox before the camera/mic step.
3. **Tenant isolation is at the database level.** Every row is scoped to a tenant. The UI does not enforce this; the database does. But the UI must never display data the user shouldn't see — defense in depth.
4. **The candidate session app must not depend on Supabase.** It has no accounts. Token-only auth.
5. **The recruiter dashboard must not depend on LiveKit.** The candidate session is the only LiveKit consumer. The Copilot panel will need a careful boundary here.
6. **PII redaction is irreversible.** A candidate whose PII has been redacted shows "(redacted)" instead of their name. The redaction action is super-admin-only and triggers a confirmation dialog requiring exact-string typing.
7. **Resending an invite supersedes the prior token immediately.** The old link stops working before the new email arrives. The UI must make this clear to the recruiter when they click "Resend."
8. **The "Save and confirm signals" action is a one-way gate.** Editing signals after confirmation creates a new snapshot version but does not roll back the pipeline. The UI uses a clear "this will lock signals" framing.
9. **Activating a role requires the pipeline to be complete.** All question banks confirmed, all `human_interview` stages staffed, all activation predicates green. The activation gate UI must render each failed predicate as an actionable chip.
10. **Stage type changes invalidate question banks.** The `EditCategoryWarningModal` exists for this. Don't bypass it in the redesign.
11. **Drag-to-reorder must have a keyboard alternative.** `@dnd-kit` `KeyboardSensor` is wired today. The redesign must preserve this.
12. **No raw PII in browser logs or third-party telemetry.** Emails, OTP codes, transcripts, JWTs, resume contents are forbidden.

---

## 10. Page Inventory Reference

Quick reference for the designer. All routes under `app.projectx.com`:

### Auth & onboarding
- `/login` — Login (email + password)
- `/invite?token=...` — Invite acceptance (set password)
- `/onboarding` — One-screen company profile wizard (Super Admin first login only)
- `/suspended` — Blocked account landing

### Dashboard
- `/` — **Home** (currently static sample data — biggest design opportunity)
- `/profile` — Read-only user profile + role assignments

### Roles (JD pipeline)
- `/jobs` — Roles list with grouping (Blocked / Needs you / In motion / Quiet), three view modes
- `/jobs/new` — New role form
- `/jobs/[id]` — JD review (3-panel layout: signals + raw JD + enriched JD)
- `/jobs/[id]/pipeline` — Pipeline editor (drag-to-reorder + stage inspector + activation gate)
- `/jobs/[id]/questions` — Per-stage question bank (review mode + interviewer mode)

### Candidates
- `/candidates` — Search & triage list
- `/candidates/[id]` — Candidate detail (profile / assignments / sessions tabs)

### Tracker (kanban)
- `/tracker` — Role picker landing
- `/tracker/[jobId]` — Per-role kanban board with drag-to-advance + auto-invite

### Power-user shortcuts
- `/pipeline` — Cross-role pipeline browser
- `/questions` — Cross-role question bank browser

### Reports (placeholder)
- `/reports` — 4-card "coming soon" page

### Settings
- `/settings/team` — Team management (invite / resend / revoke / deactivate / ATS user sync)
- `/settings/org-units` — Org canvas (custom SVG, dagre layout, pan/zoom)
- `/settings/org-units/[unitId]` — Unit detail (members / sub-units / profile / delete)
- `/settings/integrations` — ATS connection management (route exists, incomplete)

### Candidate session (separate app, separate origin)
- `interview.projectx.com/` — Neutral landing for users without a token link
- `interview.projectx.com/interview/[token]` — The wizard (consent / OTP / cam-mic) → welcome → live session → completion
- `interview.projectx.com/interview/[token]/error?code=...` — Typed error landing pages

---

## 11. Suggested Redesign Priorities

A perspective on where the redesign creates the most product value. The designer should weight these based on the redesign's strategic intent.

### Tier 1 — Highest value

1. **Dashboard home (`/`).** Currently static design with no real data. The "what needs attention today" framing is right; the execution needs to be wired to the kanban, session-state, and signal-status data that the rest of the app already has. The `CopilotBrief` widget is a great concept that needs a real model behind it.
2. **Live session monitoring + Copilot panel (`components/copilot/`).** The single largest unbuilt surface. Designing this well anchors the entire "AI-assisted" value proposition.
3. **Reports (`/reports`).** Backend has the data (`transcript`, `questions_asked`, `total_probes_fired`, `knockout_failures`, `audio_tuning_summary`). The four planned reports define what a recruiter does *after* sessions complete — this is the "I closed the loop" feeling.
4. **Borderline candidate UX.** Across cards, lists, badges, home attention items. This is a core product invariant with no current UI surface.

### Tier 2 — Polish that compounds

5. **Onboarding wizard** — currently one screen. Designer could explore a multi-step welcome that doubles as orientation (e.g. "now let's add your first role" / "now invite a teammate" / "now connect your ATS").
6. **Roles list grouping & filters** — the four-bucket grouping (Blocked / Needs you / In motion / Quiet) is a strong UX concept. The visual treatment could be sharper.
7. **JD 3-panel review** — the densest screen. Currently functional but visually cluttered. The right inspector pattern is good; the source-snippet highlighting is a standout. The Sections Rail could be more elegant.
8. **Pipeline activation gate** — currently a button at the bottom of the stage column. Could be more prominent — this is the "go-live" moment.
9. **Question bank interviewer mode** — already a thoughtful future-leaning view. Pair it with the Copilot panel to make it the primary surface during human interviews.

### Tier 3 — Foundational quality

10. **Visual consistency between Team settings and the rest of the dashboard** (raw zinc vs. px tokens).
11. **Command palette behind `⌘K`** — the affordance is already in the top bar.
12. **Notification center behind the bell** — same.
13. **Profile editing** + **Forgot password**.
14. **Sidebar collapse** — currently animates smoothly; the redesign could explore whether the rail should be auto-collapse on narrower viewports.
15. **Mobile breakpoints on the dashboard** — currently desktop-first (1280px minimum). The candidate session is mobile-ready; the dashboard is not. Decide if mobile/tablet is in scope for the redesign.

### Tier 4 — Brand & motion

16. **Audio visualizer styles in the candidate session.** Five exist; only one is wired. The brand expression during the live session is currently understated.
17. **Org units canvas.** Visually distinctive. The pressure indicators (hot/steady/cool) are a clever signal. Could push further into "live organizational dashboard" territory.
18. **Animation discipline.** Motion is used sparingly today. A clear motion language across the dashboard would unify it.

---

## 12. Where to Look in the Codebase

If the designer (or someone supporting them) wants to verify any claim in this document, the canonical sources are:

**Project-level rules and decisions:**
- `/CLAUDE.md` — Root product context
- `/frontend/app/CLAUDE.md` — Recruiter dashboard rules
- `/frontend/session/CLAUDE.md` — Candidate session rules
- `/backend/nexus/CLAUDE.md` — Backend modules and contracts

**Design system and tokens:**
- `/frontend/app/app/globals.css` — `@theme` tokens and `--px-*` variables
- `/frontend/app/components/px/` — every dashboard primitive

**Key UX files (most informative for the designer):**
- `/frontend/app/components/dashboard/AppShell.tsx` — sidebar nav, top bar, breadcrumb, collapse behavior
- `/frontend/app/app/(dashboard)/page.tsx` — the static-design-only home page
- `/frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx` — 3-panel signal review
- `/frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx` — full question bank UI
- `/frontend/app/components/dashboard/tracker/CandidateKanbanView.tsx` — kanban with auto-invite
- `/frontend/app/app/(dashboard)/settings/org-units/page.tsx` — org canvas with dagre layout
- `/frontend/session/app/interview/[token]/WizardShell.tsx` — pre-interview wizard state machine
- `/frontend/session/components/agents-ui/blocks/agent-session-view-01/components/agent-session-block.tsx` — the live session view (tile + transcript + control bar)

**Architectural specs (deep context):**
- `/docs/superpowers/specs/` — every meaningful design decision is documented here, in chronological design-spec format. Notable ones for the designer:
  - `2026-05-01-frontend-session-extract-design.md` — why the candidate app is a separate origin
  - `2026-05-06-audio-pipeline-design.md` — how the live session audio works
  - `2026-04-28-jd-creation-flow-refinement-design.md` — why the JD flow has two AI calls

---

## 13. Final Notes for the Designer

**ProjectX is an AI-native product, not an AI-bolted-on product.** The AI is in the JD enrichment, the signal extraction, the question generation, the live interview itself, the scoring, the reporting, and the Copilot panel. The recruiter's job is to *steer and verify*, not to *fill out forms*. Every screen should make the AI's work legible — what it found, how confident it is, where it came from, what it's about to do.

**The product invariant is "AI decides, human verifies."** The recruiter is not approving every AI decision; they are spotting the ones that need attention. Borderline candidates, low-confidence signals, mandatory-question coverage gaps, knockout failures — these are the moments the UI must escalate. Everything else should feel like the AI did the work and the recruiter just clicked through.

**Density is fine. Decoration is not.** Recruiters scan a lot of candidates a day. The current UI is data-dense, which is correct. The redesign should preserve that — what it can improve is the *signal-to-noise* within each dense screen.

**Brand voice:** The product talks like a senior, considered colleague. Fraunces serif for headings sets the tone. Microcopy ("Take your time — you can only move forward once each step is complete." / "Be specific — what problems, at what scale, for whom? Not your mission statement.") is conversational without being chummy. Keep that.

**Future-proofing:** The two-tier MVP→Enterprise architecture (root CLAUDE.md) means the UI must work identically whether ProjectX runs on managed Supabase or a client's VPC. The designer doesn't need to think about hosting, but they should know: no design choice should presume a specific backend availability or latency. Loading states matter. Empty states matter. Error states matter — they get used.

Everything else, you can reinvent.
