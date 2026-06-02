# Candidate Reel — Phase 2 Build Plan & Offset Calibration

- **Date:** 2026-06-02
- **Status:** Approved brainstorm 2026-06-02, pending implementation plan.
- **Refines:** `2026-06-01-candidate-reel-design.md` (the approved Candidate Reel design). This
  document does **not** re-decide the locked product/architecture choices there; it fixes the
  **build order** for Phase 2 and resolves the **transcript→video offset**, which the original
  spec assumed Phase 1 would deliver but Phase 1 did not (Phase 1 shipped word-timing capture only).
- **Test session:** `5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6` (the verified word-timed session; fixture
  at `backend/nexus/tests/fixtures/candidate_reel/session_5e004a4d_transcript.json`).

---

## 1. Why this document

The Phase 2 design (§7–§8 of the original spec) is comprehensive but assumed two things that are no
longer true:

1. **The transcript→video offset was supposed to be calibrated in Phase 1.** It wasn't —
   `RecordingPlayback.offset_ms` is still hardcoded `0`. The transcript clock is **monotonic**
   (`engine.started_at = time.monotonic()` at `on_enter`), with no stored wall-clock pairing, so
   calibration is a real, unsolved task that Phase 2 must own. This is the load-bearing risk: if the
   offset is wrong, every clip shows the wrong moment.
2. **Neither existing worker image is render-ready.** The `vision` image has ffmpeg+opencv but no
   Pillow/TTS plugins; the main image has the TTS plugins but no ffmpeg/Pillow.

Everything else the reel consumes already exists and is rich: `session_reports`
(`question_scorecards` with `asked_at_ms`/`candidate_quote`/`our_read`, `signal_scorecards`,
`summary.strengths`), the R2 `download_to_path`/`presign_get_url` helpers, the vision actor's
`download→process→upload` pattern, and `tts.TTS.synthesize()` works offline.

## 2. Build strategy — render-first spike, clips-only core

Build order is **render pipeline first, production scaffold second** (table/RLS/actor/endpoints).
Rationale: the reel's value ("does it feel good") is subjective and only a real rendered MP4 tells
us; building plumbing before the render is proven risks wasted work. The pieces built render-first
are **real, reusable functions** — only the *entrypoint* is throwaway.

**The first milestone is the clips-only core**, because the offset is the #1 unknown and a
clips-only render isolates it: cards/TTS/director are additive polish layered on afterward.

Sequence:

1. **Clips-only core** — measure the offset for `5e004a4d`, cut 2–3 hand-picked clean candidate
   spans → normalized, captioned clips → concat → one MP4 to watch. Success = play it and confirm
   footage + captions are in sync on the right moments.
2. **Director** (`reel/director.py`) — LLM → validated EDL; inspect the EDL for `5e004a4d` before rendering it.
3. **Cards** (`reel/cards.py`, Pillow) — title/ask/credit/outro 16:9 PNGs.
4. **TTS** (`reel/tts.py`) — offline `synthesize()` → WAV for re-voiced `ask` + narration (Arjun).
5. **Full render** — interleave cards + TTS + clips into `title → experience → (ask→credit→clip)×N → outro`.
6. **Production scaffold** — `session_reels` table + migration + RLS; `generate_session_reel`
   Dramatiq actor on the extended media image; POST/GET/regenerate endpoints (eligibility + audit +
   rate-limit); offset productionized. The dev entrypoint retires here.
7. **Phase 3 (frontend)** — `ReelCard` + `ReelPlayer`, separate effort.

## 3. Module layout (`app/modules/reel/`)

| File | Responsibility | Built in |
|---|---|---|
| `clips.py` | Cut one candidate span `[start_ms,end_ms]` from the recording → normalized 16:9 captioned clip (offset applied). | Step 1 |
| `offset.py` | Compute a session's transcript→video offset (the calibration). | Step 1 |
| `render.py` | Concat clips → MP4; later grows to interleave cards + TTS. | Step 1, extended Step 5 |
| `director.py` | LLM → validated `ReelEdlOut` (beats) from report + word-timed transcript. | Step 2 |
| `cards.py` | Pillow card rendering (title/ask/credit/outro). | Step 3 |
| `tts.py` | Offline `build_tts_plugin().synthesize()` → WAV. | Step 4 |
| `spike.py` | **Dev-only** entrypoint (`python -m app.modules.reel.spike <session_id>`): loads the session, hand-picks spans, calls the real functions, writes `tmp/reel_<session>.mp4`. Retired at Step 6. | Step 1 |
| `models.py` / `service.py` / `actors.py` / `router.py` / `schemas.py` | `session_reels` ORM, persistence, Dramatiq actor, API, wire models — mirror `session_reports`. | Step 6 |

`spike.py` is throwaway scaffolding; `clips`/`offset`/`render`/`director`/`cards`/`tts` are the exact
functions the production actor calls.

## 4. Offset calibration — the decision

**Key insight: both clocks run at real-time 1:1**, so the transcript→video offset is a **single
constant per session with no drift**. One scalar `offset_ms` per session suffices;
`video_ms = session_ms − offset_ms`. Per-clip alignment machinery is unnecessary.

Three candidate mechanisms:

- **A — empirical from footage (spike path).** The agent's opener is the first intended audio in
  every recording, and its session-ms is known from the transcript/event log. Measure the opener's
  speech onset in the video (`ffmpeg silencedetect`) and `offset = opener_session_ms − video_onset`
  (so `video_ms = session_ms − offset`; the offset is typically **negative** because the video starts
  before the engine's monotonic zero). Ground truth on the real footage; **clock-skew-free**
  (everything derived from the one recorded reality). Initially run as a manual measurement for
  `5e004a4d`.
- **A-automated — opener-onset as a per-session formula (production candidate).** Promote A to an
  automatic step: it reuses the recording we already download to render, needs **no cross-machine
  clock pairing, no egress internals, no new engine column/migration**. Cost: robust onset detection.
  Failure mode to guard: the candidate (who joins before the agent) making noise/speech *before* the
  opener, which would anchor on the wrong onset — mitigated by keying on the known opener
  `session_ms` window rather than the raw first non-silence, and validated against B.
- **B — wall-clock anchor formula (cross-check / fallback).**
  `offset = egress_started_at − engine_started_wall`, where `engine_started_wall` is a wall stamp at
  `on_enter` (needs a new session column + migration) and `egress_started_at` is LiveKit's egress
  media start (currently discarded at `livekit.py:254`, needs surfacing). Textbook-clean but pairs
  **two independent machine clocks** (engine container vs LiveKit Cloud) — NTP skew lands directly in
  the offset — and depends on whether LiveKit's `started_at` means *first frame* vs *process start*.
  Both are invisible without validating against footage.

**Decision:**
- **Spike:** A (manual measurement on `5e004a4d`) — unblocks the clips-only core immediately and
  gives the real number.
- **Production:** prefer **A-automated** (self-contained, clock-skew-free), with **B kept as a cheap
  cross-check/fallback**. Invest in B's engine column + egress surfacing only if A-automated proves
  flaky on real sessions.
- **Final production choice is deferred** until the spike measurement shows the actual offset and
  whether onset-detection is clean. A `pad ≈ 150ms` is applied to clip in-points regardless, and the
  design tolerates ±150ms slop without breaking a beat.

## 5. Clips-only render pipeline (Step 1 detail)

- **Cut:** `ffmpeg -ss (in_ms − offset − pad) -to (out_ms − offset + pad)` against `recording.mp4`,
  `pad ≈ 150ms`. The answer span is mostly clean candidate audio (the floor-gate guarantees the agent
  isn't speaking during the candidate's answer), so the composite track is usable as-is for clips.
- **Normalize:** re-encode every clip to identical params — **1280×720, 16:9, 30fps, H.264 + AAC
  48k**, fixed SAR — so `render.py` joins them with the fast `concat` demuxer (no re-encode at join,
  no concat glitches).
- **Captions:** generate a **`.ass` subtitle from `words[]`** for the span and burn it in (`subtitles=`
  / libass). Chosen over `drawtext` because real word timings give properly timed, multi-line, styled
  captions (readable on muted autoplay, D7) with far less pain. Words are grouped into ~3–5-word lines
  timed to their `start_ms`/`end_ms`. `words[]` is the source of truth (handles the words⊇text case);
  captions are clip-relative so the offset isn't needed for them.
- **Spike inputs:** 2–3 *clean* substantive answer turns from `5e004a4d` — explicitly **not** turn 1
  (its pre-floor-gate `"sure"` leak + skewed `start_ms`). Good candidates: the Workato
  workflow-design answer (~183s) and the rate-limiting/idempotency answer (~415s).
- **Output:** `tmp/reel_<session>_clips.mp4` + the measured offset printed for eyeballing sync.

## 6. Worker image (Step 6)

Per original spec §8: **extend the existing `vision-worker` image** (it already has ffmpeg) with
**Pillow + the `livekit.plugins.{sarvam,openai,cartesia}` TTS wheels**, and run the `reel` Dramatiq
queue on that worker. The reel actor is imported only in the media worker's entrypoint (lazy, like
the vision actor) so the lean `nexus`/`worker` images never import ffmpeg/TTS-render code. Reel render
is CPU-bound but infrequent (manual trigger); keep it concurrency-capped (the vision service already
caps `cpus: 4` post-incident).

## 7. What is unchanged from the original spec

The narrative model (§4), the `session_reels` schema (§6), the director I/O + EDL validation (§7.1),
the actor/idempotency/RLS pattern (§7.2), the ffmpeg/Pillow/TTS stages (§7.3), the API surface (§7.4),
security/compliance (§11), and testing (§10) all stand as written. This document only re-orders the
build and resolves the offset.

## 8. Testing alignment

- **Clips-only core:** the success test is **manual** — play the MP4, confirm sync — consistent with
  the manual-agent-testing preference. The offset measurement is logged for the record.
- **`clips.py`/`offset.py`:** unit-test the caption `.ass` generation from `words[]` and the
  offset-application math (pure functions, ffmpeg shelled out / mocked).
- Steps 2–6 inherit the original spec's test gates (EDL validation table-tests, `session_reels`
  cross-tenant 0-rows, endpoint RBAC/eligibility/audit, render fixture test).
