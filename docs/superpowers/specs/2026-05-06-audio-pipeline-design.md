# Audio Pipeline — LK Cloud cutover, ai-coustics QUAIL_L, adaptive interruption, empirical tuning loop

**Status:** Draft for user review · **Date:** 2026-05-06 · **Supersedes the partial-rollback portion of:** `2026-05-03-engine-redesign-phase-6-audio-authority-design.md`

## Summary

The interview engine's audio pipeline is already near-best-practice on the
LiveKit framework side: Silero VAD prewarmed in process userdata,
`MultilingualModel` turn detector, dynamic endpointing, preemptive LLM
generation, false-interruption resume. What's been holding back the
candidate UX is **not the framework wiring** — it's three specific gaps
that come from running on **self-hosted LiveKit**:

1. **Mid-thought cut-offs** — the `MultilingualModel` `unlikely_threshold`
   knob is plumbed but defaulted to `None`; endpointing `max_delay` is at
   LK's 3.0s default; Silero's `min_silence_duration` is at LK's 0.55s
   default. None of these are tuned for interview-pace conversation where
   candidates routinely pause 4-8 seconds to think.
2. **Background noise** — self-hosted LK has no enhanced noise
   cancellation. The candidate browser does standard WebRTC
   `noiseSuppression`, which is decent for stationary noise but loses to
   non-stationary noise (typing, traffic, dog, fan). Phase 6 attempted to
   solve this with server-side ai-coustics SPARROW_S/0.4, but rolled
   back when the deployment target shifted to self-hosted.
3. **Backchannel interruptions** — `mode="vad"` interrupts the agent on
   any speech start of ≥0.5s, which fires on "uh-huh" and "right." LK
   Cloud's `mode="adaptive"` is a barge-in classifier specifically
   trained to filter these, but it's Cloud-only at production scale.

This spec resolves all three by **shifting the production target from
self-hosted LK to LiveKit Cloud**, with a fallback config switch so the
engine can still run self-hosted (e.g. for local dev or a future
tenant-VPC deployment). After this spec:

- A two-knob config switch (`interview_interruption_mode`,
  `interview_noise_cancellation`) on `AIConfig` lets the engine target
  either deployment mode without code changes.
- LK Cloud mode uses `mode="adaptive"` interruption + ai-coustics
  `QUAIL_L` background noise suppression at `enhancement_level=0.5`.
  Browser-side `noiseSuppression` flips OFF when server-side NC is on
  (avoids double-denoising); browser EC and AGC stay ON
  (load-bearing for full-duplex).
- Self-hosted mode keeps `mode="vad"` + `min_words=3` +
  `min_duration=0.8` to gate backchannel via STT-aligned word-count,
  and browser-side WebRTC NS/EC/AGC all on.
- The four interview-pace tuning knobs get production-grade defaults:
  `unlikely_threshold=0.15`, `endpointing.max_delay=6.0`,
  `silero.min_silence_duration=0.8`, `silero.activation_threshold=0.5`
  Cloud / `0.6` self-hosted.
- A new `audio_tuning_summary` JSONB column on `sessions` plus a
  `audio.tuning_summary` audit-log event captures pause distribution,
  interruption tally, latency P50/P95, and the config snapshot —
  enough to land empirical tuning deltas after ~50 sessions of real
  candidate data.

The frontend learns about the deployment mode through a new
`audio_processing_hints` field on the `/start` response — the candidate
session app stays dumb and trusts the bits.

## 1 — Decisions locked in this brainstorm

| # | Question | Decision |
|---|---|---|
| A1-Q1 | Self-hosted vs LK Cloud | **LK Cloud** as the production target. Self-hosted stays as a fallback runtime via env vars. The user has already updated `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` in `backend/nexus/.env` to point at a Cloud project. |
| A1-Q2 | Single deployment-mode flag, or per-behaviour knobs | **Per-behaviour knobs.** Two independent fields: `interview_interruption_mode` and `interview_noise_cancellation`. The combination defines the mode. Avoids "all-or-nothing flag flip" risk and lets dev mix configurations during tuning. |
| A1-Q3 | Where the new fields live (`AIConfig` or `settings`) | **`AIConfig`.** They're realtime-pipeline behaviours (same family as `interview_stt_model`, `interview_turn_detector_unlikely_threshold`), not engine-integration mechanics. Matches the documented `app/ai/realtime.py` carve-out in `backend/nexus/CLAUDE.md`. |
| A1-Q4 | Voice isolation vs background noise suppression | **Background noise suppression.** Voice isolation (Krisp BVC / ai-coustics QUAIL_VF_L) silently filters other voices in the room — a coach helping a candidate would be removed from the audio. That's compliance-relevant and we do not want to silently filter it. Default model: `ai_coustics.audio_enhancement(model=EnhancerModel.QUAIL_L, enhancement_level=0.5)`. Krisp NC stays in the enum as alternative. |
| A1-Q5 | Browser-side `getUserMedia` constraints when server NC is on | `noiseSuppression: false`, `echoCancellation: true`, `autoGainControl: true`. The first avoids double-denoising the ML model's training-distribution input; the latter two are load-bearing (EC for full-duplex feedback loop, AGC for input dynamic-range stability). The Phase 6 invariant of "all three off" was wrong because QUAIL_L is not an echo canceller. |
| A1-Q6 | Per-mode `min_words` for interruption gating | Asymmetric: `0` in adaptive mode (let the classifier work), `3` in vad mode (compensate for the missing classifier). If real Cloud-mode sessions show backchannel leakage, raising adaptive's `min_words` to `2` is a safe additive guard rail tunable via env. |
| A1-Q7 | `audio_tuning_summary` persistence — column or audit-envelope only | **Both.** Audit envelope gets the full `audio.tuning_summary` event (forensic / re-analyzable). A `sessions.audio_tuning_summary JSONB` column gets the same payload (queryable for tuning analysis without parsing S3 envelopes). New Alembic migration `0028_audio_tuning_summary`. |
| A1-Q8 | Frontend ↔ backend contract for the per-mode browser constraints | New `audio_processing_hints: {noise_suppression, echo_cancellation, auto_gain_control}` field on the `/start` response. Server is source of truth; frontend reads & passes into `getUserMedia` / LiveKit `AudioCaptureOptions`. |
| A1-Q9 | Browser-side enhanced NC (Krisp/ai-coustics frontend SDK) | **Out of scope, never enabled.** Per LiveKit docs, browser-side enhanced NC + server-side enhanced NC is forbidden. Standard WebRTC `noiseSuppression` is the only browser-side filter we ever toggle. |

## 2 — Scope

### 2.1 In scope

| Surface | Change |
|---|---|
| `app/ai/config.py` | Add 3 fields: `interview_interruption_mode`, `interview_noise_cancellation`, `interview_nc_enhancement_level`. |
| `app/ai/realtime.py` | Add `build_noise_cancellation()` and `build_interruption_options()` factories. Both are lazy-import (matches existing pattern). |
| `app/config.py` | Verify defaults — `engine_endpointing_max_delay=6.0`, `engine_silero_min_silence_duration=0.8`. Add `engine_silero_activation_threshold` env override if not present. |
| `app/modules/interview_engine/agent.py` | Replace inline `interruption=` dict with factory call. Add conditional `room_options=room_io.RoomOptions(audio_input=…)` on `session.start()`. Add `_compute_audio_tuning_summary` helper called from `_handle_close`. Add `nc_model`, `interruption_mode` to the `model_versions` envelope keys. |
| `app/modules/interview_engine/event_kinds.py` | Register `audio.tuning_summary` kind. |
| `app/modules/interview_runtime/schemas.py` | Add optional `audio_tuning_summary: dict \| None` field to `SessionResult`. |
| `app/modules/interview_runtime/service.py` | `record_session_result` writes the new column. |
| `app/modules/session/router.py` | `/start` response carries `audio_processing_hints`. |
| `app/modules/session/schemas.py` | New `AudioProcessingHints` Pydantic model. |
| `pyproject.toml` (engine) | Re-add `livekit-plugins-noise-cancellation`, `livekit-plugins-ai-coustics`. |
| `migrations/versions/0028_audio_tuning_summary.py` | NEW — adds `sessions.audio_tuning_summary JSONB DEFAULT NULL`. PG11+ metadata-only. New head: `0028_audio_tuning_summary`. |
| `frontend/session/lib/api/candidate-session.ts` | Extend `/start` response type with `audio_processing_hints`. |
| `frontend/session/components/interview/…` | Read hints, pass into `AudioCaptureOptions` / `getUserMedia({ audio: {...} })`. |
| `frontend/session/tests/audio-hints.test.tsx` (NEW) | Verify constraints flip with hints; CLAUDE.md says this file needs 100% branch coverage. |
| Doc punch-list | See §6. |

### 2.2 Out of scope

- **Browser-side enhanced NC plugins** (`@livekit/krisp-noise-filter`,
  `@livekit/plugins-ai-coustics`). Forbidden per LK docs when server-side
  NC is on. Mentioned only to be explicit.
- **Voice-isolation models** (Krisp BVC, ai-coustics QUAIL_VF_L). Compliance
  concern (silently filtering coaches); not appropriate for interview
  screening as default. Enum keeps `ai_coustics_quail_vf` as a future
  toggle; no migration of existing sessions.
- **The Phase 6 server-authoritative-audio invariant** (browser EC/NS/AGC
  all off). Echo cancellation has to live browser-side because that's
  the only side with both mic and speakers. Not revisiting in this spec.
- **ai-coustics VAD adapter** (replaces Silero VAD). Untested in our
  setup, different tuning surface, would change the audit envelope's
  `model_versions["vad"]` key. Documented as a future tuning option.
- **Auto-tuning service** (per-tenant config that adjusts knobs from
  observed pause statistics). Premature; needs ground-truth data first.
- **Selectors** (per-participant NC routing, Python-only). Moot in 1:1
  candidate:agent rooms. Documented for future multi-participant
  scenarios.
- **Real-time tuning dashboard.** YAGNI. Postgres + Jupyter is the loop.
- **`tenant_settings`-driven per-tenant audio config.** Deferrable.
  `AIConfig` is global; if one tenant's candidates pause notably
  differently, that's a future spec.

## 3 — Architecture & config switch

The two new `AIConfig` fields:

| Field | Type | Self-hosted default | LK Cloud value |
|---|---|---|---|
| `interview_interruption_mode` | `Literal["adaptive", "vad"]` | `"vad"` | `"adaptive"` |
| `interview_noise_cancellation` | `Literal["off", "ai_coustics_quail", "ai_coustics_quail_vf", "krisp_nc"]` | `"off"` | `"ai_coustics_quail"` |
| `interview_nc_enhancement_level` | `float` (0.0–1.0) | `0.5` | `0.5` |

The combination defines the deployment mode. No bool predicate like
`is_livekit_cloud` is exposed — calling code reads the individual fields.

The two new factories in `app/ai/realtime.py`:

```python
def build_noise_cancellation() -> "NoiseCancellation | None":
    nc = ai_config.interview_noise_cancellation
    if nc == "off":
        return None
    if nc == "ai_coustics_quail":
        from livekit.plugins import ai_coustics
        return ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_L,
            model_parameters=ai_coustics.ModelParameters(
                enhancement_level=ai_config.interview_nc_enhancement_level,
            ),
        )
    if nc == "ai_coustics_quail_vf":
        from livekit.plugins import ai_coustics
        return ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_VF_L,
            model_parameters=ai_coustics.ModelParameters(
                enhancement_level=ai_config.interview_nc_enhancement_level,
            ),
        )
    if nc == "krisp_nc":
        from livekit.plugins import noise_cancellation
        return noise_cancellation.NC()
    raise ValueError(f"Unknown interview_noise_cancellation: {nc}")


def build_interruption_options() -> dict:
    mode = ai_config.interview_interruption_mode
    if mode == "adaptive":
        return {
            "mode": "adaptive",
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }
    return {
        "mode": "vad",
        "min_duration": 0.8,
        "min_words": 3,
        "false_interruption_timeout": 2.5,
        "resume_false_interruption": True,
    }
```

Both factories follow the existing `app/ai/realtime.py` lazy-import
discipline — the `livekit.plugins.*` modules are imported inside the
factory, only when needed. A self-hosted deploy with
`interview_noise_cancellation="off"` never imports the Cloud-only plugin
packages.

The wiring change in `app/modules/interview_engine/agent.py` (currently
lines 260-301):

```python
session = AgentSession(
    stt=build_stt_plugin(),
    llm=build_llm_plugin(),
    tts=build_tts_plugin(),
    vad=ctx.proc.userdata["vad"],
    turn_handling=TurnHandlingOptions(
        turn_detection=build_turn_detector(),
        preemptive_generation={"enabled": True},
        endpointing={
            "mode": "dynamic",
            "min_delay": settings.engine_endpointing_min_delay,
            "max_delay": settings.engine_endpointing_max_delay,
        },
        interruption=build_interruption_options(),
    ),
)

nc = build_noise_cancellation()
await session.start(
    agent=agent,
    room=ctx.room,
    room_options=room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=nc,
        ),
    ) if nc else None,
)
```

## 4 — Turn detection & VAD tuning (the fix for "cuts candidates off mid-thought")

The hierarchy of levers, highest impact first, with proposed defaults:

| Knob | Source | Current | Proposed |
|---|---|---|---|
| `MultilingualModel.unlikely_threshold` | `AIConfig.interview_turn_detector_unlikely_threshold` | `None` (model default) | `0.15` |
| `endpointing.max_delay` | `settings.engine_endpointing_max_delay` | env (verify) | `6.0` |
| `silero.min_silence_duration` | `settings.engine_silero_min_silence_duration` | env (verify) | `0.8` |
| `silero.activation_threshold` | `settings.engine_silero_activation_threshold` | env (verify) | `0.5` Cloud / `0.6` self-hosted |

Rationale per knob:

- **`unlikely_threshold=0.15`** raises the EOU-confidence floor — when the
  multilingual model is unsure whether the candidate is done, it defers to
  VAD silence rules instead of forcing a turn-end. This is the
  context-aware lever; it reads the transcript and knows phrases like
  "I need to think about that for a moment" trail into silence rather
  than ending a thought. Currently `None` means "use model's internal
  default" — interviews benefit from being notably more patient.
- **`endpointing.max_delay=6.0`** raises the upper bound on dynamic
  endpointing from LK's 3.0s default. With dynamic mode (already on),
  the effective delay sweeps within `[min_delay, max_delay]` based on
  session pause-statistics EMA. A candidate who's been pausing 4-5s
  consistently gets ~4-5s of grace; one who's been pausing 0.5s gets
  ~0.5s. `min_delay` stays at `0.5` (LK default).
- **`min_silence_duration=0.8`** raises the floor on what Silero
  considers "speech ended" from LK's 0.55s default — a 600ms breath
  pause no longer triggers the EOU classifier in the first place.
- **`activation_threshold`** is one of the few values that should
  differ per deployment mode. With ai-coustics filtering input upstream
  (Cloud), the input is clean and the default 0.5 works. Self-hosted has
  no server-side denoiser, so 0.6 reduces noise-as-speech triggers.

These are starting values. Empirical tuning (§7) will land production-ready
numbers after the first 50 sessions of real candidate data.

## 5 — Interruption handling (the fix for backchannel)

**Cloud mode (`mode="adaptive"`).** Per LK docs, adaptive mode requires
"a turn detector model with an STT that supports aligned transcripts."
We meet both: `MultilingualModel` is configured, Deepgram supplies
word-level alignment. The barge-in classifier — trained on real
conversational audio specifically to separate intentional interruption
from "uh-huh / right / mm-hmm" backchannel — is the right tool.

**Self-hosted mode (`mode="vad"`).** No classifier; we gate manually:

- **`min_words=3`** — highest-impact lever. Requires STT alignment
  (Deepgram provides). 1-2 word "uh-huh" / "right" / "got it" no longer
  registers as an interruption.
- **`min_duration=0.8`** — tightens the speech-duration floor. Sub-800ms
  vocal noise (cough, mic bump, "oh") gets dropped.

**False-interruption recovery** — same posture in both modes:

- `resume_false_interruption=True` — already on
- `false_interruption_timeout=2.0` Cloud / `2.5` self-hosted (slightly
  more lenient on the noisier self-hosted signal path)
- `discard_audio_if_uninterruptible=True` — LK default, kept

**Asymmetry note (decision A1-Q6):** `min_words=0` in adaptive mode lets
the classifier do its full job; `min_words=3` in vad mode compensates
for the missing classifier. If Cloud-mode sessions show backchannel
leakage, raise adaptive's `min_words` to `2` as an additive guard rail
via env var.

## 6 — Noise cancellation (the fix for noisy candidates)

**Plugin choice — `ai-coustics QUAIL_L` at `enhancement_level=0.5`.**

The model lineup (correcting the stale `SPARROW_S` references in the
existing doc tree):

| Goal | Krisp | ai-coustics |
|---|---|---|
| Voice isolation (kill other voices, keep primary speaker) | `BVC()` | `EnhancerModel.QUAIL_VF_L` |
| Background noise suppression (kill non-speech only, keep all voices) | `NC()` | `EnhancerModel.QUAIL_L` |

Rationale:

- **Background noise suppression, not voice isolation** (decision A1-Q4):
  voice isolation silently filters a coach helping the candidate.
  Compliance issue.
- **ai-coustics over Krisp** for noise suppression: the docs' published
  gym-membership sample shows QUAIL_L producing clearly cleaner output
  than Krisp NC. ai-coustics also exposes an `enhancement_level` knob
  (0.0-1.0) that Krisp doesn't, giving us a tunable in addition to the
  per-mode toggle. Krisp NC stays in the enum as alternative for cases
  where ai-coustics' Cloud surface is unavailable.
- **Phase 6's `enhancement_level=0.4`** was conservative; the docs use
  `0.8` for their published samples. We start at `0.5` and tune.

**Browser-side audio constraints** (correcting the Phase 6 invariant
that turned all three off):

| Constraint | Self-hosted | Cloud (server NC on) | Why |
|---|---|---|---|
| `noiseSuppression` | `true` | **`false`** | Avoid double-denoising the ML model's training-distribution input. |
| `echoCancellation` | `true` | `true` | Load-bearing for full-duplex; QUAIL_L is not an EC. |
| `autoGainControl` | `true` | `true` | Stabilizes input dynamic range for the ML model. |

**Frontend contract — `audio_processing_hints` on `/start` response:**

```typescript
{
  audio_processing_hints: {
    noise_suppression: boolean,
    echo_cancellation: boolean,
    auto_gain_control: boolean,
  }
}
```

Server is source of truth; computed from `AIConfig` at request time.
Frontend passes the bits straight into `getUserMedia({ audio: ... })`
or LiveKit's `AudioCaptureOptions` during connection.

## 7 — Observability & the empirical tuning loop

Three additions land the loop. Existing `_wire_session_observability`
already captures most of the raw signal (turn-taking states, transcripts,
per-component metrics, false-interruption events) — these additions are
about persistence and post-hoc analysis.

### 7.1 — Per-session `audio.tuning_summary` event

Emitted in `_handle_close` before envelope finalization. Computed from
events already on the collector:

```python
{
    "pauses": {
        "between_utterance_ms": {"p50": int, "p95": int, "max": int, "n": int},
        "between_turn_ms":      {"p50": int, "p95": int, "max": int, "n": int},
    },
    "interruptions": {
        "total": int, "true": int, "false": int, "agent_yielded": int,
    },
    "latency": {
        "stt_to_eou_ms":         {"p50": int, "p95": int},
        "eou_to_first_audio_ms": {"p50": int, "p95": int},
    },
    "config_snapshot": {
        "interruption_mode": "adaptive" | "vad",
        "noise_cancellation": str,
        "nc_enhancement_level": float,
        "unlikely_threshold": float | None,
        "endpointing_max_delay": float,
        "silero_min_silence_duration": float,
        "silero_activation_threshold": float,
    },
}
```

The `config_snapshot` is load-bearing — without it, post-hoc analysis
cannot correlate outcomes to settings when defaults change over time.

### 7.2 — `sessions.audio_tuning_summary` JSONB column

New column persists the same payload as the audit-envelope event.
Migration `0028_audio_tuning_summary`. Lets a recruiter analyst (or the
solo developer in a notebook) run SQL aggregations directly without
S3-envelope parsing:

```sql
SELECT
  audio_tuning_summary->'config_snapshot'->>'interruption_mode' AS mode,
  AVG((audio_tuning_summary->'interruptions'->>'false')::int) AS false_per_session,
  PERCENTILE_CONT(0.95) WITHIN GROUP (
    ORDER BY (audio_tuning_summary->'pauses'->'between_turn_ms'->>'p95')::float
  ) AS p95_pause_ms
FROM sessions
WHERE created_at > now() - interval '30 days'
  AND audio_tuning_summary IS NOT NULL
GROUP BY mode;
```

### 7.3 — OTel span attributes

Add `set_llm_span_attributes()` calls that push the four core knobs
(`unlikely_threshold`, `endpointing.max_delay`, `min_words`,
`nc_enhancement_level`) as span attributes on the existing LLM/EOU
spans. Lets you slice latency/quality by config in your OTel sink
without joining to Postgres.

### 7.4 — The actual loop

1. Ship with §3-§6 defaults (Cloud + ai_coustics_quail + adaptive +
   `unlikely_threshold=0.15`, etc.).
2. After ~50 sessions, run a notebook over `audio_tuning_summary` rows.
3. Tune one knob at a time. Three diagnostic patterns:
   - **Pause P95 hits `max_delay`** → candidates *are* pausing that long;
     consider raising `max_delay` further, or `unlikely_threshold` to
     keep dynamic mode in range.
   - **Many false interruptions per session in vad mode** → raise
     `min_words` from 3 to 4. In adaptive mode, raise `min_words` from
     0 to 2 as a guard rail.
   - **Long `eou_to_first_audio_ms` tail** → consider enabling
     `preemptive_tts=True` (tradeoff: more wasted TTS spend on
     interrupted/changed turns, lower P95 user-perceived latency).
4. Ship deltas via env-var change. Compare next 50 sessions.

YAGNI-friendly: no fancy dashboard, no purpose-built ML, no auto-tuning
service. Postgres + Jupyter + a docstring.

## 8 — Files touched (full change set)

### Backend code

- `app/ai/config.py` — 3 new `AIConfig` fields + env bindings.
- `app/ai/realtime.py` — `build_noise_cancellation()`,
  `build_interruption_options()`. Both lazy-import.
- `app/config.py` — raise defaults for `engine_endpointing_max_delay`
  (→ 6.0), `engine_silero_min_silence_duration` (→ 0.8),
  `engine_silero_activation_threshold` (→ 0.5). All three already
  referenced in `agent.py:120-123, 270-271` so the settings keys
  already exist; this is a default-value change only.
- `app/modules/interview_engine/agent.py` — wire factories; add
  `room_options` conditional on `session.start()`; add
  `_compute_audio_tuning_summary` helper called from `_handle_close`;
  add `nc_model`, `interruption_mode`, knob values to `model_versions`.
- `app/modules/interview_engine/event_kinds.py` — register
  `audio.tuning_summary` kind.
- `app/modules/interview_runtime/schemas.py` — optional
  `audio_tuning_summary: dict | None` on `SessionResult`.
- `app/modules/interview_runtime/service.py` — `record_session_result`
  writes the new column.
- `app/modules/session/router.py` — `/start` response carries
  `audio_processing_hints`.
- `app/modules/session/schemas.py` — new `AudioProcessingHints` model.
- `pyproject.toml` (engine) — re-add
  `livekit-plugins-noise-cancellation`, `livekit-plugins-ai-coustics`.

### Migration

- `migrations/versions/0028_audio_tuning_summary.py` (NEW) — adds
  `sessions.audio_tuning_summary JSONB DEFAULT NULL`. PG11+
  metadata-only. Down-migration drops the column. New head:
  `0028_audio_tuning_summary`.

### Frontend (`frontend/session`)

- `lib/api/candidate-session.ts` — extend `/start` response type with
  `audio_processing_hints`.
- `components/interview/…` (room-connect code; exact path identified
  during implementation) — read hints, pass into `AudioCaptureOptions`
  / `getUserMedia`.
- `tests/audio-hints.test.tsx` (NEW) — verify constraints flip with
  hints. CLAUDE.md root requires 100% branch coverage on this surface.

### Env vars (additive — defaults preserve current behaviour)

```
INTERVIEW_INTERRUPTION_MODE=vad                  # default; flip to "adaptive" for Cloud
INTERVIEW_NOISE_CANCELLATION=off                 # default; flip to "ai_coustics_quail" for Cloud
INTERVIEW_NC_ENHANCEMENT_LEVEL=0.5               # tunable
INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD=0.15  # already exists; raise default null→0.15
ENGINE_ENDPOINTING_MAX_DELAY=6.0                 # already exists; raise default
ENGINE_SILERO_MIN_SILENCE_DURATION=0.8           # already exists; raise default
ENGINE_SILERO_ACTIVATION_THRESHOLD=0.5           # add as override knob
LIVEKIT_URL=wss://<your-project>.livekit.cloud   # already updated
LIVEKIT_API_KEY=<cloud-key>                      # already updated
LIVEKIT_API_SECRET=<cloud-secret>                # already updated
```

`backend/nexus/.env.example` updated to document each.

### Documentation cleanup

| File | Change |
|---|---|
| `backend/nexus/CLAUDE.md` | (a) Phase 3D.engine-redesign-6 entry: change "rolled back 2026-05-04" → "partially un-rolled-back 2026-05-06 (audio pipeline spec)" — production target shifted to **LK Cloud**, NC re-enabled via ai-coustics QUAIL_L; (b) correct the SPARROW_S → QUAIL_L model migration; (c) document the per-mode browser-side `getUserMedia` contract; (d) document `mode="adaptive"` re-enable in Cloud mode; (e) update migration list head from `0027_tenant_settings` → `0028_audio_tuning_summary`; (f) `app/ai/realtime.py` description updated for new factories. |
| `/home/ishant/Projects/ProjectX/CLAUDE.md` (root) | (a) "Audio Path" section: replace unconditional "browser EC/NS/AGC on" rule with the per-mode contract from §6; (b) Two-Tier Architecture table — LiveKit row clarifies day-1 deployment targets **LK Cloud**, not self-hosted; (c) Phase 3D row reflects the audio-pipeline-spec output. |
| `frontend/session/CLAUDE.md` | Document the `audio_processing_hints` contract on `/start`, with the per-mode default values and the rule "browser-side `noiseSuppression` is OFF when server-side NC is on; EC/AGC stay ON." |
| `frontend/session/AGENTS.md` | Same note for AI agents working in that surface. |
| `docs/security/threat-model.md` | (a) Add LK Cloud SFU as a sub-processor in the candidate-audio data path; (b) add ai-coustics as the ML provider running inside that path; (c) Phase 6 section: replace "rolled back" with "partially un-rolled-back, see [audio pipeline spec]"; (d) update the data-flow diagram to show audio routes through Cloud SFU. |
| `backend/nexus/.env.example` | New env vars documented inline. |

## 9 — Test plan

### Unit

- `tests/ai/test_realtime.py` — `build_noise_cancellation()` returns
  the right shape per `AIConfig` (one assertion per enum value); the
  `"off"` value returns `None` AND does NOT import the plugin module
  (assert `livekit.plugins.ai_coustics not in sys.modules` after call).
  Same for `build_interruption_options()`: assert the dict shape per
  mode, including the asymmetric `min_words` default.
- `tests/interview_engine/test_audio_tuning_summary.py` — feed a
  synthetic event sequence, assert summary numbers (pause percentiles,
  interruption tally, latency P50/P95). Specifically tests the
  `config_snapshot` block reflects the live `AIConfig` values.
- `tests/session/test_start_endpoint.py` — assert `audio_processing_hints`
  shape per `AIConfig` configuration; round-trip through the response
  schema.

### Frontend

- `frontend/session/tests/audio-hints.test.tsx` — assert constraints
  flip with hints (negative control: reintroduce the bug, watch the
  test fail, fix, watch it pass). 100% branch coverage required by
  root CLAUDE.md.

### Integration

- One full interview in self-hosted mode (env: `INTERVIEW_INTERRUPTION_MODE=vad`,
  `INTERVIEW_NOISE_CANCELLATION=off`, `LIVEKIT_URL=ws://localhost:7880`) —
  candidate finishes, `audio_tuning_summary` written to DB and audit
  envelope, no plugin imports happened.
- One full interview in Cloud mode (env: flipped to adaptive +
  `ai_coustics_quail`) — candidate finishes, summary lands, ai-coustics
  plugin imported and instantiated, browser receives `audio_processing_hints`
  with `noise_suppression=false`.

### Manual smoke

- Connect candidate UI in Cloud mode. Speak a phrase like "Let me
  think about that for a moment…" then pause 5 seconds, then continue.
  Verify the agent does NOT interrupt during the pause (lever:
  `unlikely_threshold=0.15` + `max_delay=6.0`).
- Mid-agent-speech, say "uh-huh." Verify the agent does NOT yield in
  Cloud mode (adaptive classifier filters it). In self-hosted mode,
  verify the same — `min_words=3` rejects the 1-2 word backchannel.
- Run a vacuum cleaner near the mic. Verify Cloud mode produces clean
  STT (ai-coustics filtering); self-hosted mode produces partially
  noisy STT (browser NS + WebRTC only).

## 10 — Open questions / future work

- **`tenant_settings.engine_audio_config`** — once enough sessions
  show diverging tuning needs by industry, lift `unlikely_threshold` /
  `max_delay` / `min_words` to per-tenant overrides. Would integrate
  with the existing `tenant_settings` table from migration 0027.
- **ai-coustics VAD adapter** — replaces Silero VAD with the VAD
  embedded in the ai-coustics ML pipeline. Possibly faster (one less
  model loaded) and possibly more accurate (operates on already-cleaned
  audio). Untested in our setup. Track for a post-50-sessions
  evaluation.
- **`preemptive_tts=True`** — the current `preemptive_generation`
  block has `enabled=True` but `preemptive_tts` is implicitly `False`.
  Flipping it to `True` reduces P95 user-perceived latency at the cost
  of wasted TTS spend on interrupted/changed turns. Add as an
  empirical-tuning candidate (§7.4).
- **`docs/onboarding/engine-redesign-full-arc-e2e.md`** — the audio
  scenarios (9a soft-spoken / 9b noisy-environment) were marked
  non-gating in the Phase 6 rollback note. With ai-coustics back in
  Cloud mode, those scenarios become gating again. Out of scope for
  this spec; lands when the e2e checklist is next touched.

---

## Acceptance gate

The spec lands when:
1. All §8 code, frontend, migration, and env changes are merged.
2. All §8 doc cleanups are committed.
3. A real interview session completes in Cloud mode and writes a
   well-formed `audio_tuning_summary` row to `sessions`.
4. The same session, replayed with `INTERVIEW_INTERRUPTION_MODE=vad`
   and `INTERVIEW_NOISE_CANCELLATION=off` overrides, also completes
   cleanly (proves the fallback config switch works).
