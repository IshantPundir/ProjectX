# Candidate Reel — Design

- **Date:** 2026-06-01
- **Status:** Approved brainstorm 2026-06-01, pending implementation plan.
- **Surfaces touched:** `backend/nexus` (interview engine capture changes; new `reel` module + media worker; new migration), `frontend/app` report page (ReelCard + ReelPlayer review surface).
- **Depends on:** `2026-05-25-report-scoring-engine-design.md` (the report + `session_reports`), `2026-05-26-recruiter-report-ui-design.md` (the report page + Review Theater), `2026-05-29-vision-proctoring-design.md` (the R2 recording, the `vision-worker` image pattern, the `download→process→upload` lifecycle).

---

## 1. Context & Goal

Recruiters screen hundreds of candidates a day. Even after the AI screens and the report
ranks down to a top-10 shortlist, recruiters have no time to sit through a full session
playback per finalist. They need a **fast, skimmable "why advance this candidate" artifact**.

The **Candidate Reel** is a manually-triggered, **~45-second (60s hard cap)**, Instagram-reel-style
highlight video compiled from the session recording. It is **not** a montage of full monologue
answers; it is a **curated narrative** that argues the candidate's fit. The repeating unit is a
**question beat**: 🤖 the AI asks → ★ a "what they did well" credit beat → 🎥 the best slice of
the candidate's answer. Bookended by an intro (title + experience hook) and an outro (verdict +
"watch full interview" CTA).

The reel is a **viewing aid, never a decision**. It does not auto-advance or auto-reject anyone
(the Borderline-needs-human-review invariant is untouched).

---

## 2. Scope

### In scope (v1)
- **Live capture changes (Phase 1):** word-level timestamps + per-turn `start_ms`/`end_ms` on the
  transcript, normalized to the session clock; calibration of the transcript→video offset.
- **`session_reels` table** + status lifecycle, EDL storage, R2 key, versioning, RLS.
- **Reel Director:** an LLM (`app/ai`, structured output) that reads the word-timed transcript +
  the existing report and emits a validated **Edit Decision List (EDL)**.
- **Render pipeline:** ffmpeg clip-cutting + Pillow card rendering + offline TTS narration/question
  re-voicing (Arjun's voice) → single 16:9 MP4 → R2.
- **API:** generate / poll / playback endpoints, RBAC-gated (`reports.view`), eligibility-gated
  (verdict ∈ {advance, borderline}), rate-limited.
- **Frontend:** `ReelCard` (generate + poll + poster/play) and `ReelPlayer` (native `<video>` +
  chapter rail from EDL beats) on the report page.

### Out of scope (deferred)
- **Music bed.** Mockups showed a soft music bed; v1 ships **silent under cards** (TTS narration
  only). Licensed/royalty-free music is a later iteration — avoids a licensing rabbit hole now.
- **Vertical 9:16 / square crops.** v1 is **16:9 only** (operator chose full uncropped webcam for
  auditability). Smart-crop (reusing the vision face bbox) is deferred.
- **Re-editing / manual clip override UI.** v1 is fully AI-directed; no recruiter timeline editor.
- **Reels for rejected sessions.** Eligibility is advance/borderline only.
- **Sharing/export outside the dashboard** (public links, ATS push). v1 plays in-dashboard via
  presigned R2 URL, same as the recording.

---

## 3. Decision Log (keystone choices + rationale)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Capture word-level timing LIVE during the session**, not by re-running STT later. | LiveKit's `SpeechData` exposes `words: list[TimedString]` (`text`/`start_time`/`end_time`/`confidence`); capability `aligned_transcript="word"`; Deepgram nova-3 returns word timings by default. Live capture is lossless and free; re-deriving later is lossy and costs a second STT pass. Verified against `livekit-agents` source. |
| D2 | **AI Reel Director emits a structured EDL** (which moments, exact in/out, on-screen text, narration script). | Matches the system's "AI decides, human verifies" posture and the report engine's structured-output pattern. Most adaptive. Bounded: every clip in/out must map to a real word span; total ≤ 60s. |
| D3 | **Server-side ffmpeg render → one MP4 on R2.** | Produces a real, shareable artifact ("compile a 45s video"). Reuses the proven proctoring `download→process→upload` pattern + existing R2 storage. Keeps candidate video entirely in our infra. (Client-side EDL player rejected: no shareable file; external render APIs rejected: third-party sub-processor for candidate video violates the data-isolation posture.) |
| D4 | **Credit beat = designed card + narration in the interviewer's voice (Arjun).** | Card = skim-able + accessible; narration = carries the story. Reusing the session's exact TTS config (`build_tts_plugin()` + same `AIConfig`) keeps the framing voice continuous with the interview the recruiter just (conceptually) heard. |
| D5 | **All framing audio is TTS-generated (Arjun); all clip audio is the candidate's real recording.** AI question beats are **re-voiced via TTS over a question card**, not cut from the recording. | The recording's audio is a *mixed* candidate+agent track — cutting a clean AI-only segment is unreliable (candidate backchannels bleed in). Re-synthesizing the question is deterministic and clean. Net: the only "real" voice in the reel is the candidate's; everything around it is consistent. |
| D6 | **16:9 landscape, full uncropped webcam.** | Operator choice: preserves the audit trail (full scene), no smart-crop machinery, embeds cleanly in the report page. Reel-native 9:16 deferred. |
| D7 | **Burned-in captions on candidate clips.** | The reel must read on muted autoplay. Captions are derived from the word-timed transcript for the clip's span. |
| D8 | **Trigger: any user with `reports.view`. Eligibility: verdict ∈ {advance, borderline} + report `ready` + recording `ready`.** | Recruiters/hiring managers self-serve (the reel is a viewing tool). A highlight reel of a rejected candidate is contradictory, so reject is blocked. Borderline is allowed precisely because the reel aids the *required* human review. |
| D9 | **New tenant-scoped `session_reels` table, mirroring `session_reports`.** | Same lifecycle/RLS/versioning shape the team already understands; one reel per session (unique on `session_id`), regenerable with a version bump. |
| D10 | **Render runs on a media-capable worker (ffmpeg + Pillow + TTS plugins) on a dedicated `reel` Dramatiq queue.** | The main `nexus` image has no ffmpeg; the `vision` image has ffmpeg+opencv but not necessarily the TTS plugins. The reel actor needs ffmpeg + Pillow + the `livekit.plugins.{sarvam,openai,cartesia}` TTS deps in one image. §8 picks the packaging. |

---

## 4. The Reel Narrative Model

Ordered list of **beats**. Beat kinds:

| Kind | Visual | Audio | Source |
|---|---|---|---|
| `title` | Branded title card: candidate name, role, one-liner (e.g. "6 yrs · AI systems · Workato") | TTS intro line (optional) | generated |
| `experience` | Candidate webcam clip + burned captions | candidate's real audio | recording clip |
| `ask` | Question card: AI question text, branded | TTS re-voiced question (Arjun) | generated |
| `credit` | Credit card: "★ What they nailed" + one line | TTS narration (Arjun) | generated |
| `clip` | Candidate webcam clip + burned captions | candidate's real audio | recording clip |
| `outro` | Verdict + score + "▶ Watch full interview" CTA | TTS outro line (optional) | generated |

Canonical sequence: `title → experience → (ask → credit → clip)×N → outro`, where N is chosen by
the director to fit the duration budget (typically 3). The director MAY drop `experience` or vary
N to honor the 60s cap.

---

## 5. Phase 1 — Live Capture Changes (prerequisite; ships first)

**Goal:** after this lands, the operator runs a fresh strong-candidate session and the DB holds
everything a great reel needs. Independently testable by inspecting `sessions.transcript`.

### 5.1 Extend the transcript turn model
`app/modules/interview_runtime/models.py :: TranscriptEntry` gains:
- `start_ms: int` — turn start on the session clock.
- `end_ms: int` — turn end on the session clock.
- `words: list[WordTiming] | None` — `WordTiming = {text: str, start_ms: int, end_ms: int, confidence: float}`.

`words` is populated for **candidate** turns (from STT). Agent turns keep `words=None` (they are
re-voiced, not clipped). Backward-compatible: existing rows without these fields still parse
(fields optional); the report player and recording playback ignore unknown/missing fields today.

### 5.2 Capture word timings in the engine
In `agent.py`, the candidate turn is committed in `on_user_turn_completed` reading only
`new_message.text_content`. Add a custom **`stt_node`** override (or read `alternatives[0].words`
off the `FINAL_TRANSCRIPT` `SpeechEvent`) to capture the per-word `TimedString`s for the turn.

**Clock normalization (the load-bearing detail).** STT word times are on the *audio-stream clock*;
the persisted `timestamp_ms` is `time.monotonic() - started_at` (session clock). At each final
transcript we already call `_t_ms()`. We anchor the word span to the session clock by aligning the
turn's last word `end_time` to the capture instant (`_t_ms()` at commit), and expressing every word
as `word_session_ms = turn_end_ms - (last_word.end_time - word.start_time)*1000`. Equivalent: store
words relative to a captured `start_ms` for the turn. The spec-level guarantee: **`words[].start_ms`
/`end_ms` and the turn's `start_ms`/`end_ms` are all on the same session clock as the existing
`timestamp_ms`.**

### 5.3 Calibrate the transcript→video offset
`recording.py :: RecordingPlayback.offset_ms` is hardcoded `0` with a "calibrate once measured"
comment. Phase 1 makes it real:
- Persist a **`recording_clock_offset_ms`** on the session, measured as
  `(egress media start) − (session clock t=0)`. The LiveKit egress snapshot reports a start time;
  the session has `recording_started_at` and the engine's `started_at`. We compute and store the
  delta when egress is confirmed (during `_reconcile`), so `video_ms = session_ms − offset`.
- `get_session_recording_playback` returns this real offset instead of `0`. (Bonus: fixes the
  existing report player's lead-in drift.)
- **Test-session caveat:** the offset is small (a few seconds of lead-in). The reel render applies
  it plus a small safety pad (§7.3); the design tolerates ±200ms slop without breaking a beat.

### 5.4 (Optional) event-log word stream
Emit a `turn.transcript_words` event in the engine event log for forensic/debug parity. Not
required by the reel pipeline (the pipeline reads `sessions.transcript`), so this is best-effort.

---

## 6. Data Model — `session_reels`

New tenant-scoped table (new Alembic migration; **rollback script required**, per CI/CD rules).
Mirrors `session_reports`:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `tenant_id` | uuid not null | FK `clients`, RLS key |
| `session_id` | uuid not null | FK `sessions`, **unique** (one reel per session) |
| `assignment_id` | uuid not null | FK `candidate_job_assignments` (index for hub queries) |
| `status` | text not null | `pending│generating│ready│failed` (check constraint) |
| `generation_error` | text | populated on `failed` (≤500 chars, no PII) |
| `edl` | jsonb | the validated Edit Decision List (the beats) |
| `r2_key` | text | `reels/{tenant_id}/{session_id}.mp4` when ready |
| `duration_seconds` | numeric | final rendered duration |
| `version` | int not null default 1 | bumped on regenerate |
| `model_versions` | jsonb | director model id + prompt version + render toolchain versions |
| `created_by` | uuid not null | FK `users` (the recruiter who triggered) |
| `created_at` / `updated_at` | timestamptz | `touch_updated_at` trigger |

RLS: the canonical `tenant_isolation` (USING + WITH CHECK, `NULLIF(...,'')::uuid`) +
`service_bypass` pair. Added to `_TENANT_SCOPED_TABLES`; boot assertion `_assert_rls_completeness`
covers it automatically. Cross-tenant read returns 0 rows (test gate).

---

## 7. Phase 2 — Reel Generation Backend

### 7.1 Reel Director (LLM → EDL)
- New `app/modules/reel/director.py`, called from the actor; uses `app/ai` Responses API with
  structured output (same pattern as `report_scorer/*`).
- **Input (context before document, per house rule):** the report ground truth
  (`question_scorecards` with `asked_at_ms`/`candidate_quote`/`status`/`our_read`,
  `signal_scorecards`, `summary.strengths`), then the **word-timed transcript**.
- **Output schema (`ReelEdlOut`):** ordered `beats[]`, each
  `{kind, source_turn_ref?, in_ms?, out_ms?, on_screen_text?, caption?, narration_text?}`.
- Prompt at `prompts/v3/reel/director.txt`, versioned via `PromptLoader`.
- **Validation (deterministic guard rails after the LLM):**
  - every `clip`/`experience` beat's `[in_ms, out_ms]` must fall within a real candidate turn's
    `[start_ms, end_ms]` (snap to nearest word boundaries; reject hallucinated spans);
  - sum of beat durations ≤ **60s** (trim/drop lowest-value beats to fit; target ~45s);
  - at least one `clip` beat or the generation fails honestly (`status=failed`).

### 7.2 Render actor
- New Dramatiq actor `generate_session_reel(session_id, tenant_id, correlation_id, force=False)`
  on queue **`reel`**; `max_retries=2`, exponential backoff; idempotency gate (skip if `ready` and
  not `force`), mirroring `score_session_report`.
- Loads report + word-timed transcript + recording key under **bypass-RLS + explicit `tenant_id`**
  (`SET LOCAL app.current_tenant`), exactly like the report actor.
- Does **not** write the `generating` mark mid-transaction (same reasoning as the report actor:
  keep the transaction short; rely on the idempotency gate + retries).

### 7.3 ffmpeg / Pillow / TTS stages
1. Download `recording.mp4` from R2 to a `TemporaryDirectory` (`download_to_path`, as vision does).
2. **Clip beats:** `ffmpeg` cut `[in_ms − pad, out_ms + pad]` with `offset` applied
   (`video_ms = session_ms − recording_clock_offset_ms`); re-encode to a normalized 16:9 H.264 +
   AAC intermediate; burn captions for the span (drawtext or a generated `.ass`/`.srt` from
   `words[]`).
3. **Card beats** (`title`/`ask`/`credit`/`outro`): Pillow renders a 16:9 PNG (brand styling from
   a shared card template); duration = max(min read time, narration audio length).
4. **TTS:** `build_tts_plugin()` (same `AIConfig` as the session) → `.synthesize(text)` → collect
   PCM frames → WAV, for `ask` (re-voiced question) and `credit`/intro/outro narration.
5. **Concat + normalize:** ffmpeg `concat`/`filter_complex` to one stream, consistent fps/SAR,
   normalized audio levels; (music bed deferred).
6. Upload final MP4 → `reels/{tenant}/{session}.mp4`; set `status=ready`, persist `edl`,
   `duration_seconds`, `model_versions`. On any failure: `status=failed` + `generation_error`,
   re-raise for retry.

### 7.4 API (`app/modules/reel/router.py`)
All under global rate-limit middleware (authenticated class: 600/min IP, 10k/min tenant). RBAC via
the existing pattern.

| Method | Path | Guard | Behaviour |
|---|---|---|---|
| POST | `/api/reports/session/{session_id}/reel` | `reports.view` + eligibility (verdict∈{advance,borderline}, report ready, recording ready) | insert/refresh `session_reels(pending)`, enqueue actor, audit-log the trigger; 202 |
| GET | `/api/reports/session/{session_id}/reel` | `reports.view` | 202 if `pending`/`generating`; 200 with `{status, signed_url, duration, edl_chapters}` if `ready`; 200 `failed`; 404 if none |
| POST | `/api/reports/session/{session_id}/reel/regenerate` | `reports.view` | re-enqueue with `force=True`, version bump |

`signed_url` is a short-lived presigned R2 GET, minted on read (TTL = `recording_signed_url_ttl`),
never logged. Triggering a reel is an **audited action** (`actor_id`, `tenant_id`,
`action=reel.generate`, `resource_type=session`, `resource_id`, `correlation_id`).

---

## 8. Infra / Worker Packaging

The reel actor needs **ffmpeg + Pillow + the TTS plugin deps** in one image. Decision:
**extend the existing `vision-worker` image** (it already has ffmpeg + opencv + numpy) to also carry
Pillow and the `livekit.plugins.{sarvam,openai,cartesia}` TTS extras, and run the `reel` queue on
that worker (`-Q vision,reel` or a sibling process). Rationale: avoids a third image; ffmpeg is the
expensive apt dependency and it's already there; TTS plugin wheels are light. The actor is imported
only in the media worker's entrypoint (lazy, like the vision actor) so the lean `nexus`/`worker`
images never import ffmpeg/TTS-render code. `docker-compose.yml` adds the `reel` queue to that
service. Cost: render is CPU-bound but infrequent (manual trigger) — `--processes 1` is fine for MVP.

---

## 9. Phase 3 — Frontend (`frontend/app`)

- **Types** (`lib/api/reports.ts`): `ReelPlayback { status, signed_url, duration_seconds, chapters: {kind,label,start_ms}[] }`.
- **Hooks** (`lib/hooks/use-reel.ts`): `useReel(sessionId)` (poll every 5s while `generating`),
  `useGenerateReel(sessionId)` (mutation → optimistic `generating`).
- **`ReelCard`** in `ReportView` (left column, after `SessionPlayback`): when none + eligible → a
  "Create candidate reel" button; while generating → progress; when ready → 16:9 poster + Play;
  ineligible (reject / no recording) → hidden or a quiet disabled note.
- **`ReelPlayer`** modal: native `<video src={signed_url}>` + minimal controls + a **chapter rail**
  built from `edl_chapters` (jump to beat). Reuses the Review Theater's video-controller patterns
  (callback-ref for the element node — the Base UI portal-remount gotcha from
  `feedback_dialog_portal_node_ref`) but is simpler (no question timeline, no proctoring flags).
- Eligibility/gating mirrored client-side for affordance only; the **server is the source of truth**.

---

## 10. Testing

- **Phase 1:** unit-test the clock-normalization math (word→session-ms); assert `TranscriptEntry`
  round-trips with/without `words`; manual: run a live session, inspect `sessions.transcript`.
- **EDL validation:** table-test the guard rails — hallucinated spans rejected, >60s trimmed,
  zero-clip → `failed`.
- **`session_reels`:** cross-tenant read returns 0 rows (mandatory new-table gate); membership in
  `_TENANT_SCOPED_TABLES`; boot assertion green.
- **Endpoints:** RBAC (no `reports.view` → 403), eligibility (reject verdict → 4xx, no recording →
  4xx), status transitions, audit-log emission.
- **Render:** a fixture recording + a canned EDL → assert an MP4 is produced with expected duration
  (±tolerance) and beat count; TTS mocked.
- Per the manual-agent-testing preference, the end-to-end "does the reel feel good" check is the
  operator running a real session + generating a reel — not an automated eval.

---

## 11. Security / Compliance

- Candidate video never leaves our infra (no external render API — D3). Reel MP4 lives in the
  private recording bucket family; presigned GET only.
- No raw PII in logs: log `session_id` + `reel_id`, never transcript text, narration text, or signed
  URLs. `generation_error` is scrubbed to ≤500 chars.
- Reel generation is audited (§7.4). The reel makes no hiring decision (Borderline invariant intact).
- Threat-model update: the reel is a new derived view of candidate data on the recruiter surface;
  reference the recording/report sections — no new external service joins the data path.

---

## 12. Open Questions / Deferred

- **Music bed** (licensing) — deferred to a later iteration.
- **9:16 / square + smart-crop** — deferred (reuse vision face bbox when revisited).
- **Recruiter re-edit UI** — deferred; v1 is fully AI-directed (regenerate is the only lever).
- **Exact caption renderer** (ffmpeg `drawtext` vs generated `.ass`) — implementation-plan detail.
- **Precise offset calibration method** — Phase 1 will measure against the first real recording and
  may refine the egress-start anchor.
