# Report Review Theater — Design

**Date:** 2026-05-30
**Status:** Approved (brainstorm) → ready for implementation plan
**Surfaces:** `frontend/app` (recruiter dashboard) · `backend/nexus` (interview_engine, vision, reporting, storage)

---

## 1. Problem & Goal

Recruiters screen hundreds of candidates and cannot watch full session recordings. The current report page shows a small, plain `<video>` with an inline transcript rail. We are replacing it with an immersive, click-to-open **Review Theater**: a calm, light glassmorphic popup whose centerpiece is a **scannable session timeline of cards** — letting a recruiter understand what a candidate did right/wrong and where integrity concerns occurred in ~10 seconds, then jump straight to those moments.

**Inspiration:** `tmp/original-46c499923c5871f8de1d73c0b887e487.webp` (bottom timeline-with-cards). Grounded to our real data and constraints below.

### Goals
- A "Play" poster on the report page opens a large theater popup over a frosted backdrop.
- Aesthetic: **light, calm, welcoming** frosted-white glass (daylight palette) — explicitly *not* dark.
- The timeline lets recruiters scan question outcomes + proctoring integrity and click-to-seek any moment.
- Authoritative, server-side data: real per-question timestamps and server-generated thumbnails on R2. **No client-side canvas frame-grabs or other workarounds.**

### Non-goals
- No change to scoring/verdict semantics or report content (we surface existing data better).
- No redesign of the rest of the report page (only the player area becomes the poster).
- No video-timeline offset re-calibration (we stay consistent with how proctoring already aligns; see §7).

---

## 2. The Data (verified against session `ee1e6683-…`)

The theater is fed by three already-existing endpoints, all already fetched on the report page:

| Source | Endpoint | Carries |
|---|---|---|
| Report | `GET /api/reports/session/{id}` (`ReportRead`) | verdict, dimension scores, per-question scorecards (status, candidate quote, our read), decision/strengths/concerns |
| Recording | `GET /api/reports/session/{id}/recording` (`RecordingPlayback`) | presigned R2 video URL, duration, transcript (`role`,`text`,`t_ms`), `offset_ms` |
| Proctoring | `GET /api/reports/session/{id}/proctoring` (`ProctoringAnalysis`) | `risk_band`, `detector_summary`, `flagged_intervals` (`start_ms`,`end_ms`,`kind`,`confidence`), `gaze_heatmap.off_screen_timeline` |

**Gaps this design closes (backend work):**
1. Question scorecards have **no timestamp**, and transcript entries have `question_id = null` → we cannot place a question card on the video timeline.
2. There are **no thumbnails** for the filmstrip cards.

---

## 3. UX / Visual Design (frontend)

### 3.1 Entry point — the poster
The current inline `SessionPlayback` area becomes a **poster**: a still frame (first thumbnail / soft gradient fallback) with a large play button and a verdict overlay. Clicking it opens the theater. (Decision: *Poster + play button*, not a small inline player.)

### 3.2 Theater layout (decision: top-summary + right-detail + bottom-timeline)
A full-screen overlay with a **light frosted backdrop** that blurs/dims the report behind it. Inside:

- **Top summary bar (static):** candidate identity · dimension score gauges (Overall / Technical / Communication; Behavioral only if assessed) · integrity-risk chip · verdict chip · close. These are whole-session values that never change, so they live up top.
- **Stage (center):** the `<video>` (presigned R2 URL) with playback controls.
- **Right "This moment" panel (live):** the time-anchored detail surface. Default state shows the **overall decision summary** (headline + why-positive / why-negative). Selecting a timeline item swaps it to that item's detail:
  - *Question selected* → status badge + question text + candidate's quoted words + "our read" + "Jump to mm:ss".
  - *Proctoring flag selected* → the integrity detail (kind, time range, confidence) + its thumbnail.
- **Bottom timeline (the centerpiece) — three linked layers:**
  1. **Filmstrip:** equal-width question cards in a horizontally-scrollable row (browse layer). Each card: thumbnail · `Q#` · short title · status badge (green pass / amber partial / red fail) · timestamp. Click → seek + select.
  2. **Node track:** the scrubber/playhead, with a colored node per question at its **true** `asked_at_ms` (precision/seek layer). Card↔node linked by color + hover.
  3. **Integrity lane:** a continuous proctoring **density heatmap** (from `off_screen_timeline` / `flagged_intervals`). Top/most-severe flags are clickable markers → right panel.

  *Why filmstrip + node track instead of pinning cards to timestamps:* fixed card width vs. variable session length and clustered questions make absolute-positioned cards overlap and break at 8–10 questions. Decoupling **browse** (filmstrip) from **time** (node track) scales to any count, never overlaps, and preserves true timing — matching what the reference mockup actually does.

- **Transcript toggle (secondary):** an optional "Transcript" view reusing the existing clickable transcript rail (click a line to seek). Default view is the timeline; the transcript data is already loaded, so this is low-cost. *(Flag for user confirmation — included as low-priority.)*

### 3.3 Styling
- Light frosted-white glass panels (`rgba(255,255,255,~0.65)` + `backdrop-filter: blur`), soft borders, soft shadows, over a bright warm backdrop.
- Colors from the existing daylight theme tokens in `app/theme.css` (`--px-ok` teal-green, `--px-caution` amber, `--px-danger` coral, `--px-accent` teal/cyan). New `theater.css` owns glass + timeline styles and animations.

### 3.4 Graceful degradation
- Legacy sessions (no `question_id` in transcript, no thumbnails): cards show the soft color+status with no thumbnail; nodes fall back to even spacing when `asked_at_ms` is null. The theater remains fully usable.
- Recording / proctoring still processing: existing polling states apply; timeline layers fill in progressively.

---

## 4. Backend Architecture

Three well-bounded module changes plus a shared storage helper and one new table.

### 4.1 interview_engine — the authoritative time anchor
**File:** `app/modules/interview_engine/agent.py`
Populate `TranscriptEntry.question_id` (a field that already exists but is always `None`) on **question-bearing agent turns**, sourced from `self._brain.active_question_id` (the brain already tracks this; exposed at `brain/service.py`). Also tag **candidate turns** while a question is on the floor, so the transcript can be grouped/attributed by question.

- "Question-bearing" = directive acts that put/keep a question on the floor (ASK / ACK_ADVANCE / PROBE / CLARIFY / REDIRECT). Filler/meta turns stay `None`.
- This persists automatically to `sessions.transcript` via `record_session_result` (no schema change).
- **Human review required** (engine touch). Test: assert `question_id` is populated for delivered questions in a session run.

`asked_at_ms` for a question = the **minimum `timestamp_ms`** among agent transcript entries bearing that `question_id`. Derived identically by both the reporting builder and the vision job from the one source of truth (`sessions.transcript`).

### 4.2 storage — server-side upload helper
**File:** `app/storage/s3.py`
Add `upload_bytes(key, data: bytes, content_type: str)` to `ObjectStorage` / `S3CompatibleStorage` (boto3 `put_object` via `asyncio.to_thread`, mirroring `download_to_path`). Reuse the existing `recording_storage_*` bucket/credentials/client (`get_object_storage()`).
**Config:** add `thumbnail_key_prefix: str = "thumbnails"`. Reuse `recording_signed_url_ttl_seconds` for presigned thumbnail GETs.

### 4.3 vision — thumbnail extraction (rides the existing decode pass)
**File:** `app/modules/vision/` (`actors.py` / `analysis.py`)
The `analyze_session_proctoring` actor already downloads the recording from R2 and decodes frames with OpenCV. Extend it:

1. Load `sessions.transcript`; compute per-question `asked_at_ms` (min agent `timestamp_ms` per `question_id`).
2. After gaze analysis produces `flagged_intervals`, pick the **top-N most severe flags** (by kind severity × confidence; N configurable, e.g. 6) and take each flag's `start_ms`.
3. For the combined target set (question timestamps + top-flag timestamps), grab the frame nearest each timestamp via **targeted `cv2` seeks** (`CAP_PROP_POS_FRAMES`, one read per target — O(targets), low memory; the sampled gaze pass already proves decode works). Resize to ~320px wide, encode **WebP** (`IMWRITE_WEBP_QUALITY`).
4. Upload each via `get_object_storage().upload_bytes` under
   `{thumbnail_key_prefix}/{tenant_id}/{session_id}/q_{question_id}.webp` (questions) and
   `{thumbnail_key_prefix}/{tenant_id}/{session_id}/flag_{start_ms}.webp` (flags).
5. Upsert rows into the new table (§4.5). Idempotent: keys are deterministic (overwrite); rows upsert. The actor's existing status-row idempotency gate is unchanged.
6. Expose a read helper in the vision public API: `get_session_timeline_thumbnails(db, session_id) -> list[...]` for the reporting layer to consume (no cross-module deep import).

**Faces note:** thumbnails are frames of the consented interview recording — same sensitivity as the recording itself (private bucket, presigned-only, short TTL). Not third-party facial scraping.

### 4.4 reporting — surface timestamps + thumbnails
**Files:** `app/modules/reporting/schemas.py`, `service.py`, `actors.py`, router.
- `QuestionOut` gains `asked_at_ms: int | None` (persisted in `question_scorecards` JSONB — stable) and `thumbnail_url: str | None` (**presigned at read time** — ephemeral, never stored).
- `build_report`: set `asked_at_ms` per question from the transcript join.
- Report **GET** path: after loading the report, call the vision read helper for `kind='question'` thumbnails, presign each, attach `thumbnail_url` to the matching `QuestionOut` by `question_id`.
- Proctoring **GET** path (`/proctoring`): attach `thumbnail_url` (presigned) to the top `flagged_intervals` that have a `flag_*` thumbnail. Add an optional `thumbnail_url` to the flagged-interval schema.

Each endpoint presigns only its own domain's thumbnails → clean ownership, and the frontend already calls both.

### 4.5 New table — `session_timeline_thumbnails`
Migration `NNNN_session_timeline_thumbnails` (verify the current Alembic head at implementation time — the recording/proctoring migrations landed after the `0048` noted in CLAUDE.md; use the next available number). Tenant-scoped.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID NOT NULL | FK `clients.id` ON DELETE CASCADE |
| `session_id` | UUID NOT NULL | FK `sessions.id` ON DELETE CASCADE |
| `kind` | TEXT NOT NULL | `'question'` \| `'flag'` (CHECK) |
| `ref_id` | TEXT NOT NULL | question_id (UUID str) or flag key (`start_ms`) |
| `t_ms` | INTEGER NOT NULL | captured timestamp, session-start-relative |
| `s3_key` | TEXT NOT NULL | R2 object key |
| `created_at` | TIMESTAMPTZ NOT NULL | `now()` |

- Unique `(session_id, kind, ref_id)` for idempotent upsert.
- Canonical RLS pair (`tenant_isolation` USING+WITH CHECK with `NULLIF`, `service_bypass`).
- Register in `_TENANT_SCOPED_TABLES` and the `_assert_rls_completeness` enumerated list in `app/main.py`.
- Rollback script required.

---

## 5. API Contract Changes

```
QuestionOut (ReportRead.questions[]):
  + asked_at_ms: int | null      # ms since session start; null for legacy sessions
  + thumbnail_url: string | null # presigned R2 GET; null until vision job done / legacy

ProctoringFlaggedInterval (ProctoringAnalysis.flagged_intervals[]):
  + thumbnail_url: string | null # presigned; present only for top-N severe flags
```

Frontend `lib/api/reports.ts` types updated to match. The filmstrip card is fully described by one `questions[]` entry (status + read from report, time + thumbnail in the same object).

---

## 6. Frontend Component Plan

New tree under `components/dashboard/reports/theater/`:
- `ReviewTheater.tsx` — full-screen overlay container (focus trap, Esc-close, scroll-lock, frosted backdrop).
- `TheaterTopBar.tsx` — identity + gauges + risk/verdict chips + close.
- `TheaterStage.tsx` — `<video>` + transport controls + imperative seek API.
- `ThisMomentPanel.tsx` — default decision summary; per-item question/flag detail.
- `SessionTimeline.tsx` — composes `Filmstrip`, `NodeTrack`, `IntegrityLane`.
- `useTheaterState.ts` — active item, playhead↔selection sync, seek wiring.
- `theater.css` — glass + timeline styles/animations.
- `SessionPlayback.tsx` → refactor into a **poster** that opens `ReviewTheater` (or a new `PlaybackPoster.tsx`; keep the existing transcript-seek logic for the transcript toggle).

Data joins client-side by `question_id`. Seeks use the existing offset (`offset_ms`).

---

## 7. Timeline alignment (offset)

All anchors (`asked_at_ms`, proctoring `start_ms`/`end_ms`, thumbnail `t_ms`) are **session-start-relative**. The recording currently assumes video `t=0 = session start` (`offset_ms = 0`), which is how proctoring intervals already align in the current UI. Thumbnails are grabbed from the recording at those same ms, so they stay consistent with existing behavior. If `offset_ms` is ever calibrated, it applies uniformly to all layers. Not a blocker.

---

## 8. Testing

- **Engine:** `question_id` populated on delivered questions (session-level test); fillers stay null.
- **Storage:** `upload_bytes` PUTs to the right bucket/key/content-type (mocked client).
- **Vision:** target-timestamp seek selects nearest frame; thumbnails encoded + uploaded (mocked storage); rows upserted; top-N flag selection; idempotent re-run; RLS membership of the new table.
- **Reporting:** `asked_at_ms` in scorecards; GET presigns `thumbnail_url` per question; `/proctoring` presigns top-flag thumbnails.
- **Migration:** the new `session_timeline_thumbnails` migration up/down; `_assert_rls_completeness` passes with the new table.
- **Frontend:** theater open/close + a11y; seek wiring; active-item↔node sync; legacy-session degradation (null thumbnail/asked_at_ms); API type coverage.

---

## 9. Security / PII / Ops

- Thumbnails inherit the recording's sensitivity: private R2 bucket, **presigned GET only**, short TTL; never public URLs; never logged.
- New table carries the canonical RLS pair and CASCADE on tenant/session delete.
- No R2 CORS change needed (server-side presigned GET, like the video).
- No new external sub-processor; reuses the existing recording bucket/client.

---

## 10. Open / Deferred

- Transcript toggle in the theater — included as low-priority; confirm during review.
- `offset_ms` calibration — out of scope (consistent with current proctoring alignment).
- Top-flag count `N` and severity ranking weights — tune during implementation (config-driven).
