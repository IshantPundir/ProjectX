# Signal Schema v2 + Job Metadata

> **Status:** Approved design — ready for implementation planning
> **Date:** 2026-04-11
> **Depends on:** Phase 2B (Signal Editing & Confirmation) — shipped
> **Prerequisite for:** Phase 2C (Question Bank Generation & HM Review)

---

## What This Delivers

Migrates the signal extraction schema from 4 rigid JSONB columns (`required_skills`, `preferred_skills`, `must_haves`, `good_to_haves`) to a **single universal flat list** where each signal item carries rich metadata (type, priority, weight, knockout, stage, evaluation method). Also adds structured recruiter-provided job metadata fields for screening context (salary, work arrangement, location, etc.).

This is the foundational schema change that enables Phase 2C's two-round question bank architecture (phone screen + deep interview).

---

## Core Product Principle

**AI decides, human verifies.** The AI autonomously determines ALL signal attributes from the JD context. Every field is also recruiter-editable so mistakes can be corrected. No feature should require manual input before the system can function — always have an AI-generated starting point.

---

## Signal Item Schema (v2 — Universal)

Each signal is a single JSON object with 10 fields across 4 groups:

### WHAT
| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `value` | string (min 1 char) | free text | The signal itself: "Python", "5+ years backend", "Stakeholder management" |
| `type` | enum | `competency`, `experience`, `credential`, `behavioral` | What kind of signal (determines question format) |

### Type Definitions

```
competency  — A demonstrable skill or knowledge area that can be probed
              with depth questions. Technical or domain-specific.
              Examples: "Python", "System Design", "SEO/SEM", "Consultative Selling"

experience  — A verifiable background requirement. Time-based, scope-based,
              or achievement-based. Checked via direct questions.
              Examples: "5+ years backend", "Led team of 5+", "Managed $1M+ budget"

credential  — A formal qualification: degree, certification, license, clearance.
              Binary — candidate either has it or doesn't.
              Examples: "CS degree", "AWS Solutions Architect", "Series 7 license"

behavioral  — A soft skill or trait observable through situational questions.
              NOT generic fluff ("team player") — must be role-specific and probeable.
              Examples: "Mentoring junior engineers", "Cross-functional stakeholder management"
```

### HOW IMPORTANT
| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `priority` | enum | `required`, `preferred` | Must-have vs nice-to-have |
| `weight` | enum | `1`, `2`, `3` | Relative importance: 1=baseline, 2=important, 3=critical |
| `knockout` | boolean | true/false | If true, failing this signal = auto-reject in screening |

### Weight Scale

```
weight=3 — Critical. The CORE hiring criterion. Appears prominently in JD title,
           first paragraph, or described with most detail. 1-2 signals per job max.
           Session AI: ~8 min, deep multi-probe. Score: 3x multiplier.

weight=2 — Important. Standard required or preferred signal. Mentioned clearly
           but not the primary focus. Most signals are weight=2.
           Session AI: ~4 min, standard depth. Score: 2x multiplier.

weight=1 — Baseline. Table-stakes at this seniority level. Mentioned briefly,
           often in a bullet list. Required but not a differentiator.
           Session AI: ~2 min, quick check. Score: 1x multiplier.
```

### Knockout Rules

```
Mark knockout=true when:
  - JD uses non-negotiable language: "must have", "required", "mandatory"
  - Credentials that are legally required (licenses, clearances)
  - Hard experience thresholds stated as minimum requirements

Do NOT mark knockout for:
  - Preferred/nice-to-have signals
  - Soft skills or behavioral traits (never binary pass/fail)
  - Technology competencies (depth matters, not binary yes/no)
```

### WHEN & HOW
| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `stage` | enum | `screen`, `interview` | Which interview round probes this signal |
| `evaluation_method` | enum | `depth_probe`, `verification`, `situational`, `case_study` | How to assess (derived from type+stage, recruiter-overridable) |

### Stage Assignment

```
screen    — Credentials, basic experience checks, knockout signals, and any
            signal verifiable with a direct question in under 1 minute.

interview — Competencies needing depth probing, behavioral traits needing
            situational questions, any signal where QUALITY of knowledge
            matters, not just possession.
```

### Evaluation Method Defaults (derived from type + stage)

| type | stage=screen | stage=interview |
|------|-------------|-----------------|
| `competency` | `verification` | `depth_probe` |
| `experience` | `verification` | `situational` |
| `credential` | `verification` | `verification` |
| `behavioral` | `verification` | `situational` |

Recruiter can override any of these. The AI does NOT set `evaluation_method` — it is always derived from the defaults above. Recruiter overrides are persisted.

### PROVENANCE
| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `source` | enum | `ai_extracted`, `ai_inferred`, `recruiter` | Who produced this signal |
| `inference_basis` | string or null | free text | Required when source=ai_inferred, null otherwise |

### EVALUATION (populated later by Call 3)
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `evaluation_hint` | string or null | null | What "good" looks like — filled by Call 3, editable by recruiter |

---

## Database Schema Changes

### `job_postings` — new metadata columns

All nullable. Recruiter fills what they know at job creation time. AI does not touch these.

| Column | Type | Values | Notes |
|--------|------|--------|-------|
| `employment_type` | TEXT + CHECK | `full_time`, `part_time`, `contract`, `internship` | |
| `work_arrangement` | TEXT + CHECK | `remote`, `hybrid`, `onsite` | |
| `location` | TEXT | free text | Required if hybrid/onsite (enforced in app, not DB) |
| `salary_range_min` | INTEGER | | Annual, in smallest currency unit |
| `salary_range_max` | INTEGER | | |
| `salary_currency` | TEXT + CHECK | `INR`, `USD`, `EUR` | Required if salary provided |
| `travel_required` | TEXT + CHECK | `none`, `occasional`, `frequent` | |
| `start_date_pref` | TEXT + CHECK | `immediate`, `within_30_days`, `within_90_days`, `flexible` | |

**Salary is internal-only.** Never surfaces in the enriched JD, even if the raw JD doesn't mention it. Used by the phone screen AI to verify candidate expectations.

### `job_posting_signal_snapshots` — column changes

**Drop:**
- `required_skills` (JSONB)
- `preferred_skills` (JSONB)
- `must_haves` (JSONB)
- `good_to_haves` (JSONB)
- `min_experience_years` (INTEGER) — becomes a signal item with `type="experience"`

**Add:**
- `signals` (JSONB NOT NULL DEFAULT '[]') — flat list of signal items

**Keep unchanged:**
- `id`, `tenant_id`, `job_posting_id`, `version`
- `seniority_level` (TEXT NOT NULL) — classification, not a probeable signal
- `role_summary` (TEXT NOT NULL) — context, not a probeable signal
- `prompt_version`, `confirmed_by`, `confirmed_at`, `created_at`

### No data migration needed

Database has zero snapshots (all test data deleted). Clean drop + add. No dual-write, no backfill.

### Enum implementation

Enums are Pydantic `Literal` types on the Python side. DB stores as `TEXT` with CHECK constraints (not Postgres ENUM types — those are painful to alter). Validation lives in Pydantic; the DB CHECK is a safety net.

---

## Call 1 Prompt Redesign

The prompt at `prompts/v1/jd_enhancement.txt` is rewritten to produce the new signal schema. Key changes:

### What stays the same
- Dual-audience rule (enriched JD serves AI downstream + candidates)
- Canonical 7-section JD structure
- Context-before-document ordering in user messages
- Provenance rules (ai_extracted vs ai_inferred with inference_basis)
- Soft skills rule (strip generic fluff from signals)
- Sections to preserve verbatim (benefits, compensation, legal text)

### What changes
- Output `signals` as a flat list instead of 4 named arrays
- Each signal item includes: value, type, priority, weight, knockout, stage, source, inference_basis
- AI does NOT set `evaluation_method` (derived from type+stage defaults)
- AI does NOT set `evaluation_hint` (populated by Call 3)
- `min_experience_years` is no longer a standalone field — it becomes a signal item with `type="experience"`, e.g. `{ value: "5+ years backend development", type: "experience", ... }`
- Prompt includes explicit definitions for type, weight, knockout, and stage
- Prompt includes coverage requirements (minimums per type/stage)

### Coverage requirements (enforced in Pydantic schema)
- At least 5 signals total
- At least 1 signal with `stage="screen"`
- At least 1 signal with `stage="interview"`
- At least 1 signal with `type="competency"`
- No more than 5 knockout signals (prevent over-flagging)

### Call 2 prompt
`prompts/v1/jd_reenrichment.txt` is also updated to reference the new flat signal list format instead of the 4-column structure.

---

## Job Creation Form — New Fields

The frontend job creation form (`/jobs/new`) adds these fields below the existing ones:

| Field | UI Component | Required? |
|-------|-------------|-----------|
| Employment Type | Select dropdown | No |
| Work Arrangement | Select dropdown | No |
| Location | Text input (shown when hybrid/onsite selected) | Conditional |
| Salary Range | Two number inputs (min/max) + currency select | No |
| Travel Required | Select dropdown | No |
| Preferred Start Date | Select dropdown | No |

All optional — the recruiter fills what they know. The form works with zero metadata (backward compatible with current flow).

---

## Frontend Signal Panel Redesign

### Display grouping

Primary group by **stage** (Screen / Interview), secondary by **type** within each stage:

```
┌─ PHONE SCREEN ──────────────────────────────────┐
│  Knockouts (knockout=true)                        │
│  [⛔ 5+ years backend] [⛔ CS degree]            │
│                                                    │
│  Credentials & Experience (verification)           │
│  [🏷 AWS cert] [🏷 Led team of 5+]              │
│                                                    │
│  Quick Competency Check                            │
│  [🔵 Python] [🔵 SQL]                            │
├─ DEEP INTERVIEW ────────────────────────────────┤
│  Core Competencies (weight shown as stars)         │
│  [🔵★★★ System Design] [🔵★★ Python]            │
│                                                    │
│  Behavioral (situational)                          │
│  [🟢 Mentoring] [🟢 Cross-functional collab]     │
└──────────────────────────────────────────────────┘
```

### Edit mode additions

The existing edit panel (from 2B) gains:
- **Type selector** per chip (dropdown: competency/experience/credential/behavioral)
- **Weight selector** per chip (1/2/3 shown as dots or stars)
- **Knockout toggle** per chip (checkbox or toggle)
- **Stage selector** per chip (screen/interview — or drag between sections)
- **Evaluation method override** (dropdown, only shown when recruiter wants to change the default)

The chip itself visually encodes: provenance color (blue/amber/green) + weight indicator (stars/dots) + knockout badge (⛔ if true).

---

## Backend API Changes

### Updated request/response schemas

**`SignalItemResponse`** — gains: type, priority, weight, knockout, stage, evaluation_method, evaluation_hint

**`SignalSnapshotResponse`** — changes from 4 list fields to: `signals: list[SignalItemResponse]`, keeps `seniority_level`, `role_summary`, `confirmed_by`, `confirmed_at`, `version`

**`SignalItemInput`** — gains: type, priority, weight, knockout, stage. Keeps validation: ai_inferred requires inference_basis. Adds validation: recruiter source must have inference_basis=null.

**`SaveSignalsRequest`** — changes from 4 list fields to: `signals: list[SignalItemInput]`, keeps `seniority_level`, `role_summary`

**`JobPostingCreate`** — gains: employment_type, work_arrangement, location, salary_range_min, salary_range_max, salary_currency, travel_required, start_date_pref. All optional.

**`JobPostingWithSnapshot`** — gains the same metadata fields for display.

**`JobPostingSummary`** — no change (list view doesn't need metadata or signals).

### Endpoint changes

No new endpoints. Existing endpoints updated to handle new schema:
- `POST /api/jobs` — accepts new metadata fields
- `GET /api/jobs/{id}` — returns metadata + new signal shape
- `PATCH /api/jobs/{id}/signals` — accepts new signal shape
- `POST /api/jobs/{id}/signals/confirm` — unchanged behavior
- `POST /api/jobs/{id}/enrich` — Call 2 uses new signal format

---

## Files Changed (19 total)

### Backend (12 files)
| File | Change |
|------|--------|
| Alembic migration (new) | Drop 4 signal columns + min_experience_years, add `signals` JSONB, add job metadata columns |
| `app/models.py` | Update `JobPostingSignalSnapshot` + `JobPosting` columns |
| `app/ai/schemas.py` | `ExtractedSignals` → flat `signals: list[SignalItem]` with new fields + coverage validators |
| `app/modules/jd/schemas.py` | Update all request/response schemas, add job metadata fields to `JobPostingCreate` |
| `app/modules/jd/actors.py` | Update `_persist_enriched`, `_build_reenrich_user_message` for flat list |
| `app/modules/jd/service.py` | Update `save_signals`, `create_job_posting` for new fields |
| `app/modules/jd/router.py` | Update `_snapshot_to_response`, `_job_with_snapshot_to_response`, `create_job` for metadata |
| `prompts/v1/jd_enhancement.txt` | Full rewrite for new signal schema |
| `prompts/v1/jd_reenrichment.txt` | Update for flat signal list format |
| `tests/test_jd_signals.py` | Rewrite assertions for new schema |
| `tests/test_jd_actor.py` | Update fake extraction output |
| `tests/test_ai_schemas.py` | Update validation tests |

### Frontend (7 files)
| File | Change |
|------|--------|
| `lib/api/jobs.ts` | Update `SignalItem`, `SignalSnapshot`, `SaveSignalsBody`, add metadata types |
| `stores/job-edit.ts` | Change from section-based to flat list editing |
| `components/dashboard/jd-panels/SignalsPanel.tsx` | Group by stage→type instead of 4 hardcoded sections |
| `components/dashboard/jd-panels/EditableSignalsPanel.tsx` | Add type/weight/knockout/stage controls per chip |
| `components/dashboard/jd-panels/SignalsPanelWrapper.tsx` | Update save payload construction |
| `components/dashboard/jd-panels/SignalChip.tsx` | Encode weight + knockout visually |
| `app/(dashboard)/jobs/new/page.tsx` | Add metadata form fields |

---

## Non-Goals (deferred)

- **`evaluation_hint` population** — null until Phase 2C (Call 3)
- **Question bank generation** — Phase 2C
- **Two-round question bank tables** — Phase 2C
- **HM review workflow** — Phase 2C
- **Session configuration UI** — Phase 2C
- **`min_experience_years` backward compatibility** — no existing data, clean migration
- **Prompt version bumping** — stays v1 until prompt quality is validated
