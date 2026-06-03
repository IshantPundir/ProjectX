# ProjectX — Product Overview (Simple Version)

> **For:** The UI/UX designer redesigning ProjectX.
> **Goal:** Give you a clear picture of what the product does, who uses it, and how it works today — in plain language.
> **Date:** 2026-05-15 (snapshot) · **Corrections appended 2026-06-03**
>
> ⚠️ **Shipped since this snapshot:** the **Reports** page is no longer a placeholder — it's
> a real reports hub + per-session report viewer with scoring, **recording playback**, a
> **ReviewTheater** player, an optional **Candidate Reel** highlight video, and a
> **proctoring** integrity panel. Read the "placeholder / coming soon" notes below as
> historical. The AI bot's name is **Arjun**.

---

## What ProjectX Does

ProjectX replaces the recruiter phone-screen with an **AI-led video interview**.

When a company hires people, the first conversation is usually a 30-minute phone call with a recruiter — "tell me about yourself, what are you looking for, what's your background?" Recruiters spend most of their week doing this. ProjectX automates that call.

**How it works in one paragraph:**
A recruiter pastes a job description into ProjectX. The AI reads it, pulls out the important hiring requirements ("must have 5+ years of Python", "must have led a team"), and writes a structured set of 8–10 interview questions for that role. The recruiter reviews and confirms everything. Then candidates get a link by email, click it, do a quick consent + camera check, and join a real video interview — but the interviewer is an AI. After the interview, the recruiter sees the transcript and a scorecard, and decides whether to move the candidate forward.

**The wedge:**
Replace phone-screens for big companies (Fortune 500) that hire hundreds of people a month. One recruiter can now oversee dozens of interviews running at the same time instead of doing them one at a time.

**What it is NOT:**
- Not a chatbot. The candidate is on a real video call.
- Not an ATS (Applicant Tracking System). It plugs into existing ones like Ceipal, Greenhouse, Workday.
- Not a sourcing tool. Candidates come from manual entry, resume upload, or ATS sync.
- Not generic. Every job gets its own custom-generated interview, based on the actual job description and company context.

---

## Three Apps, Three Audiences

ProjectX is actually three separate web apps:

| App | Who uses it | What they do |
|---|---|---|
| **Recruiter Dashboard** | Recruiters, hiring managers, admins | The main product — set up roles, manage candidates, review interviews |
| **Admin Console** | ProjectX internal staff only | Provision new customer accounts. **Not in scope for redesign.** |
| **Candidate Session** | Job candidates | The interview itself — joins a video call with the AI |

**You're redesigning the Recruiter Dashboard.** The Candidate Session is covered in this doc because it's the other side of the same product, and the brand needs to feel consistent.

---

## Who Uses the Dashboard

Six kinds of users, with different levels of access:

| Role | What they do |
|---|---|
| **Super Admin** | The customer's account owner. Sets up the company, invites the team. There's exactly one per customer. |
| **Admin** | Manages a division or team within the company. Can invite people, create roles. |
| **Recruiter** | The daily driver. Creates job postings, reviews candidates, moves them through interviews. |
| **Hiring Manager** | Reviews candidate reports, makes the final yes/no decision. |
| **Interviewer** | Joins live human panel interviews (with AI help on the side). Future scope. |
| **Observer** | Read-only. Usually a leader who wants visibility but not action. |

**The main user is the Recruiter.** Most screens are designed around them. Admins use the settings pages. Hiring managers and observers mostly read.

---

## Words You'll See Everywhere

You'll see these terms throughout the product. Learn them once:

| Word | Meaning |
|---|---|
| **Role** (or Job) | A specific job opening. "Senior Backend Engineer at Acme." |
| **JD** | Job Description. The pasted text of the role. |
| **Signal** | A specific hiring requirement extracted from the JD by AI. Example: "5+ years Python", "Led team of 8". Marked as must-have or nice-to-have. |
| **Pipeline** | The sequence of interview stages for a role. Like: Intake → Phone Screen → AI Interview → Panel → Debrief. |
| **Stage** | One step in the pipeline. Six types exist (see below). |
| **Question Bank** | The 8–10 questions the AI will ask in a given stage. AI-generated, recruiter-edited, recruiter-confirmed. |
| **Candidate** | A person being considered for one or more roles. |
| **Assignment** | The link between one candidate and one role. A candidate can be assigned to multiple roles. |
| **Session** | One specific live AI interview. Has a start, an end, a transcript, a score. |
| **Org Unit** | A node in the company structure (division, region, team). Used to organize who can see what. |
| **Company Profile** | A short blob about the company (what it does, industry, what a good hire looks like). The AI reads this for context every time it generates something. |
| **AI Copilot Panel** | A future side-panel for live human-led interviews showing transcript, signals, next AI probe. Not built yet. |
| **Borderline** | A candidate the AI couldn't confidently advance or reject. Goes to a human. **Never auto-decided.** |
| **Knockout signal** | A hard requirement (e.g. "must have US work authorization"). Failing it ends the interview early. |

### The six stage types

| Stage type | What it is |
|---|---|
| **Intake** | The first stage. Recruiter screen, paperwork. No questions. |
| **Phone Screen** | A quick human phone call. Has questions. |
| **AI Screening** | The flagship — the AI video interview. Has questions. Sends invite link. |
| **Human Interview** | A panel interview with real humans (with AI help via the Copilot panel). Has questions, has participants. |
| **Take-Home** | An async assignment (code, written exercise). |
| **Debrief** | The final stage. Make the decision. No questions. |

---

## The Recruiter Journey — Step by Step

Here's the path a new recruiter walks the first time they use ProjectX.

### Step 1 — Get the account (one-time)

A ProjectX operator creates the account. The customer's Super Admin gets an invite email, clicks the link, sets a password, lands in the dashboard.

### Step 2 — Onboarding (one-time)

The Super Admin sees a single-screen wizard asking three questions:
- "What does your company actually build or do?"
- "What's your industry?"
- "What does a strong hire look like here?"

This becomes the **Company Profile**. Every AI call after this reads it for context. Without it, nothing can run.

### Step 3 — Create the first Role

Click "Roles" in the sidebar → "New role". Fill out a form (role title, which part of the company, employment type, salary, etc.). Save.

The role now exists in **Draft** state. The recruiter is taken to the role's detail page.

### Step 4 — Add the job description

On the role detail page, the recruiter pastes the raw JD text.

They click "Enrich" — the AI rewrites it into a polished, structured version (this takes 10–30 seconds, with live progress shown on screen).

They click "Extract signals" — the AI reads the JD and pulls out a list of must-haves and nice-to-haves (also takes 10–30 seconds, also shows live progress).

### Step 5 — Review the signals (the biggest screen in the app)

The role detail page becomes a **three-column layout**:

- **Left column** — A list of the signal categories (Must-haves, Nice-to-haves, Snapshot) with counts. Click to scroll.
- **Center column** — The main editor. Three tabs: "Signal details" (the editable list), "Raw JD" (original text), "Enriched JD" (AI-polished version). On the Signal details tab, each signal is a row showing the requirement, how confident the AI is, and which sentence in the JD it came from.
- **Right column** — The inspector. Click any signal on the left, the right panel shows everything about it: source snippet highlighted in context, confidence score, type, weight, edit and remove buttons.

The recruiter reads through, edits anything wrong, then clicks **"Save and confirm signals"**. This is a one-way gate — once confirmed, the signals are locked. A default pipeline gets created automatically.

### Step 6 — Build the pipeline

After confirming signals, the recruiter is sent to the **Pipeline editor**.

If no pipeline exists yet, they pick a starting point: a saved template, a starter pack, or build from scratch.

Once they have stages, the screen is two columns:
- **Left** — A vertical list of stages. Each stage card shows its name, type, duration, and how many people are assigned. Drag handles let the recruiter reorder. A "+ Add stage" button at the bottom.
- **Right** — A stage inspector that opens when you click a card. Two tabs: Configuration (name, type, duration, difficulty, pass criteria) and Participants (who's running this stage — interviewers, reviewers, observers).

At the bottom is the **Activation Gate** — a checklist that decides whether the role can go live. Every stage needs questions confirmed, every human panel stage needs interviewers assigned, etc. When everything's green, "Activate role" turns the job live.

### Step 7 — Generate and review the questions

For each stage, the AI generates a set of interview questions. The recruiter reviews them on the **Question Bank page**.

A row of **stage pills** at the top — one per stage. Each pill has a small icon showing the status: confirmed (✓), generating (•••), needs review (◆), empty (○), failed (✗).

The page has two view modes (a toggle in the top right):

**Review Mode (default)** — A two-column master-detail layout:
- **Left panel (sticky)** — Three live meters at the top showing question count, mandatory count, and minutes used vs. the time budget. Below: a numbered list of questions (01, 02, 03…). At the bottom: "+ Add question" and the **"Confirm bank"** primary button.
- **Right panel** — The selected question's full detail. Big serif question text, signal chips showing which signals this question probes, evaluation hint, "Listen for" + "Red flags" boxes side by side, follow-up probes, and a three-tier rubric (Exceeds / Meets / Below).

**Interviewer Mode** — A focused full-card view designed to be used *during* a live human interview. One question per screen, big text, scoring buttons at the bottom. Built for the future Copilot panel.

The recruiter can "Refine" a question (write a note, the AI rewrites it), "Regenerate" a question (full re-roll), edit text in place, or add a hand-written question. Then click **"Confirm bank"** to lock it in.

### Step 8 — Add candidates

The recruiter goes to the **Candidates page**. This is a flat searchable list — name, email, current title, location, when they were added, what roles they're assigned to.

They click "+ Add candidate", fill out a form (name, email, phone, LinkedIn, upload resume). The resume goes to secure cloud storage. Save.

Candidates can also come in automatically from an ATS (Ceipal, Greenhouse, Workday) — they show up in the list with a small "From Ceipal" chip.

### Step 9 — Move candidates through the pipeline (Tracker — the daily workspace)

The **Tracker** is where the recruiter spends most of their day. It's a Kanban board.

`/tracker` shows a card grid of all roles. The recruiter picks one.

`/tracker/[role]` opens the board for that role. One column per stage. Cards represent candidates.

Each card shows:
- Initials avatar (color-coded per person)
- Name + email
- Source chip if imported from ATS
- Two status badges: assignment status (active / hired / rejected / etc.) and session status (invited / in progress / completed)
- A small ⋮ menu with "Resend invite (with OTP)"

The recruiter **drags cards across columns to advance candidates**.

**The magic feature** — when a card is dropped into an AI Screening column for the first time, **the interview invite is automatically sent**. The recruiter doesn't have to click "Send invite". A toggle at the top of each AI Screening column controls this. Toast appears: "Invite sent (OTP enabled)".

Hover, drag, and drop animations are polished — cards lift slightly on hover, the dragged card floats with a soft shadow and a 2° tilt, the drop animation is smooth and lands cleanly.

### Step 10 — The candidate takes the interview

The candidate gets an email, clicks the link, goes through their wizard (consent → OTP if needed → camera check), and starts the interview. See [The Candidate Journey](#the-candidate-journey) below.

While the interview runs, the recruiter sees the kanban card update from "invited" → "in progress" → "completed".

**Today, that's all the recruiter sees during the live interview.** No live transcript. No real-time signal feed. The **Copilot panel** (future) will fix this.

### Step 11 — Review the results

After the interview, the AI writes a transcript, a list of questions asked and skipped, and any knockout failures.

**Today, there's no UI to view this nicely.** The Reports page is a 4-card placeholder showing what's coming:
- Hiring funnel (stage conversion rates)
- Time-to-hire (median days from signal to offer)
- Interviewer calibration (how each interviewer's scores compare to the panel)
- Offer-accept rate

This is one of the biggest design opportunities.

### Step 12 — Manage the team and structure (admins only)

Two settings pages:

**Team & Access** — A table of all team members. Three categories:
1. Active users (with their role chips)
2. Pending invites (with Resend / Revoke actions)
3. ATS-imported users (with a "Send invite" link)

Super admins can invite new people, deactivate existing ones.

**Org Units** — A visual canvas showing the company's internal structure. Each org unit (company → divisions → regions → teams) is a colored node, connected by lines. The recruiter can pan, zoom, right-click to add sub-units or delete. Each node has a "hiring pressure" indicator (hot / steady / cool) based on how many open roles roll up to it.

---

## The Candidate Journey

The candidate experience is a separate web app. Important context for you because:
1. Recruiters monitor it (today indirectly, tomorrow via Copilot panel).
2. Round 2 panel interviews bring humans into this surface alongside the AI.
3. The brand needs to feel consistent across both apps.

### 1. The invite email

The candidate gets an email with:
- A personal greeting
- Company name, role, stage name, expected duration
- One big button: **"Start pre-check"**
- A note that the link is personal and expires in 72 hours

### 2. The wizard (consent → OTP → camera/mic)

Click the link. A minimal centered layout opens. A header bar shows the company name + role. A step-progress strip shows where they are.

**Step 1 — Consent.** A card with the legal consent text. A checkbox: "I have read and understood…" Click Continue. (This is required by law in some US states. Timestamped.)

**Step 2 — OTP (optional).** If the role requires it: "Send code" button → email arrives with a 6-digit code → monospace input → Verify. 60-second cooldown between resends. 3 attempts max.

**Step 3 — Camera & mic.** A 16:9 video preview. Click "Test camera & mic". The browser asks permission. Stream goes live. The app briefly samples ambient noise and shows a warning if the environment is too loud ("Your environment sounds noisy. The interview will still work, but for the cleanest call, find a quieter spot."). Then a green "Camera and mic are working ✓" + Continue.

### 3. Welcome screen

A full-screen centered view:
- **"You're ready to begin"**
- Company · Role · Duration
- A monospace uppercase pill button: **"BEGIN INTERVIEW"**

Click → the room connects.

### 4. The live interview

A full-viewport view. Three regions:

**Top — The tile area.** The AI interviewer is shown as an animated audio visualizer (a soft pulsing visual that moves with the AI's voice). Five styles exist (only one wired today). The candidate's own camera shows as a small 90×90px self-view tile.

**Behind it — The transcript panel.** Hidden by default. Toggle from the control bar. When open: a chat-style scrollable history with timestamps. While the AI is thinking, three animated dots show.

**Bottom — The control bar.** A floating rounded pill at the bottom with:
- Mic toggle (with device picker)
- Camera toggle (with device picker)
- Screen share toggle
- Transcript toggle
- **END CALL** button (red)

If the connection drops, a blurred overlay appears: "Reconnecting… Please don't close this tab." 30-second timeout.

### 5. The completion screen

When the interview ends — whether by completing all questions, time running out, or the candidate clicking END CALL — the candidate sees:

- **"Thanks for completing your interview."**
- **"You can close this tab. We'll be in touch soon."**

No confetti. No score. No next-steps link. Deliberate restraint.

### 6. Errors

If something goes wrong (link expired, link already used, connection failed, the AI never connected), the candidate gets a clean error page with a title, an explanation, and a small error code. **No retry buttons.** All errors tell the candidate to contact the recruiter.

---

## Current State — What's Done, What's Half-Done, What's Coming

### Done and working well

- Login, invite acceptance, onboarding
- Roles list (with smart grouping: Blocked / Needs you / In motion / Quiet)
- New role form
- JD review (3-panel layout)
- Pipeline editor (drag to reorder, stage inspector, activation gate)
- Question bank (review + interviewer modes)
- Candidates list
- Candidate detail (profile / assignments / sessions tabs)
- **Tracker kanban** (just shipped, polished drag animations)
- Team & access settings
- Org units canvas
- The entire candidate session app (wizard, OTP, cam/mic, live interview, completion, errors)

### Built but using fake data (biggest visible gap)

- **Dashboard home page.** The entire landing screen — the attention cards, the active roles table, the activity feed, the Copilot brief — is hardcoded sample data. The design exists. The data wiring doesn't. This is the most-visible blank canvas for the redesign.

### Missing in current screens (small fixes)

- No "Forgot password" link on login.
- The profile page is read-only. You can't change your own name or password.
- The `⌘K` "Search or jump to" button is a visual placeholder — there's no command palette behind it.
- The notification bell always shows a dot — there's no notification center behind it.
- The Team settings table uses a slightly different color palette than the rest of the dashboard.
- The Borderline candidate visual treatment doesn't exist — but the product invariant requires one.

### Coming later (you should design with these in mind)

| Feature | What it is |
|---|---|
| **AI Copilot Panel** | The most important upcoming feature. A side panel that runs alongside live human-led interviews. Shows live transcript, signal cards per exchange, what the AI is about to ask next, and a coverage tracker. Should be visually distinct from the video grid. |
| **Reports page** | Four planned dashboards (hiring funnel, time-to-hire, interviewer calibration, offer-accept rate). Data already exists in the system. |
| **ATS integrations UI** | A settings page to connect Ceipal, Greenhouse, Workday accounts. |
| **Tenant settings** | Per-customer config of the AI interviewer (the AI's name, what it does when a knockout signal fails). |
| **Borderline candidate UX** | Visual treatment in cards, lists, badges, and home dashboard attention items. |
| **Command palette** | Behind the `⌘K` button. Jump to anything fast. |
| **Notification center** | Behind the bell. |
| **Profile editing** | Self-service name and password change. |
| **OAuth / SSO** | Google, Microsoft, Okta, Azure AD login. |
| **Audit log surface** | View who did what, when. |

---

## Design System Today

### Colors

The app uses a warm-light token palette. The brand accent is a warm orange-ish color. The semantic colors are:
- **OK / success** — soft green
- **Caution** — amber
- **Danger** — red
- **AI** — accent-tinted (a slightly different chip color used when something is AI-authored)

Backgrounds are layered (a slightly lighter background tier, a card surface tier, a lifted surface tier). Foreground colors are tiered from highest contrast (page titles) to lowest (subtle hints).

### Typography

- **Fraunces serif** — used for page titles, section headings, dialog titles, and question text. Conveys "thoughtful, considered, premium." Sizes: 24–44px.
- **Inter** — body, UI labels, small text.
- **Monospace** — numbers, positions, code-like data (Q01 of 09 · 14 min remaining), and button labels in the candidate session (BEGIN INTERVIEW, END CALL).

### Components

The dashboard has its own small component library called `px/`. Hand-rolled, no shadcn. The key pieces:
- Buttons (primary / outline / ghost / destructive)
- Inputs, textareas, labels
- Selects, dialogs, tooltips, tabs, alerts, badges, skeletons
- A specialized danger-confirm dialog (for destructive actions)
- A toast system

### Layout

- Sidebar — 220px wide, can collapse to 54px. Smooth 180ms transition.
- Top bar — 48px, sticky.
- Page widths — 800px (profile, narrow forms), 1200px (home), 1400px (settings, lists), 1600px (candidates, kanban).
- Dialogs and drawers are common patterns. Drawers slide in from the right for deeper config.

### Motion

Used sparingly on the dashboard. Heavier on the candidate session (control bar entrance animation, transcript fades, the audio visualizer shrinking to a small pip when the chat opens).

### Accessibility

The app has decent a11y today — keyboard navigation everywhere, focus management in dialogs, drag-and-drop has keyboard alternatives, ARIA labels on icon-only buttons. Preserve this.

### Desktop-first

The dashboard targets 1280px+ viewports. The candidate session is mobile-friendly (people may take interviews from their phone). **Decide if mobile/tablet dashboard is in scope for the redesign.**

---

## Things the Redesign Cannot Break (Product Invariants)

These are non-negotiable. You can change the look completely, but not the behavior:

1. **Borderline candidates never auto-advance or auto-reject.** They always go to a human.
2. **Candidate consent is required and timestamped** before any recording starts.
3. **PII redaction is irreversible.** Once a candidate's data is wiped (legal request), it's gone. The UI should make this clear before the action.
4. **Resending an interview invite immediately invalidates the old link.** The recruiter needs to understand this when they click Resend.
5. **"Save and confirm signals" is a one-way gate.** Once confirmed, the pipeline can be built. Going back creates a new version, doesn't undo.
6. **Activating a role requires the pipeline to be complete.** Show each missing item as a fixable chip.
7. **Drag-to-reorder must work with the keyboard.** Today it does. Keep it that way.
8. **No personal data (emails, OTP codes, transcripts) in browser logs or third-party tools.**

---

## Quick Map of All Pages

### Sign in & setup
- `/login` — Sign in (email + password)
- `/invite` — Accept an invite, set password
- `/onboarding` — Company profile wizard
- `/suspended` — Account-blocked landing

### Main app
- `/` — Home (where the recruiter lands)
- `/profile` — Your own profile (read-only)

### Roles (jobs)
- `/jobs` — Roles list
- `/jobs/new` — New role form
- `/jobs/[id]` — Role detail (the 3-panel JD review)
- `/jobs/[id]/pipeline` — Pipeline editor
- `/jobs/[id]/questions` — Question bank

### Candidates
- `/candidates` — Search & triage list
- `/candidates/[id]` — Candidate detail

### Tracker (kanban)
- `/tracker` — Role picker
- `/tracker/[role]` — Kanban board

### Power-user shortcuts
- `/pipeline` — Jump-to-pipeline across all roles
- `/questions` — Jump-to-questions across all roles

### Reports (placeholder)
- `/reports` — Coming-soon page

### Settings
- `/settings/team` — Team management
- `/settings/org-units` — Org structure canvas
- `/settings/org-units/[unit]` — Unit detail
- `/settings/integrations` — ATS connections (incomplete)

### Candidate session (separate app)
- Landing for tokenless users
- `/interview/[token]` — Wizard → live interview → completion
- Error pages by code

---

## Where the Biggest Design Opportunities Are

If I had to rank them:

### Highest value

1. **Dashboard home page.** Currently fake data. Wire it up. The "what needs your attention today" framing is right — execute it.
2. **AI Copilot Panel.** The biggest unbuilt surface. This is where ProjectX shows off being AI-native. Live transcript, real-time signals, what the AI is about to ask next.
3. **Reports page.** The data exists. The UI doesn't. This closes the loop after every interview.
4. **Borderline candidate UX.** A clear visual language across cards, lists, badges. The product needs it.

### High value polish

5. Onboarding could be richer — make it feel like an introduction to the product.
6. JD review (the 3-panel layout) is functional but dense. The signal source-snippet highlighting is a standout — push that further.
7. The pipeline activation gate could be more prominent — this is a "go-live moment".
8. Question bank interviewer mode is a great future-leaning view. Pair with the Copilot panel.

### Foundational

9. Visual consistency (especially the Team settings page).
10. Command palette behind `⌘K`.
11. Notification center behind the bell.
12. Profile editing and Forgot password.

### Brand & motion

13. The candidate session has five audio visualizer styles built — only one is wired. Brand expression during the live interview is currently understated.
14. A cleaner motion language across the dashboard would unify it.

---

## Final Notes

**ProjectX is AI-native, not AI-bolted-on.** The AI is in every screen — pulling signals, writing questions, asking the interview, scoring answers, writing reports. The recruiter's job is to *steer and verify*, not to fill out forms. Every screen should make the AI's work legible — what it found, how confident it is, where it came from, what it's about to do.

**"AI decides, human verifies."** The recruiter doesn't approve every AI decision; they spot the ones that need attention. Borderline candidates, low-confidence signals, missing question coverage, knockout failures — these are the moments the UI must escalate. Everything else should feel like the AI did the work and the recruiter just clicked through.

**Density is fine. Decoration is not.** Recruiters scan a lot of candidates a day. The current UI is data-dense, which is correct. The redesign should keep that — what it can improve is the *signal-to-noise ratio* within each screen.

**Brand voice.** The product talks like a senior, considered colleague. Microcopy is conversational without being chummy ("Take your time — you can only move forward once each step is complete." / "Be specific — what problems, at what scale, for whom? Not your mission statement."). Keep that tone.

**Loading states matter. Empty states matter. Error states matter.** AI work takes 10–30 seconds. Empty pipelines, empty candidate lists, failed extractions — they all happen often. Design these screens like they're first-class, not afterthoughts.

Everything else, you can reinvent.
