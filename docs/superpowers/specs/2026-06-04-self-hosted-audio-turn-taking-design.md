# Self-Hosted Audio & Turn-Taking — Design

> **STATUS: APPROVED — ready for implementation plan.** Authored 2026-06-04.
> Supersedes the audio-path portions of
> [`2026-05-06-audio-pipeline-design.md`](./2026-05-06-audio-pipeline-design.md)
> (LK-Cloud-locked audio). This is **Step 1** of the deployment-decoupling plan in
> [`../../deployment/2026-06-03-deployment-architecture-research.md`](../../deployment/2026-06-03-deployment-architecture-research.md)
> ("decouple Cloud *features*"), brought forward for quality reasons (see Motivation).

---

## Motivation

Two unrelated drivers converged on the same surgical change:

1. **Portability (deployment Step 1).** The agent's only remaining hard LiveKit-Cloud
   lock-in is **adaptive interruption handling** — the barge-in model runs in LiveKit's
   datacenters and is documented as Cloud-deploy-only. Removing it makes
   `nexus-engine` portable to a self-hosted agent fleet (Trigger A) with no further
   audio rework.

2. **Audio quality in a quiet-environment B2B screening context.** Forensic analysis of
   session `5e004a4d…6438f6` (LiveKit traces + engine envelope) showed the candidate's
   own voice was frequently clipped/disfluent at utterance ends, consistent with
   over-aggressive server-side noise cancellation (`ai-coustics QUAIL_L`). Candidates are
   **mandated to be in a silent environment**, so heavy server-side NC has near-zero
   upside (little noise to cancel) and real downside (false-suppressing speech, leaving
   clipped tails). It also adds per-minute cost and a vendor dependency on the
   realtime path.

**Decision:** drop adaptive interruption + ai-coustics, switch the VAD to **Silero**
(open-source, local), and let the **browser's built-in noise suppression** be the light,
free safety net. Keep echo cancellation (load-bearing for full-duplex barge-in).

### What this change does NOT fix (explicit)

The original UX complaint — the **9–10 s end-of-utterance (EOU) holds** and the
mistimed *"take your time"* hold-cue — is driven by the **MultilingualModel
turn-detector's near-zero EOU probabilities** (`max_delay=10 s`) and the **hold-cue
gating**, which are *separate knobs* from anything here. They are deliberately
**out of scope** and handled in the immediately-following EOU spec
(`2026-06-XX-eou-and-hold-cue-tuning-design.md`). This change may *indirectly* help by
feeding cleaner speech to the turn detector, but it does not retune timing.

---

## Goals

- Remove the LiveKit-Cloud-only **adaptive interruption** dependency; barge-in via VAD.
- Remove **ai-coustics** entirely (noise cancellation **and** the VAD adapter it provides).
- Adopt **Silero VAD**, prewarm-loaded per LiveKit best practice.
- Flip server-authoritative browser audio constraints to `noiseSuppression: true`
  (EC + AGC unchanged).
- Add two **default-on** dev/test toggles so local tuning runs don't spend tokens
  (report scoring) or heavy compute (vision proctoring).
- Update docs, the threat model, and tests to match. No dead config, no compatibility
  stubs.

## Non-goals

- EOU `max_delay` retune and hold-cue gating (next spec).
- Touching the `MultilingualModel` turn detector, STT (Deepgram), or TTS (Sarvam).
- Self-hosting the SFU / egress (deployment Trigger B).
- Any new Silero tuning knobs (use documented defaults; timing tuning is the next spec).

---

## The one tradeoff being accepted

Dropping adaptive interruption moves **barge-in detection from the Cloud context-aware
model to VAD** (`interruption mode="vad"`, the documented downgrade path). VAD-based
detection is blunter at distinguishing a true interruption from conversational
backchannel. **Mitigation, all retained:**

- `min_words=2` — the agent will not yield to fewer than 2 transcribed words (filters
  "mm", "yeah", "okay").
- `min_duration=1.0 s` — filters brief noise bursts.
- `false_interruption_timeout=2.0 s` + `resume_false_interruption=True` — the agent
  resumes its line if an "interruption" produced no transcript.
- The pure `turn_taking/floor.py::should_yield` + `eou.py::is_backchannel(min_words=2)`
  rules remain as the documented invariant (audit/tests).

**Acceptance gate:** a live talk-test confirming the agent is still cleanly
interruptible and does not yield to backchannel. (LiveKit confirms `mode="vad"` is the
supported, idiomatic way to disable adaptive; all of the above gates carry over
unchanged into VAD mode — verified against the Turn Handling Options reference,
2026-06-04.)

---

## Part A — Audio & turn-taking rework

### A1. `app/ai/realtime.py` (the single blessed `livekit.plugins.*` import site)

**`build_interruption_options()`** — flip the mode; keep every gate:

```python
def build_interruption_options() -> dict[str, object]:
    """Construct the `interruption=` block for TurnHandlingOptions.

    VAD-based barge-in (self-hostable; no LiveKit-Cloud dependency). The word-count
    and duration gates filter backchannel/noise; false-interruption recovery resumes
    the agent's line if no transcript follows.
    """
    logger.info("ai.realtime.interruption.built", mode="vad")
    return {
        "mode": "vad",
        "min_duration": 1.0,
        "min_words": 2,
        "false_interruption_timeout": 2.0,
        "resume_false_interruption": True,
    }
```

> Note: with `MultilingualModel` + aligned Deepgram transcripts, an omitted mode would
> default to `"adaptive"` — so `mode` MUST be set explicitly to `"vad"`.

**`build_noise_cancellation()`** — **deleted in full** (function + its `ai_coustics`
imports). No replacement; browser-side NS covers the quiet-env case.

**`build_vad()`** — replace the ai-coustics VAD with Silero, loaded with documented
defaults. Per LiveKit guidance this is **called from `prewarm()`** (blocking model load),
not per session:

```python
def build_vad() -> object:
    """Construct the Silero VAD. Blocking model load — call from prewarm()."""
    from livekit.plugins import silero
    logger.info("ai.realtime.vad.built", provider="silero")
    return silero.VAD.load()   # documented defaults: min_silence_duration=0.55,
                               # activation_threshold=0.5, sample_rate=16000, force_cpu=True
```

**`build_turn_detector()`** — **unchanged.** `MultilingualModel` stays (local ONNX;
not a Cloud feature). EOU tuning is the next spec.

### A2. `app/modules/interview_engine/agent.py`

- **`prewarm(proc)`** (already bootstraps OTel) → add
  `proc.userdata["vad"] = build_vad()`. This loads Silero once per worker process.
- **Imports** → drop `build_noise_cancellation`; keep `build_interruption_options`,
  `build_vad` (now only called in prewarm), `build_turn_detector`.
- **`run()` AgentSession** → `vad=ctx.proc.userdata["vad"]` (was `vad=build_vad()`).
  `turn_handling` unchanged in shape (interruption block now returns `mode="vad"`).
- **`run()` `session.start(...)`** (current lines ~1029–1035) → remove
  `nc_filter = build_noise_cancellation()` and the `noise_cancellation=nc_filter`
  argument. Drop the now-empty `audio_input=room_io.AudioInputOptions(...)`; keep
  `room_options=room_io.RoomOptions(delete_room_on_close=True)`.

> `compute_audio_summary` / `audio_tuning_summary` records endpointing + turn-detector
> config only (no NC field), so it needs **no change**. (Optional, additive: record the
> interruption `mode` for observability — not required; defer.)

### A3. `app/config.py`

Remove the now-dead noise-cancellation config (no compatibility stub):

- `NoiseCancellationMode` Literal (and its `# "off"/"krisp_nc" no longer valid` comment).
- `interview_noise_cancellation`.
- `interview_nc_enhancement_level`.

No new Silero knobs (YAGNI; defaults are the documented sane values).

### A4. `app/modules/session/service.py` + `schemas.py` (server-authoritative browser hints)

`_compute_audio_processing_hints()` → `noise_suppression=True` (EC + AGC stay `True`).
Rewrite both the function docstring and `AudioProcessingHints` schema docstring:

> No server-side noise cancellation. The browser's built-in noise suppression handles
> the (rare, mandated-quiet) ambient case; echo cancellation is load-bearing for
> full-duplex barge-in; AGC stabilizes input level.

The frontend `lib/api/audio-hints.ts` is generic (maps server hints → getUserMedia
constraints) and needs **no logic change** — the flip propagates automatically.

### A5. Dependencies (`backend/nexus/pyproject.toml`)

- Remove `livekit-plugins-ai-coustics>=0.2,<1`.
- Add `livekit-plugins-silero>=1.5.4,<2` (track the pinned livekit-agents minor).
- Regenerate `uv.lock` (lockfile is authoritative; committed).

---

## Part B — Dev/test toggles (default-on; prod behavior unchanged)

Both flags default `True`, so production is byte-for-byte unchanged. The operator sets
them `false` in a local `.env` for tuning runs. Both are **non-destructive**: the session
still completes, persists `coverage_summary`, and keeps its recording, so a skipped
session remains fully **re-scorable / re-analyzable later** via existing manual endpoints.

### B1. `AUTO_SCORE_SESSION_REPORTS` (default `True`) — report LLM scorer

- New setting `auto_score_session_reports: bool = True` in `app/config.py`.
- Gate the **single** enqueue site, `interview_runtime/service.py` (~line 462). Add
  `from app.config import settings`. Short-circuit *before* the `.send()`:

```python
if not settings.auto_score_session_reports:
    logger.info(
        "interview_runtime.record_session_result.report_scoring_disabled",
        session_id=str(session_id), reason="auto_score_session_reports=false",
    )
elif result.coverage_summary is not None:
    try:
        from app.modules.reporting import score_session_report
        score_session_report.send(str(session_id), str(tenant_id), correlation_id)
        ...
```

The existing best-effort try/except and the `coverage_summary is not None` check are
preserved inside the `elif`.

### B2. `AUTO_ANALYZE_PROCTORING` (default `True`) — vision gaze analysis (CPU/GPU)

- New setting `auto_analyze_proctoring: bool = True` in `app/config.py`.
- Gate the **single** choke point, `session/recording.py::_enqueue_vision_analysis`
  (~line 125) — every enqueue path funnels through it:

```python
def _enqueue_vision_analysis(session_id: str, tenant_id: str) -> None:
    if not settings.auto_analyze_proctoring:
        logger.info("session.recording.vision_analysis_disabled",
                    session_id=session_id, reason="auto_analyze_proctoring=false")
        return
    from app.modules.vision import analyze_session_proctoring
    analyze_session_proctoring.send(session_id, tenant_id)
```

### B3. Not gated (intentional)

- **Recording egress** (auto on `/start`) — stays on. It's cheap relative to LLM/GPU and
  it's what makes a skipped session re-scorable later. Disabling it would defeat B1/B2's
  "re-scorable" property.
- **Reels** — already manual-only (`reel/router.py`); never auto-fire in test runs.

### B4. `.env.example`

Document both flags with their default and intent:

```
# Dev/test ergonomics — leave TRUE in every real environment.
# Set FALSE locally to skip post-session work during agent tuning runs.
AUTO_SCORE_SESSION_REPORTS=true   # false => skip the report LLM scorer (saves tokens)
AUTO_ANALYZE_PROCTORING=true      # false => skip vision gaze analysis (saves CPU/GPU)
```

---

## Testing & validation

### Automated
- `tests/ai/test_realtime_factories.py`:
  - `build_vad()` returns a Silero VAD (assert provider/type; mock the heavy load).
  - `build_noise_cancellation` test **removed**.
  - `build_interruption_options()` returns `mode="vad"` with the gates intact.
- `tests/test_audio_hints.py`: `_compute_audio_processing_hints()` →
  `noise_suppression=True`, `echo_cancellation=True`, `auto_gain_control=True`.
- New: `record_session_result` does **not** enqueue when
  `auto_score_session_reports=false` (and still commits the completion).
- New: `_enqueue_vision_analysis` is a no-op when `auto_analyze_proctoring=false`.
- Frontend `frontend/session`: update `tests/lib/api/audio-hints.test.ts` and
  `tests/components/interview/CameraMicStep.test.tsx` to expect `noiseSuppression: true`.

### Manual (operator)
- Boot `nexus-engine`: confirm a single `ai.realtime.vad.built provider=silero` line at
  prewarm, and **no** `ai_coustics` import anywhere.
- **Live talk-test** (the barge-in acceptance gate): the agent is cleanly interruptible
  on ≥2 words, does not yield to "mm/yeah", and resumes after a false interruption; no
  audible NC over-suppression of the candidate's own voice.
- With the toggles `false`: complete a session; confirm `report_scoring_disabled` /
  `vision_analysis_disabled` log lines and that **no** `report_scoring` / `vision`
  job is enqueued, while the session row still reaches `completed` with
  `coverage_summary` populated.

---

## Docs & compliance updates (part of this change)

- **New** this spec; add a `> Superseded by …` header note to
  `2026-05-06-audio-pipeline-design.md` (do not rewrite history).
- **Root `CLAUDE.md` → "Audio Path"**: `noiseSuppression` `false → true`; remove the
  "locked to LK Cloud / ai-coustics is not an EC" framing; note browser-native NS +
  VAD-mode barge-in.
- **`backend/nexus/CLAUDE.md`**: update the Phase 3D.audio-pipeline description and the
  `app/ai/realtime.py` blessed-import list (− `ai_coustics`, + `silero`; interruption
  now `mode="vad"`; `build_noise_cancellation` removed). Add the two dev toggles to the
  config notes.
- **`docs/security/threat-model.md`**: **remove ai-coustics as an audio-path
  sub-processor** — candidate audio no longer transits an external NC processor (a net
  reduction in data-path surface). Note the browser handles light NS locally.
- **`docs/deployment/2026-06-03-deployment-architecture-research.md`**: mark "Step 1 —
  decouple Cloud features" as in-progress/done (adaptive interruption removed;
  ai-coustics removed — only the own-key item is now moot since the dep is gone).

---

## Rollback

Pure config/dependency change, no migration. Rollback = revert the branch and
`uv.lock`. The two dev toggles default `True`, so even a partial revert leaves prod
behavior intact. No data shape changes; no session in flight is affected (the change is
read at process start / session start).

---

## File-change summary (blast radius)

| File | Change |
|---|---|
| `app/ai/realtime.py` | interruption `mode="vad"`; delete `build_noise_cancellation`; `build_vad`→Silero |
| `app/modules/interview_engine/agent.py` | prewarm-load Silero; drop NC call + `noise_cancellation=`; use `ctx.proc.userdata["vad"]` |
| `app/config.py` | remove NC config; add `auto_score_session_reports`, `auto_analyze_proctoring` |
| `app/modules/session/service.py` | `noise_suppression=True`; docstring; gate report enqueue |
| `app/modules/session/schemas.py` | `AudioProcessingHints` docstring |
| `app/modules/interview_runtime/service.py` | import `settings`; gate report enqueue on `auto_score_session_reports` |
| `app/modules/session/recording.py` | gate `_enqueue_vision_analysis` on `auto_analyze_proctoring` |
| `backend/nexus/pyproject.toml` + `uv.lock` | − ai-coustics, + silero |
| `backend/nexus/.env.example` | document both toggles |
| `frontend/session/lib/api/audio-hints.ts` | docstring only |
| `frontend/session/app/interview/[token]/CameraMicStep.tsx` | verify NS-on comment consistency |
| tests (backend + frontend) | as listed under Testing |
| docs (this spec, CLAUDE.md ×2, threat-model, audio-pipeline header, deployment) | as listed |
