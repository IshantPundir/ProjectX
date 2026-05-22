# Interview Engine v2 — Milestone 3: Audio, EOU & Turn-Taking Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking. This is **M3** of the master plan
> (`2026-05-22-interview-engine-v2-master-plan.md`) — read its §2 (build order), §5 (M3), §3a CMI-3
> (instrumented latency gate), §6 (test/eval), §7 (R1 EOU · R3 LiveKit · R5 TTS) first. Match the M1
> plan's conventions (`2026-05-22-interview-engine-v2-m1-foundations.md`): TDD task shape, per-subagent
> git-scope guardrails, public-API imports, the livekit-free FastAPI process via lazy `__getattr__`.

**Goal:** Build the v2 conversation plane's **floor-control substrate** — recalibrated semantic EOU +
dynamic endpointing wired into a v2 `AgentSession`, the one-directional-interruption / floor-always-yields
invariant, a backchannel gate, the hold-space cue, the unresponsive ladder, a barge-in *classification
scaffold* (signals captured; attribution deferred to the M5 brain), and a v2 audio/latency summary
(CMI-3) — talk-testable end-to-end via a **canned listen-respond harness** (no brain, no mouth, no LLM)
so the user can confirm it waits on think-pauses, never talks over them, runs the unresponsive ladder,
and dumps a numeric audio summary within the §3 latency budget.

**Architecture:** The turn-taking *decision logic* lives in three **pure, livekit-free, unit-tested**
modules (`turn_taking/{floor,eou,pacing}.py`) plus a pure `audio_metrics.py` (CMI-3). The only
livekit-bearing code is `app/ai/realtime.py` (a minimal factory tweak so v2 tunes EOU independently of
v1) and the v2 `agent.py` harness, which wires a real `AgentSession` (STT+keyterms / VAD / tuned
turn-detector / v2 endpointing / adaptive interruption, **preemptive generation OFF**) and answers each
completed turn with the **next canned bank question** via `session.say()` + `raise StopResponse()`. The
behavioral layer (hold-space + unresponsive ladder) is a thin async silence-timer in the harness that
ticks the pure ladder/pacer and speaks cues via `session.say(..., add_to_chat_ctx=False)`. A wrong
real-time semantic classifier is **never** introduced — barge-in attribution is recorded as a scaffold
for the M5 brain (DESIGN-SPEC §4 / doc 08 key simplifier).

**Tech Stack:** Python 3.13, Pydantic v2 / dataclasses, pytest/pytest-asyncio, LiveKit Agents (Python,
`AgentSession` / `TurnHandlingOptions` / `MultilingualModel` / `on_user_turn_completed` / `StopResponse`),
`app/ai/realtime.*` (the blessed livekit-plugin site), `app/config.py` + `app/ai/config.py` (env knobs).

**Conventions to honor (from CLAUDE.md + memory + M1):**
- **Module public-API discipline:** cross-module imports go through `__init__.py`
  (`tests/test_module_boundaries.py` enforces it). The legacy `interview_engine/agent.py` already imports
  v2 only through its public API; M3 adds no new cross-module deep import. Inside `interview_engine_v2/`,
  intra-module deep imports (`from app.modules.interview_engine_v2.turn_taking.eou import ...`) are fine.
- **Keep the nexus/FastAPI process livekit-free.** `floor.py` / `eou.py` / `pacing.py` / `audio_metrics.py`
  are **pure** (no `livekit` import). Only `realtime.py` and `agent.py` touch livekit; `agent.run` stays
  the lazy `__getattr__` export.
- **NO regex for intent/understanding** (`feedback_no_regex`, DESIGN-SPEC §6). The backchannel gate is a
  tiny lowercased-token **allowlist + word-count + the turn-detector signal**, *not* `re`. Use plain
  `in`/`split()` checks. (Same sanctioned-backstop status as M1's no-leak validator.)
- **Quality before latency** (`feedback_quality_before_latency`): `preemptive_generation` stays **OFF**
  in v2 (memory: preemptive rejected). CMI-3 is a *numeric* gate, not a vibe — dump real percentiles.
- **`reasoning_effort` gating contract** (`app/ai/config.py` header) is untouched here (M3 sends no LLM
  calls — the harness uses `session.say()` only).
- Run backend commands in Docker from `backend/nexus/`: `docker compose run --rm nexus <cmd>` for one-shot,
  `docker compose exec -T nexus pytest <path> -v` when the stack is already up.

**Verified-live facts this plan is built on (do NOT re-derive; confirm only if a step fails):**
- STT is **already Deepgram nova-3 / `en-IN`** by default (`interview_stt_provider="deepgram"`,
  `interview_stt_model="nova-3"`). `SessionConfig.keyterms` is already populated by
  `build_session_config` from `stage_question_banks.extracted_keyterms`. **M3 does NOT change STT.** (The
  backend `CLAUDE.md` "STT stays Sarvam" line predates the 2026-05-19 Deepgram keyterm migration and is
  stale — do not act on it.)
- Dynamic endpointing already ships (`engine_endpointing_mode="dynamic"`, `min_delay=0.8`,
  `max_delay=4.5`) and `build_turn_detector()` already builds `MultilingualModel(unlikely_threshold=0.5)`.
  The M1 v2 proof-of-life wires **none** of this into its `AgentSession` — M3 wires it (with v2-specific
  knobs so v1 is byte-for-byte unchanged).
- v1 `agent.py` computes the audio summary in `_compute_audio_tuning_summary` / `_percentile_stats` /
  `_extract_ms` from `audio.metrics.{eou,llm,tts}_metrics` events emitted by a `metrics_collected`
  handler. v2 gets its **own copy** (CMI-3, survives the M6 v1 deletion).
- `app/ai/realtime.py` factories are global (engine-version-agnostic): `build_stt_plugin(keyterms=...)`,
  `build_tts_plugin()`, `build_vad()`, `build_turn_detector()`, `build_interruption_options()`,
  `build_noise_cancellation()`.

---

## File structure (M3)

```
backend/nexus/app/modules/interview_engine_v2/
├── turn_taking/
│   ├── floor.py        ← NEW: floor-yield invariant + barge-in resumption-classification SCAFFOLD (pure)
│   ├── eou.py          ← NEW: EouConfig + backchannel gate + UnresponsiveLadder (pure)
│   └── pacing.py       ← NEW: endpointing-config builder + HoldSpacePacer (pure)
├── audio_metrics.py    ← NEW: v2 copy of percentile/extract + audio-summary builder (pure; CMI-3)
└── agent.py            ← MODIFY: M1 proof-of-life → full canned listen-respond harness (livekit)

backend/nexus/app/
├── config.py           ← MODIFY: add engine_v2_* EOU/endpointing/behavioral knobs (Settings)
├── ai/config.py        ← MODIFY: AIConfig pass-through properties for the new knobs
└── ai/realtime.py      ← MODIFY: build_turn_detector() gains an optional unlikely_threshold override

backend/nexus/.env.example  ← MODIFY: document the new engine_v2_* env vars

backend/nexus/tests/interview_engine_v2/
├── test_config.py            ← EXTEND: assert the new engine_v2_* surface
├── test_turn_taking_floor.py     ← NEW
├── test_turn_taking_eou.py       ← NEW
├── test_turn_taking_pacing.py    ← NEW
├── test_audio_metrics.py         ← NEW
└── test_realtime_turn_detector.py← NEW (factory override; pure-ish, no AgentSession)
```

> **File-structure note vs master plan §4:** the master lists `turn_taking/{floor,eou,pacing}.py` for M3
> and the CMI-3 "own copy of `_percentile_stats`/`_extract_ms`". `audio_metrics.py` is the natural,
> self-contained home for that copy (it is not turn-taking logic), so it lands at the module top level.
> This is consistent with §4, not a deviation.

---

## Design decisions resolved in this plan (the brief asked the plan to resolve these)

1. **Harness shape (master §5 point 7).** M3 evolves the M1 `agent.py run()` proof-of-life into a
   **canned listen-respond harness**: a real `AgentSession` (STT+keyterms / VAD / tuned turn-detector /
   v2 endpointing / adaptive interruption, **preemptive OFF**) + a `_CannedBankAgent(Agent)` that, on
   each completed user turn, captures the utterance, runs the barge-in scaffold + writes an audit event,
   then speaks the **next bank question verbatim** via `session.say()` and `raise StopResponse()` (so no
   LLM is ever invoked). The behavioral layer is a single async silence timer that ticks the pure
   `HoldSpacePacer` + `UnresponsiveLadder` and voices cues via `session.say(..., add_to_chat_ctx=False)`.
   Talk-testing M3 = flip a throwaway job to `v2` and dial in (the exact M1 cutover seam). **M4 swaps the
   `_CannedBankAgent` for the real mouth; M5 adds the brain** — the AgentSession wiring + behavioral layer
   + metrics built here carry forward unchanged.
2. **Hold-space + unresponsive ladder are realtime *reflexes*, not brain Directives.** During a think-pause
   the brain isn't even called (doc 03/08) — the cue masks latency and keeps the candidate engaged. So the
   trigger + timing live in the M3 turn-taking layer and the cue text is a tunable Settings string. (M4's
   mouth *may* later re-voice HOLD/REASSURE in persona, but the trigger stays here.) **The two reflexes own
   disjoint silence:** the hold-space pacer fires only on a *mid-answer* pause (the candidate has begun
   answering this question, tracked via `started_answering`), and the unresponsive ladder owns *pre-answer*
   silence (the candidate hasn't started). They never stack on the same gap (plan fix #1).
3. **Backchannel gate + barge-in classifier are AUDIT/SCAFFOLD, never a real-time racer.** LiveKit's
   adaptive interruption + `min_words=2` already does the realtime yield. Our pure functions run
   **post-capture** (at `on_user_turn_completed`) to enrich the audit trail and hand structured signals to
   the M5 brain. This honors DESIGN-SPEC §4's "don't race a real-time classification we can't win."
4. **v2 tunes EOU independently of v1.** New `engine_v2_*` knobs default to v1's current values
   (`unlikely_threshold=0.5`, endpointing `[0.8, 4.5]` dynamic). `build_turn_detector()` gains an optional
   `unlikely_threshold` override with a sentinel default, so v1's no-arg call is byte-identical. This makes
   "M3 must not break v1" structural, and lets the user retune v2 on talk-tests without disturbing v1.
5. **EOU recalibration is empirical (R1).** This plan ships *the substrate + tunable knobs + the numeric
   gate*, with v1's values as the starting point. The actual Indian-ESL/Hinglish numbers are decided by
   the Task 9 talk-test (numbers, not vibes) — not blind-guessed in code.

---

## Task 1: `engine_v2_*` EOU / endpointing / behavioral config knobs

**Files:**
- Modify: `backend/nexus/app/config.py` (Settings — add after the `engine_endpointing_*` block, ~line 325)
- Modify: `backend/nexus/app/ai/config.py` (AIConfig — add after `engine_mouth_prompt_cache_key`, ~line 200)
- Modify: `backend/nexus/.env.example`
- Test: `backend/nexus/tests/interview_engine_v2/test_config.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/interview_engine_v2/test_config.py`:
```python
def test_engine_v2_eou_defaults():
    cfg = AIConfig()
    # EOU / endpointing — v2 starts at v1's current values (isolated knobs).
    assert cfg.engine_v2_turn_detector_unlikely_threshold == 0.5
    assert cfg.engine_v2_endpointing_mode == "dynamic"
    assert cfg.engine_v2_endpointing_min_delay == 0.8
    assert cfg.engine_v2_endpointing_max_delay == 4.5
    # Hold-space (mid-answer think pause).
    assert cfg.engine_v2_hold_space_enabled is True
    assert cfg.engine_v2_hold_space_delay_s == 2.5
    assert cfg.engine_v2_hold_space_message  # non-empty warm cue
    # Unresponsive ladder.
    assert cfg.engine_v2_unresponsive_prompt_1_s == 7.0
    assert cfg.engine_v2_unresponsive_prompt_2_s == 15.0
    assert cfg.engine_v2_unresponsive_max_no_responses == 2
    assert cfg.engine_v2_unresponsive_message_1
    assert cfg.engine_v2_unresponsive_message_2
    # Backchannel gate (mirrors the LiveKit interruption min_words).
    assert cfg.engine_v2_backchannel_min_words == 2


def test_engine_v2_eou_env_override(monkeypatch):
    monkeypatch.setenv("ENGINE_V2_TURN_DETECTOR_UNLIKELY_THRESHOLD", "0.35")
    monkeypatch.setenv("ENGINE_V2_ENDPOINTING_MAX_DELAY", "5.0")
    monkeypatch.setenv("ENGINE_V2_HOLD_SPACE_DELAY_S", "3.0")
    cfg = AIConfig()
    assert cfg.engine_v2_turn_detector_unlikely_threshold == 0.35
    assert cfg.engine_v2_endpointing_max_delay == 5.0
    assert cfg.engine_v2_hold_space_delay_s == 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_config.py -k engine_v2_eou -v`
Expected: FAIL — `AttributeError: 'AIConfig' object has no attribute 'engine_v2_turn_detector_unlikely_threshold'`.

- [ ] **Step 3: Add the Settings fields**

In `backend/nexus/app/config.py`, immediately after `engine_endpointing_max_delay` (~line 325), add:
```python
    # --- Interview engine v2 (two-plane) — EOU / turn-taking knobs ---
    # Isolated from the v1 engine_endpointing_* / interview_turn_detector_*
    # knobs so retuning v2 on talk-tests never changes v1 behavior (master §3
    # "M3 must not break v1"). Defaults intentionally match v1's current values;
    # the Indian-ESL/Hinglish recalibration is decided by the M3 talk-test
    # (master §7 R1), not guessed in code.
    engine_v2_turn_detector_unlikely_threshold: float | None = 0.5
    engine_v2_endpointing_mode: Literal["fixed", "dynamic"] = "dynamic"
    engine_v2_endpointing_min_delay: float = 0.8
    engine_v2_endpointing_max_delay: float = 4.5

    # Hold-space: one warm cue on a long MID-ANSWER pause (candidate is
    # formulating, turn-detector has not fired EOU). Never on a complete answer
    # (doc 08 "resolved"). Realtime reflex — NOT a brain directive. M4's mouth
    # may later re-voice this in persona; the trigger stays in the turn layer.
    engine_v2_hold_space_enabled: bool = True
    engine_v2_hold_space_delay_s: float = 2.5
    engine_v2_hold_space_message: str = "Take your time."

    # Unresponsive ladder: candidate not responding to a posed question.
    # ~7s -> gentle nudge; ~15s -> "still there?"; after N no-responses ->
    # close as candidate_unresponsive (doc 08 "resolved": ~6-8s / ~15s / 2).
    engine_v2_unresponsive_prompt_1_s: float = 7.0
    engine_v2_unresponsive_prompt_2_s: float = 15.0
    engine_v2_unresponsive_max_no_responses: int = 2
    engine_v2_unresponsive_message_1: str = "Whenever you're ready."
    engine_v2_unresponsive_message_2: str = "Are you still there?"

    # Backchannel gate: an utterance with fewer than this many words, OR made
    # entirely of backchannel tokens, is treated as engagement (AI keeps the
    # floor), not a turn grab. Mirrors the LiveKit interruption min_words=2.
    engine_v2_backchannel_min_words: int = 2
```
(`Literal` is already imported in this module.)

- [ ] **Step 4: Add the AIConfig pass-through properties**

In `backend/nexus/app/ai/config.py`, after the `engine_mouth_prompt_cache_key` property (~line 200), add:
```python
    # --- Interview engine v2 — EOU / turn-taking ---
    @property
    def engine_v2_turn_detector_unlikely_threshold(self) -> float | None:
        return self._settings.engine_v2_turn_detector_unlikely_threshold

    @property
    def engine_v2_endpointing_mode(self) -> str:
        return self._settings.engine_v2_endpointing_mode

    @property
    def engine_v2_endpointing_min_delay(self) -> float:
        return self._settings.engine_v2_endpointing_min_delay

    @property
    def engine_v2_endpointing_max_delay(self) -> float:
        return self._settings.engine_v2_endpointing_max_delay

    @property
    def engine_v2_hold_space_enabled(self) -> bool:
        return self._settings.engine_v2_hold_space_enabled

    @property
    def engine_v2_hold_space_delay_s(self) -> float:
        return self._settings.engine_v2_hold_space_delay_s

    @property
    def engine_v2_hold_space_message(self) -> str:
        return self._settings.engine_v2_hold_space_message

    @property
    def engine_v2_unresponsive_prompt_1_s(self) -> float:
        return self._settings.engine_v2_unresponsive_prompt_1_s

    @property
    def engine_v2_unresponsive_prompt_2_s(self) -> float:
        return self._settings.engine_v2_unresponsive_prompt_2_s

    @property
    def engine_v2_unresponsive_max_no_responses(self) -> int:
        return self._settings.engine_v2_unresponsive_max_no_responses

    @property
    def engine_v2_unresponsive_message_1(self) -> str:
        return self._settings.engine_v2_unresponsive_message_1

    @property
    def engine_v2_unresponsive_message_2(self) -> str:
        return self._settings.engine_v2_unresponsive_message_2

    @property
    def engine_v2_backchannel_min_words(self) -> int:
        return self._settings.engine_v2_backchannel_min_words
```

- [ ] **Step 5: Document the env vars in `.env.example`**

In `backend/nexus/.env.example`, near the `ENGINE_*` vars added in M1, add (commented, with defaults):
```bash
# Interview engine v2 — EOU / turn-taking (isolated from v1; tune on talk-tests).
ENGINE_V2_TURN_DETECTOR_UNLIKELY_THRESHOLD=0.5
ENGINE_V2_ENDPOINTING_MODE=dynamic
ENGINE_V2_ENDPOINTING_MIN_DELAY=0.8
ENGINE_V2_ENDPOINTING_MAX_DELAY=4.5
ENGINE_V2_HOLD_SPACE_ENABLED=true
ENGINE_V2_HOLD_SPACE_DELAY_S=2.5
ENGINE_V2_HOLD_SPACE_MESSAGE=Take your time.
ENGINE_V2_UNRESPONSIVE_PROMPT_1_S=7.0
ENGINE_V2_UNRESPONSIVE_PROMPT_2_S=15.0
ENGINE_V2_UNRESPONSIVE_MAX_NO_RESPONSES=2
ENGINE_V2_UNRESPONSIVE_MESSAGE_1=Whenever you're ready.
ENGINE_V2_UNRESPONSIVE_MESSAGE_2=Are you still there?
ENGINE_V2_BACKCHANNEL_MIN_WORDS=2
```

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_config.py -v`
Expected: PASS (existing M1 config tests + the two new ones).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/.env.example backend/nexus/tests/interview_engine_v2/test_config.py
git commit -m "feat(engine-v2): add isolated v2 EOU/endpointing/behavioral config knobs"
```

---

## Task 2: `turn_taking/pacing.py` — endpointing config builder + hold-space pacer

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/turn_taking/pacing.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_turn_taking_pacing.py`

> Pure + livekit-free. `build_endpointing_options()` produces the plain dict the AgentSession
> `TurnHandlingOptions(endpointing=...)` expects (built in the harness, Task 7). `HoldSpacePacer` decides
> whether a *mid-answer* silence has crossed the hold-space threshold and whether the one-time cue is
> still owed for the current pause (doc 08: one warm cue per long pause, never on a complete answer).

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_turn_taking_pacing.py`:
```python
"""pacing.py — endpointing config + hold-space pacer (pure, no livekit)."""

from app.modules.interview_engine_v2.turn_taking.pacing import (
    EndpointingSettings,
    HoldSpacePacer,
    build_endpointing_options,
)


def test_build_endpointing_options_shape():
    opts = build_endpointing_options(
        EndpointingSettings(mode="dynamic", min_delay=0.8, max_delay=4.5)
    )
    assert opts == {"mode": "dynamic", "min_delay": 0.8, "max_delay": 4.5}


def test_hold_space_fires_once_after_threshold():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    pacer.on_pause_started(at_s=10.0)
    assert pacer.cue_due(now_s=12.0) is False        # 2.0s < 2.5s
    assert pacer.cue_due(now_s=12.6) is True          # crossed 2.5s
    pacer.mark_cued()
    assert pacer.cue_due(now_s=20.0) is False          # only once per pause


def test_hold_space_resets_on_resume():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    pacer.on_pause_started(at_s=10.0)
    assert pacer.cue_due(now_s=13.0) is True
    pacer.mark_cued()
    pacer.on_resume()                                   # candidate spoke again
    pacer.on_pause_started(at_s=20.0)                   # a new pause
    assert pacer.cue_due(now_s=23.0) is True            # cue owed again


def test_hold_space_disabled_never_fires():
    pacer = HoldSpacePacer(enabled=False, delay_s=2.5)
    pacer.on_pause_started(at_s=0.0)
    assert pacer.cue_due(now_s=100.0) is False


def test_cue_due_false_when_no_pause_open():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    assert pacer.cue_due(now_s=5.0) is False            # no pause started
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_turn_taking_pacing.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `pacing.py`**

`backend/nexus/app/modules/interview_engine_v2/turn_taking/pacing.py`:
```python
"""Pacing — dynamic endpointing config + the hold-space pause reflex.

Pure (no livekit, no LLM). The harness (agent.py) builds the LiveKit
TurnHandlingOptions endpointing dict from build_endpointing_options(), and ticks
HoldSpacePacer off a silence timer to decide when to speak ONE warm "take your
time" cue on a long mid-answer pause. The turn-detector + endpointing decide
when the answer is actually COMPLETE; the pacer only fires while the turn is
still open (candidate formulating), so it never lands on a complete answer
(DESIGN-SPEC §3, doc 08 "resolved").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointingSettings:
    """The three values LiveKit's endpointing dict needs (DESIGN-SPEC §3)."""

    mode: str            # "dynamic" (per-candidate adaptive) | "fixed"
    min_delay: float
    max_delay: float


def build_endpointing_options(settings: EndpointingSettings) -> dict[str, object]:
    """Render the plain dict passed to TurnHandlingOptions(endpointing=...)."""
    return {
        "mode": settings.mode,
        "min_delay": settings.min_delay,
        "max_delay": settings.max_delay,
    }


class HoldSpacePacer:
    """Owes at most one hold-space cue per open mid-answer pause.

    Lifecycle, driven by the harness off user-state transitions:
      - on_pause_started(at_s): candidate stopped speaking, turn still open.
      - cue_due(now_s): True once `delay_s` has elapsed and the cue is unspent.
      - mark_cued(): record that the cue was spoken for this pause.
      - on_resume(): candidate started speaking again -> clear pause state.
    """

    def __init__(self, *, enabled: bool, delay_s: float) -> None:
        self._enabled = enabled
        self._delay_s = delay_s
        self._pause_started_at: float | None = None
        self._cued_this_pause = False

    def on_pause_started(self, at_s: float) -> None:
        self._pause_started_at = at_s
        self._cued_this_pause = False

    def on_resume(self) -> None:
        self._pause_started_at = None
        self._cued_this_pause = False

    def cue_due(self, now_s: float) -> bool:
        if not self._enabled or self._pause_started_at is None or self._cued_this_pause:
            return False
        return (now_s - self._pause_started_at) >= self._delay_s

    def mark_cued(self) -> None:
        self._cued_this_pause = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_turn_taking_pacing.py -v`
Expected: PASS (all five).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/turn_taking/pacing.py backend/nexus/tests/interview_engine_v2/test_turn_taking_pacing.py
git commit -m "feat(engine-v2): turn_taking pacing — endpointing builder + hold-space pacer"
```

---

## Task 3: `turn_taking/eou.py` — backchannel gate + unresponsive ladder

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/turn_taking/eou.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_turn_taking_eou.py`

> Pure + livekit-free. `is_backchannel()` is a sanctioned **allowlist + word-count** check (NOT regex —
> `feedback_no_regex`). `UnresponsiveLadder` is the ~7s -> ~15s -> close state machine (doc 08
> "resolved"). The harness ticks the ladder off the silence timer and speaks each rung's cue via
> `session.say(..., add_to_chat_ctx=False)`; on a real response it calls `reset()`.

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_turn_taking_eou.py`:
```python
"""eou.py — backchannel gate + unresponsive ladder (pure, no livekit, no regex)."""

import pytest

from app.modules.interview_engine_v2.turn_taking.eou import (
    BACKCHANNEL_TOKENS,
    EouConfig,
    LadderAction,
    UnresponsiveLadder,
    is_backchannel,
)


@pytest.mark.parametrize("text", ["yeah", "haan", "mm", "right", "ok", "achha", "hmm"])
def test_single_backchannel_token_is_backchannel(text):
    assert is_backchannel(text, min_words=2) is True


@pytest.mark.parametrize("text", ["yeah yeah", "haan haan", "mm hmm", "ok ok"])
def test_multiword_all_backchannel_tokens_is_backchannel(text):
    # >= 2 words but EVERY word is a backchannel token -> still engagement.
    assert is_backchannel(text, min_words=2) is True


@pytest.mark.parametrize("text", [
    "I built the billing sync",
    "yeah so I migrated the connector",   # starts with a token but is a real clause
    "no I haven't used Java",
])
def test_real_clause_is_not_backchannel(text):
    assert is_backchannel(text, min_words=2) is False


def test_empty_or_blank_is_backchannel():
    # nothing meaningful spoken -> treat as non-turn (keep the floor / ignore).
    assert is_backchannel("", min_words=2) is True
    assert is_backchannel("   ", min_words=2) is True


def test_backchannel_tokens_includes_indian_english():
    for tok in ("haan", "achha", "theek"):
        assert tok in BACKCHANNEL_TOKENS


def _ladder() -> UnresponsiveLadder:
    return UnresponsiveLadder(
        EouConfig(prompt_1_s=7.0, prompt_2_s=15.0, max_no_responses=2)
    )


def test_ladder_rungs_in_order():
    lad = _ladder()
    lad.on_question_posed(at_s=0.0)
    assert lad.action(now_s=3.0) is LadderAction.NONE       # before rung 1
    assert lad.action(now_s=7.5) is LadderAction.PROMPT_1   # rung 1
    assert lad.action(now_s=8.0) is LadderAction.NONE       # already fired rung 1
    assert lad.action(now_s=15.5) is LadderAction.PROMPT_2  # rung 2 == 1 no-response
    assert lad.action(now_s=16.0) is LadderAction.NONE


def test_ladder_two_no_responses_closes():
    lad = _ladder()
    # 1st posed question goes fully unanswered through both rungs.
    lad.on_question_posed(at_s=0.0)
    assert lad.action(now_s=7.5) is LadderAction.PROMPT_1
    assert lad.action(now_s=15.5) is LadderAction.PROMPT_2   # no-response #1 recorded
    # re-posed (same or next question), again unanswered to rung 2.
    lad.on_question_posed(at_s=20.0)
    assert lad.action(now_s=27.5) is LadderAction.PROMPT_1
    assert lad.action(now_s=35.5) is LadderAction.CLOSE_UNRESPONSIVE  # no-response #2 -> close


def test_ladder_reset_on_response_clears_state():
    lad = _ladder()
    lad.on_question_posed(at_s=0.0)
    assert lad.action(now_s=7.5) is LadderAction.PROMPT_1
    lad.on_candidate_responded()       # real answer arrived
    lad.on_question_posed(at_s=10.0)
    assert lad.action(now_s=13.0) is LadderAction.NONE     # timer restarted from 10.0
    assert lad.action(now_s=17.5) is LadderAction.PROMPT_1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_turn_taking_eou.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `eou.py`**

`backend/nexus/app/modules/interview_engine_v2/turn_taking/eou.py`:
```python
"""EOU behavioral layer — backchannel gate + unresponsive ladder (pure).

No livekit, no LLM, NO regex (DESIGN-SPEC §6 / feedback_no_regex). The backchannel
gate is a sanctioned tiny allowlist + word-count heuristic (a non-authoritative
realtime signal, exactly like M1's no-leak token list — not intent classification).
The unresponsive ladder is the doc-08 "resolved" reflex: ~7s gentle nudge -> ~15s
"still there?" -> after N no-responses, close as candidate_unresponsive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Lowercased single-token backchannels (English + common Indian-English). A turn
# made entirely of these (any length) is engagement, not a turn grab. Kept tight
# to avoid swallowing real one-word answers like "no"/"yes" to a yes/no probe.
BACKCHANNEL_TOKENS: frozenset[str] = frozenset({
    "yeah", "yep", "yup", "ok", "okay", "right", "sure", "uh-huh", "uhhuh",
    "mm", "mmm", "mhm", "hmm", "haan", "han", "achha", "accha", "theek",
    "thik", "cool", "got", "gotcha",
})


def _words(text: str) -> list[str]:
    return [w for w in text.strip().lower().split() if w]


def is_backchannel(text: str, *, min_words: int) -> bool:
    """True if `text` should NOT grab the floor (engagement / silence).

    Backchannel when: blank, OR fewer than `min_words` words, OR every word is a
    backchannel token. A clause with any non-backchannel word is a real turn.
    """
    words = _words(text)
    if not words:
        return True
    if len(words) < min_words:
        return True
    stripped = [w.strip(".,!?-") for w in words]
    return all(w in BACKCHANNEL_TOKENS for w in stripped if w)


@dataclass(frozen=True)
class EouConfig:
    prompt_1_s: float
    prompt_2_s: float
    max_no_responses: int


class LadderAction(str, Enum):
    NONE = "none"
    PROMPT_1 = "prompt_1"               # ~7s gentle nudge
    PROMPT_2 = "prompt_2"               # ~15s "still there?"  (== one no-response)
    CLOSE_UNRESPONSIVE = "close_unresponsive"


class UnresponsiveLadder:
    """Tracks silence after a posed question and escalates per doc 08.

    Reaching PROMPT_2 (the candidate ignored a question through both rungs) counts
    as one no-response. After `max_no_responses`, the next PROMPT_2 boundary
    returns CLOSE_UNRESPONSIVE instead. A real response resets everything.
    """

    def __init__(self, config: EouConfig) -> None:
        self._config = config
        self._posed_at: float | None = None
        self._fired_1 = False
        self._fired_2 = False
        self._no_responses = 0

    def on_question_posed(self, at_s: float) -> None:
        self._posed_at = at_s
        self._fired_1 = False
        self._fired_2 = False

    def on_candidate_responded(self) -> None:
        self._posed_at = None
        self._fired_1 = False
        self._fired_2 = False
        self._no_responses = 0

    def action(self, now_s: float) -> LadderAction:
        if self._posed_at is None:
            return LadderAction.NONE
        elapsed = now_s - self._posed_at
        if not self._fired_2 and elapsed >= self._config.prompt_2_s:
            self._fired_2 = True
            self._no_responses += 1
            if self._no_responses >= self._config.max_no_responses:
                return LadderAction.CLOSE_UNRESPONSIVE
            return LadderAction.PROMPT_2
        if not self._fired_1 and elapsed >= self._config.prompt_1_s:
            self._fired_1 = True
            return LadderAction.PROMPT_1
        return LadderAction.NONE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_turn_taking_eou.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/turn_taking/eou.py backend/nexus/tests/interview_engine_v2/test_turn_taking_eou.py
git commit -m "feat(engine-v2): turn_taking eou — backchannel gate + unresponsive ladder"
```

---

## Task 4: `turn_taking/floor.py` — yield invariant + barge-in classification scaffold

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/turn_taking/floor.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_turn_taking_floor.py`

> Pure + livekit-free. `should_yield()` documents+tests the one-directional invariant (AI yields to any
> genuine speech; LiveKit's adaptive interruption is what *enforces* it at runtime). `classify_resumption()`
> is the doc-08 flowchart as a **scaffold**: it maps already-captured signals to a provisional label that
> is recorded to the audit trail. It is **NOT** consulted to decide whether to yield (the AI always
> yields) — the M5 brain does the authoritative attribution. This is the "don't race a real-time
> classification we can't win" simplifier (DESIGN-SPEC §4).

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_turn_taking_floor.py`:
```python
"""floor.py — yield invariant + barge-in resumption SCAFFOLD (pure, no livekit)."""

from app.modules.interview_engine_v2.turn_taking.floor import (
    ResumptionLabel,
    ResumptionSignals,
    classify_resumption,
    should_yield,
)


def test_yields_on_genuine_speech():
    assert should_yield(word_count=4, is_backchannel=False) is True


def test_does_not_yield_on_backchannel():
    assert should_yield(word_count=1, is_backchannel=True) is False
    assert should_yield(word_count=3, is_backchannel=True) is False  # "yeah yeah yeah"


def test_continuation_when_prior_incomplete_and_quick_resume():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=False, gap_ms=900,
        ai_prompt_fully_delivered=False, word_count=6, is_backchannel=False,
    ))
    assert label is ResumptionLabel.CONTINUATION


def test_barge_in_when_ai_prompt_was_cut_off():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=True, gap_ms=3000,
        ai_prompt_fully_delivered=False, word_count=5, is_backchannel=False,
    ))
    assert label is ResumptionLabel.BARGE_IN


def test_early_answer_when_prompt_delivered_and_prior_complete():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=True, gap_ms=2500,
        ai_prompt_fully_delivered=True, word_count=8, is_backchannel=False,
    ))
    assert label is ResumptionLabel.EARLY_ANSWER


def test_backchannel_short_circuits():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=True, gap_ms=500,
        ai_prompt_fully_delivered=True, word_count=1, is_backchannel=True,
    ))
    assert label is ResumptionLabel.BACKCHANNEL


def test_scaffold_label_is_advisory_only():
    # Negative control: the function is pure data->label; it must expose NO
    # side effect / no "should the AI yield" coupling. should_yield ignores it.
    assert should_yield(word_count=6, is_backchannel=False) is True  # regardless of label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_turn_taking_floor.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `floor.py`**

`backend/nexus/app/modules/interview_engine_v2/turn_taking/floor.py`:
```python
"""Floor control — the one-directional-interruption invariant + a barge-in
resumption-classification SCAFFOLD (pure, no livekit).

INVARIANT (DESIGN-SPEC §4, doc 08 "resolved"): the candidate may interrupt the AI;
the AI NEVER interrupts the candidate, and yields to any genuine speech (>= min
words, not a backchannel). At runtime LiveKit's adaptive interruption enforces the
yield; `should_yield` is the pure statement of the rule (used for audit + tests).

SCAFFOLD (doc 08): `classify_resumption` maps signals already captured at the turn
boundary to a provisional continuation/early/barge-in/backchannel label. It is
recorded for audit and consumed later by the M5 brain (which does the AUTHORITATIVE,
semantic attribution by meaning). It MUST NOT gate realtime behavior — we never race
a real-time classifier (the old continuation-watcher's mistake). The AI yields
regardless of the label.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Resume within this gap, while the AI had only just started, reads as the candidate
# finishing their own prior thought (doc 03 O5 / doc 08). Tunable later if needed.
_CONTINUATION_GAP_MS = 1500


class ResumptionLabel(str, Enum):
    BACKCHANNEL = "backchannel"
    CONTINUATION = "continuation"
    EARLY_ANSWER = "early_answer"
    BARGE_IN = "barge_in"


@dataclass(frozen=True)
class ResumptionSignals:
    """Signals captured at the boundary (none individually decisive — doc 08)."""

    prior_utterance_complete: bool   # turn-detector view of the prior candidate turn
    gap_ms: int                      # ms from prior candidate EOU to this resume
    ai_prompt_fully_delivered: bool  # had the AI finished delivering its line?
    word_count: int
    is_backchannel: bool


def should_yield(*, word_count: int, is_backchannel: bool) -> bool:
    """The AI yields the floor to any genuine speech (not a backchannel)."""
    return word_count >= 1 and not is_backchannel


def classify_resumption(signals: ResumptionSignals) -> ResumptionLabel:
    """Provisional label for the audit trail (doc 08 flowchart). ADVISORY ONLY."""
    if signals.is_backchannel:
        return ResumptionLabel.BACKCHANNEL
    if not signals.prior_utterance_complete and signals.gap_ms <= _CONTINUATION_GAP_MS:
        return ResumptionLabel.CONTINUATION
    if not signals.ai_prompt_fully_delivered:
        # candidate spoke before the AI finished its new prompt -> cannot be an
        # answer to that prompt; it's a go-back / repair / continuation -> barge-in.
        return ResumptionLabel.BARGE_IN
    return ResumptionLabel.EARLY_ANSWER
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_turn_taking_floor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/turn_taking/floor.py backend/nexus/tests/interview_engine_v2/test_turn_taking_floor.py
git commit -m "feat(engine-v2): turn_taking floor — yield invariant + barge-in scaffold"
```

---

## Task 5: `audio_metrics.py` — v2 audio/latency summary (CMI-3)

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/audio_metrics.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_audio_metrics.py`

> Pure + livekit-free. v2's **own copy** of the percentile/extract math + the summary builder, operating
> on the v2 `event_log` events (the same `audio.metrics.*` payload shapes the AgentSession emits, collected
> in Task 7). Survives the M6 v1 deletion. The harness logs this dict at session close so a talk-test
> yields the numeric CMI-3 gate (perceived response = mouth `llm_ttft_ms` + `tts_ttfb_ms`; here, with no
> mouth, the focus is `end_of_utterance_delay_ms` patient-but-not-laggy + `tts_ttfb_ms` for the canned say).

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_audio_metrics.py`:
```python
"""audio_metrics.py — percentile math + v2 audio summary (pure; CMI-3)."""

from app.modules.interview_engine_v2.audio_metrics import (
    compute_audio_summary,
    extract_ms,
    percentile_stats,
)


def test_percentile_stats_empty():
    assert percentile_stats([]) == {"p50": 0, "p95": 0, "max": 0, "n": 0}


def test_percentile_stats_odd_and_even():
    assert percentile_stats([100, 200, 300]) == {"p50": 200, "p95": 300, "max": 300, "n": 3}
    # even n -> mean of the two middle values (matches v1 _percentile_stats)
    out = percentile_stats([100, 200, 300, 400])
    assert out["p50"] == 250 and out["max"] == 400 and out["n"] == 4


def test_extract_ms_filters_and_scales():
    events = [
        {"kind": "audio.metrics.eou_metrics", "payload": {"end_of_utterance_delay": 0.9}},
        {"kind": "audio.metrics.eou_metrics", "payload": {"end_of_utterance_delay": 0}},   # dropped
        {"kind": "audio.metrics.eou_metrics", "payload": {"end_of_utterance_delay": None}}, # dropped
    ]
    assert extract_ms(events, "end_of_utterance_delay") == [900]


def test_compute_audio_summary_shape():
    events = [
        {"kind": "audio.metrics.eou_metrics",
         "payload": {"end_of_utterance_delay": 1.1, "transcription_delay": 0.2}},
        {"kind": "audio.metrics.tts_metrics", "payload": {"ttfb": 0.3}},
        {"kind": "audio.metrics.llm_metrics", "payload": {"ttft": 0.15}},
    ]
    summary = compute_audio_summary(events=events, config_snapshot={"endpointing_mode": "dynamic"})
    assert summary["latency"]["end_of_utterance_delay_ms"]["p50"] == 1100
    assert summary["latency"]["tts_ttfb_ms"]["p50"] == 300
    assert summary["latency"]["llm_ttft_ms"]["p50"] == 150
    assert summary["config"] == {"endpointing_mode": "dynamic"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_audio_metrics.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `audio_metrics.py`**

`backend/nexus/app/modules/interview_engine_v2/audio_metrics.py`:
```python
"""v2 audio / latency summary (CMI-3). Pure copy of the v1 percentile math so v2
survives the M6 deletion of interview_engine/. Operates on the v2 event-log events
(EventLogEvent.model_dump(mode="json") shape: {"kind", "payload", ...}); the
AgentSession emits the same `audio.metrics.{eou,llm,tts}_metrics` payloads v1 used.
"""

from __future__ import annotations

from typing import Any


def percentile_stats(values: list[int]) -> dict[str, int]:
    """p50/p95/max/n for an int list (true median for even n) — matches v1."""
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "n": 0}
    s = sorted(values)
    n = len(s)
    p50 = (s[n // 2 - 1] + s[n // 2]) // 2 if n % 2 == 0 else s[n // 2]
    p95 = s[min(n - 1, int(n * 0.95))]
    return {"p50": p50, "p95": p95, "max": s[-1], "n": n}


def extract_ms(events: list[dict[str, Any]], field: str) -> list[int]:
    """Pull a positive float `field` (seconds) from each event payload -> ms."""
    out: list[int] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        val = payload.get(field)
        if isinstance(val, (int, float)) and val > 0:
            out.append(int(val * 1000))
    return out


def compute_audio_summary(
    *, events: list[dict[str, Any]], config_snapshot: dict[str, object],
) -> dict[str, object]:
    """Aggregate latency percentiles from audio.metrics.* events (CMI-3 gate)."""
    eou = [e for e in events if e.get("kind") == "audio.metrics.eou_metrics"]
    llm = [e for e in events if e.get("kind") == "audio.metrics.llm_metrics"]
    tts = [e for e in events if e.get("kind") == "audio.metrics.tts_metrics"]
    return {
        "latency": {
            "end_of_utterance_delay_ms": percentile_stats(extract_ms(eou, "end_of_utterance_delay")),
            "transcription_delay_ms": percentile_stats(extract_ms(eou, "transcription_delay")),
            "llm_ttft_ms": percentile_stats(extract_ms(llm, "ttft")),
            "tts_ttfb_ms": percentile_stats(extract_ms(tts, "ttfb")),
        },
        "config": dict(config_snapshot),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_audio_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/audio_metrics.py backend/nexus/tests/interview_engine_v2/test_audio_metrics.py
git commit -m "feat(engine-v2): v2 audio/latency summary (CMI-3, own percentile copy)"
```

---

## Task 6: `build_turn_detector()` optional threshold override (v1 stays byte-identical)

**Files:**
- Modify: `backend/nexus/app/ai/realtime.py` (`build_turn_detector`)
- Test: `backend/nexus/tests/interview_engine_v2/test_realtime_turn_detector.py`

> So v2 can pass its own `engine_v2_turn_detector_unlikely_threshold` without touching v1's no-arg call.
> A sentinel default preserves v1 behavior exactly (the existing `None` value is meaningful — it means
> "use the MultilingualModel default" — so `None` cannot be the sentinel).

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_realtime_turn_detector.py`:
```python
"""build_turn_detector accepts an explicit threshold override (v2) while the
no-arg v1 call still reads AIConfig. The MultilingualModel itself pulls in
livekit native deps, so we patch it to capture the constructor kwargs without
loading the plugin.
"""

import sys
import types

import pytest


@pytest.fixture
def captured(monkeypatch):
    """Stub livekit.plugins.turn_detector.multilingual.MultilingualModel."""
    calls: list[dict] = []

    class _FakeModel:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    mod = types.ModuleType("livekit.plugins.turn_detector.multilingual")
    mod.MultilingualModel = _FakeModel
    # ensure parent packages resolve for the `from ... import` inside the factory
    for name in ("livekit", "livekit.plugins", "livekit.plugins.turn_detector"):
        sys.modules.setdefault(name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "livekit.plugins.turn_detector.multilingual", mod)
    return calls


def test_v1_call_reads_aiconfig_default(captured, monkeypatch):
    from app.ai import realtime
    monkeypatch.setattr(realtime.ai_config, "_settings", realtime.ai_config._settings)
    realtime.build_turn_detector()  # no arg -> AIConfig (default 0.5)
    assert captured[-1] == {"unlikely_threshold": 0.5}


def test_v2_override_used(captured):
    from app.ai import realtime
    realtime.build_turn_detector(unlikely_threshold=0.35)
    assert captured[-1] == {"unlikely_threshold": 0.35}


def test_explicit_none_uses_model_default(captured):
    from app.ai import realtime
    realtime.build_turn_detector(unlikely_threshold=None)
    assert captured[-1] == {}   # MultilingualModel() with no kwargs
```

> If `realtime.ai_config._settings` indirection is awkward in the first test, instead
> `monkeypatch.setenv("INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD", "0.5")` and rebuild AIConfig — but the
> default is already 0.5, so the plain no-arg call asserting `{"unlikely_threshold": 0.5}` is sufficient.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_realtime_turn_detector.py -v`
Expected: FAIL — `build_turn_detector()` takes no arguments / override not honored.

- [ ] **Step 3: Add the override parameter**

In `backend/nexus/app/ai/realtime.py`, replace the `build_turn_detector` body with a sentinel-defaulted
parameter (keep the docstring; add the override note):
```python
_USE_AICONFIG_THRESHOLD = object()  # module-level sentinel


def build_turn_detector(
    unlikely_threshold: "float | None | object" = _USE_AICONFIG_THRESHOLD,
) -> "TurnDetectionMode":
    """Construct the LiveKit multilingual turn-detector model.

    (... keep the existing docstring ...)

    `unlikely_threshold`: omit (sentinel) to read
    `AIConfig.interview_turn_detector_unlikely_threshold` — the v1 path, byte-for-byte
    unchanged. Pass an explicit float (or None for the model default) to override —
    the v2 engine passes `AIConfig.engine_v2_turn_detector_unlikely_threshold` so it
    tunes EOU independently of v1.
    """
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    if unlikely_threshold is _USE_AICONFIG_THRESHOLD:
        unlikely_threshold = ai_config.interview_turn_detector_unlikely_threshold
    if unlikely_threshold is None:
        return MultilingualModel()
    return MultilingualModel(unlikely_threshold=unlikely_threshold)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_realtime_turn_detector.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm v1's call site is unchanged**

The v1 `interview_engine/agent.py` calls `build_turn_detector()` with no argument — it now resolves the
sentinel and reads AIConfig exactly as before. Verify no other call site passes positional args:
Run: `docker compose run --rm nexus bash -lc "grep -rn 'build_turn_detector(' app/ | grep -v test"`
Expected: only `interview_engine/agent.py` (no-arg) for now; the v2 call is added in Task 7.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/ai/realtime.py backend/nexus/tests/interview_engine_v2/test_realtime_turn_detector.py
git commit -m "feat(engine-v2): build_turn_detector optional threshold override (v1 unchanged)"
```

---

## Task 7: Wire the v2 canned listen-respond harness in `agent.py`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine_v2/agent.py` (replace the M1 proof-of-life `run()`)
- Test: `backend/nexus/tests/interview_engine_v2/test_harness_script.py` (pure helpers only)

> This is the one livekit-bearing task and the one that merges tightly-coupled pieces (AgentSession wiring
> + the canned-responder Agent + the behavioral-layer silence timer + metrics collection + the audio
> summary dump) so there is no broken intermediate commit (M2 lesson). The **pure** parts (the bank
> script + the v2 keyterm assembler) are extracted as testable helpers; the livekit wiring is verified by
> the Task 9 talk-test (the user's primary method — there is no way to unit-test a live room).
>
> **R3 reminder:** LiveKit is fast-evolving. The APIs below were confirmed against the docs MCP on
> 2026-05-22 (`AgentSession`, `TurnHandlingOptions`, `on_user_turn_completed`, `StopResponse`,
> `session.say(add_to_chat_ctx=...)`, `user_state_changed`, `metrics_collected`). If a signature differs
> in the installed version, re-check via `docs_search` before adapting — do not guess.

- [ ] **Step 1: Write the failing test for the pure helpers**

`backend/nexus/tests/interview_engine_v2/test_harness_script.py`:
```python
"""Pure helpers behind the M3 harness: the bank script + v2 keyterm assembler."""

from app.modules.interview_engine_v2.agent import (
    BankScript,
    assemble_v2_keyterms,
)


def test_bank_script_advances_then_finishes():
    script = BankScript(intro="Hi, I'm Sam.", questions=["Q1?", "Q2?"], closing="Thanks!")
    assert script.next_line() == "Hi, I'm Sam."   # intro first
    assert script.next_line() == "Q1?"
    assert script.next_line() == "Q2?"
    assert script.next_line() == "Thanks!"        # closing
    assert script.is_terminal_line is True
    assert script.next_line() is None              # nothing after close


def test_bank_script_empty_bank_goes_intro_then_close():
    script = BankScript(intro="Hi.", questions=[], closing="Bye.")
    assert script.next_line() == "Hi."
    assert script.next_line() == "Bye."
    assert script.is_terminal_line is True


def test_assemble_v2_keyterms_dedup_and_cap():
    terms = assemble_v2_keyterms(candidate_first_name="Ravi", bank_keyterms=["Workato", "ravi", "iPaaS"])
    assert terms[0] == "Ravi"                 # candidate name first
    assert "Workato" in terms and "iPaaS" in terms
    # case-insensitive dedup: "ravi" collides with "Ravi"
    assert sum(1 for t in terms if t.lower() == "ravi") == 1
    assert len(terms) <= 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_harness_script.py -v`
Expected: FAIL — `BankScript` / `assemble_v2_keyterms` not importable.

- [ ] **Step 3: Replace `agent.py` with the harness**

Replace `backend/nexus/app/modules/interview_engine_v2/agent.py` with:
```python
"""Interview Engine v2 — LiveKit entrypoint (M3 canned listen-respond harness).

M3 scope: the floor-control SUBSTRATE, talk-testable with NO brain and NO mouth.
A real AgentSession (STT+keyterms / VAD / tuned turn-detector / v2 dynamic
endpointing / adaptive interruption, preemptive generation OFF) listens; on each
completed user turn a _CannedBankAgent captures the utterance, records the
barge-in scaffold + audit, then speaks the NEXT bank question verbatim via
session.say() and raises StopResponse() (so no LLM is ever invoked). A silence
timer ticks the pure HoldSpacePacer + UnresponsiveLadder and voices cues via
session.say(add_to_chat_ctx=False). At close, the v2 audio summary is logged
(CMI-3). The mouth lands in M4, the brain in M5 — both reuse this wiring.

Imports livekit; only ever imported lazily via interview_engine_v2.__getattr__('run')
inside the engine container, so the FastAPI/nexus process never loads livekit.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    MetricsCollectedEvent,
    StopResponse,
    TurnHandlingOptions,
    UserStateChangedEvent,
    room_io,
)

from app.ai.config import ai_config
from app.ai.realtime import (
    build_interruption_options,
    build_noise_cancellation,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
    build_vad,
)
from app.config import settings
from app.modules.interview_engine_v2.audio_metrics import compute_audio_summary
from app.modules.interview_engine_v2.event_log.collector import EventCollector
from app.modules.interview_engine_v2.turn_taking.eou import (
    EouConfig,
    LadderAction,
    UnresponsiveLadder,
    is_backchannel,
)
from app.modules.interview_engine_v2.turn_taking.floor import (
    ResumptionSignals,
    classify_resumption,
    should_yield,
)
from app.modules.interview_engine_v2.turn_taking.pacing import (
    EndpointingSettings,
    HoldSpacePacer,
    build_endpointing_options,
)
from app.modules.interview_runtime.schemas import SessionConfig

log = structlog.get_logger("interview_engine_v2")

_KEYTERM_CAP = 50


def assemble_v2_keyterms(*, candidate_first_name: str, bank_keyterms: list[str]) -> list[str]:
    """v2-native keyterm pass (self-contained; no import of interview_engine/).

    Candidate first name + cached bank keyterms, case-insensitive dedup, capped.
    """
    terms: list[str] = []

    def _add(term: str) -> None:
        t = term.strip()
        if not t or len(terms) >= _KEYTERM_CAP:
            return
        if any(t.lower() == x.lower() for x in terms):
            return
        terms.append(t)

    if candidate_first_name.strip():
        _add(candidate_first_name.split()[0])
    for term in bank_keyterms:
        _add(term)
    return terms


@dataclass
class BankScript:
    """Linear canned script: intro -> each bank question -> closing (no brain)."""

    intro: str
    questions: list[str]
    closing: str
    _idx: int = field(default=0, init=False)
    is_terminal_line: bool = field(default=False, init=False)

    def next_line(self) -> str | None:
        lines = [self.intro, *self.questions, self.closing]
        if self._idx >= len(lines):
            return None
        line = lines[self._idx]
        self._idx += 1
        self.is_terminal_line = self._idx >= len(lines)
        return line


def _now_ms() -> int:
    return int(time.time() * 1000)


class _CannedBankAgent(Agent):
    """Answers each completed turn with the next bank line. No LLM is invoked."""

    def __init__(self, *, script: BankScript, collector: EventCollector,
                 ladder: UnresponsiveLadder, started_at: float,
                 state: dict[str, object],
                 pose_question: Callable[[float], None]) -> None:
        super().__init__(instructions="")
        self._script = script
        self._collector = collector
        self._ladder = ladder
        self._started_at = started_at
        self._state = state               # shared behavioral-timer state (fix #2)
        self._pose_question = pose_question

    def _t_ms(self) -> int:
        return int((time.monotonic() - self._started_at) * 1000)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        # Fix #2: hold the floor against the silence timer while we deliver the
        # next line, so a ladder/hold cue can never overlap the canned question.
        self._state["responding"] = True
        try:
            text = new_message.text_content or ""
            word_count = len([w for w in text.split() if w])
            backchannel = is_backchannel(
                text, min_words=settings.engine_v2_backchannel_min_words)

            # A real response resets the unresponsive ladder (no-response count -> 0).
            if should_yield(word_count=word_count, is_backchannel=backchannel):
                self._ladder.on_candidate_responded()

            # Barge-in SCAFFOLD: record a provisional label for the M5 brain.
            # Advisory only — never used to decide whether to yield (AI always yields).
            label = classify_resumption(ResumptionSignals(
                prior_utterance_complete=True,        # best-effort in M3; refined in M5
                gap_ms=0,
                ai_prompt_fully_delivered=True,
                word_count=word_count,
                is_backchannel=backchannel,
            ))
            self._collector.record(
                "turn.captured",
                {"word_count": word_count, "is_backchannel": backchannel,
                 "resumption_label": label.value},
                t_ms=self._t_ms(), wall_ms=_now_ms(),
            )

            # Deliver the next canned bank line, then re-arm the ladder for the NEW
            # question via _pose_question (resets started_answering + the pacer too).
            # The terminal closing line poses nothing -> leave the ladder disarmed.
            line = self._script.next_line()
            if line is not None:
                await self.session.say(line, add_to_chat_ctx=True)
                if not self._script.is_terminal_line:
                    self._pose_question(time.monotonic())
        finally:
            self._state["responding"] = False
        raise StopResponse()


async def run(
    ctx: JobContext,
    config: SessionConfig,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """v2 per-session run. M3: canned listen-respond floor-control harness."""
    started_at = time.monotonic()
    collector = EventCollector(
        session_id=config.session_id,
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    collector.record(
        "engine.v2.dispatched",
        {"job_title": config.job_title, "question_count": len(config.stage.questions)},
        t_ms=0, wall_ms=_now_ms(),
    )

    await ctx.connect()
    await ctx.wait_for_participant()

    keyterms = assemble_v2_keyterms(
        candidate_first_name=config.candidate.name,
        bank_keyterms=list(config.keyterms),
    )
    endpointing = build_endpointing_options(EndpointingSettings(
        mode=ai_config.engine_v2_endpointing_mode,
        min_delay=ai_config.engine_v2_endpointing_min_delay,
        max_delay=ai_config.engine_v2_endpointing_max_delay,
    ))
    session = AgentSession(
        stt=build_stt_plugin(keyterms=keyterms),
        tts=build_tts_plugin(),
        vad=build_vad(),
        # Fix #3: own unresponsive behavior in ONE place. The manual ladder gives
        # the multi-rung + close-after-N semantics that LiveKit's away timeout
        # (a single state flip) cannot express, so disable the framework timeout
        # (docs: "Set to None to turn off") and let UnresponsiveLadder run it.
        # (R3: confirm None disables it cleanly in the installed version.)
        user_away_timeout=None,
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(
                unlikely_threshold=ai_config.engine_v2_turn_detector_unlikely_threshold,
            ),
            preemptive_generation={"enabled": False},   # quality-before-latency lock
            endpointing=endpointing,
            interruption=build_interruption_options(),
        ),
    )

    # --- metrics collection (CMI-3): mirror v1's audio.metrics.* events ---
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        collector.record(f"audio.metrics.{m.type}", payload,
                         t_ms=int((time.monotonic() - started_at) * 1000),
                         wall_ms=_now_ms())

    # --- behavioral layer: one silence timer ticks the pure pacer + ladder ---
    ladder = UnresponsiveLadder(EouConfig(
        prompt_1_s=ai_config.engine_v2_unresponsive_prompt_1_s,
        prompt_2_s=ai_config.engine_v2_unresponsive_prompt_2_s,
        max_no_responses=ai_config.engine_v2_unresponsive_max_no_responses,
    ))
    pacer = HoldSpacePacer(
        enabled=ai_config.engine_v2_hold_space_enabled,
        delay_s=ai_config.engine_v2_hold_space_delay_s,
    )
    # started_answering: has the candidate begun answering the CURRENT question?
    #   reset False by _pose_question; set True on the first 'speaking' after it.
    #   Routes silence (fix #1): PRE-answer silence -> unresponsive ladder;
    #   MID-answer pause (started_answering) -> hold-space pacer.
    # responding (fix #2): the agent is delivering a canned line -> the silence
    #   loop must not speak a cue over it.
    state: dict[str, object] = {
        "started_answering": False, "responding": False,
        "closing": False, "silence_task": None,
    }

    def _pose_question(at_s: float) -> None:
        """Arm the ladder for a freshly-posed question and reset turn state."""
        ladder.on_question_posed(at_s=at_s)
        pacer.on_resume()                  # no open hold-space window yet
        state["started_answering"] = False

    async def _silence_watch() -> None:
        """Tick the pacer (mid-answer) OR the ladder (pre-answer) while silent."""
        try:
            while not state["closing"]:
                await asyncio.sleep(0.5)
                if state["responding"] or state["closing"]:
                    continue                      # fix #2: don't speak over the agent
                now = time.monotonic()
                if state["started_answering"]:
                    # Mid-answer think-pause -> at most one hold-space cue.
                    if pacer.cue_due(now_s=now):
                        pacer.mark_cued()
                        state["responding"] = True
                        try:
                            await session.say(settings.engine_v2_hold_space_message,
                                              add_to_chat_ctx=False)
                        finally:
                            state["responding"] = False
                    continue
                # Pre-answer silence -> the unresponsive ladder owns it (fix #1/#3).
                action = ladder.action(now_s=now)
                if action is LadderAction.NONE:
                    continue
                state["responding"] = True
                try:
                    if action is LadderAction.PROMPT_1:
                        await session.say(settings.engine_v2_unresponsive_message_1,
                                          add_to_chat_ctx=False)
                    elif action is LadderAction.PROMPT_2:
                        await session.say(settings.engine_v2_unresponsive_message_2,
                                          add_to_chat_ctx=False)
                        # Re-pose so a STILL-silent candidate accrues a 2nd
                        # no-response and the ladder can reach CLOSE (the pure
                        # ladder counts per-question; one permanently-silent
                        # question alone never closes — re-posing here drives the
                        # 2nd cycle without needing a completed turn).
                        ladder.on_question_posed(at_s=now)
                    elif action is LadderAction.CLOSE_UNRESPONSIVE:
                        state["closing"] = True
                        collector.record("engine.v2.candidate_unresponsive", {},
                                         t_ms=int((now - started_at) * 1000),
                                         wall_ms=_now_ms())
                        await session.say(settings.engine_v2_unresponsive_message_2,
                                          add_to_chat_ctx=False)
                        await session.aclose()
                finally:
                    state["responding"] = False
        except asyncio.CancelledError:
            pass

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        now = time.monotonic()
        if ev.new_state == "speaking":
            state["started_answering"] = True   # fix #1: this turn is now an answer
            pacer.on_resume()                    # any speech clears a hold-space window
        elif ev.new_state == "listening":
            # Open a hold-space window ONLY if the candidate has begun answering;
            # pre-answer silence belongs to the ladder, not the pacer (fix #1).
            if state["started_answering"]:
                pacer.on_pause_started(at_s=now)
        collector.record("audio.user.state",
                         {"old_state": ev.old_state, "new_state": ev.new_state},
                         t_ms=int((now - started_at) * 1000), wall_ms=_now_ms())

    script = BankScript(
        intro=(f"Hi {config.candidate.name or 'there'}. I'm {settings.engine_agent_name}, "
               f"and I'll be running this screening. Let's get started."),
        questions=[q.text for q in config.stage.questions],
        closing="That's everything from my side. Thanks for your time today.",
    )
    agent = _CannedBankAgent(script=script, collector=collector, ladder=ladder,
                             started_at=started_at, state=state,
                             pose_question=_pose_question)

    nc_filter = build_noise_cancellation()
    await session.start(
        agent=agent, room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(noise_cancellation=nc_filter),
        ),
    )

    # Deliver the intro + first question, then arm the ladder on the first question.
    await session.say(script.next_line() or "", add_to_chat_ctx=True)   # intro
    first_q = script.next_line()
    if first_q is not None:
        await session.say(first_q, add_to_chat_ctx=True)
    # Arm the ladder + reset started_answering for the first question. (If the
    # candidate barged in on the intro, this resets that stray 'speaking' flag,
    # so the ladder governs pre-answer silence on the real first question.)
    _pose_question(time.monotonic())

    state["silence_task"] = asyncio.create_task(_silence_watch())

    @session.on("close")
    def _on_close(_ev: object) -> None:
        state["closing"] = True
        env = collector.envelope()
        summary = compute_audio_summary(
            events=[e.model_dump(mode="json") for e in env.events],
            config_snapshot={
                "endpointing_mode": ai_config.engine_v2_endpointing_mode,
                "endpointing_min_delay": ai_config.engine_v2_endpointing_min_delay,
                "endpointing_max_delay": ai_config.engine_v2_endpointing_max_delay,
                "turn_detector_unlikely_threshold":
                    ai_config.engine_v2_turn_detector_unlikely_threshold,
            },
        )
        # CMI-3: the talk-test reads these numbers from the engine logs.
        log.info("engine.v2.audio_tuning_summary", **summary)

    try:
        await ctx.room.local_participant.set_attributes({"session_outcome": "completed"})
    except Exception:  # noqa: BLE001
        log.warning("engine.v2.session_outcome.publish_failed", exc_info=True)
```

> **Notes for the implementer — R3 verification (verify against the installed livekit version; do not
> guess; the Task 9 talk-test is the gate, per fix #4):**
> - **No `llm=` + always-`StopResponse` never generates a reply.** `_CannedBankAgent.on_user_turn_completed`
>   raises `StopResponse()` on every turn, so `llm_node` is never reached. Confirm this in a talk-test
>   (the agent only ever speaks canned bank text + cue strings; it never free-generates). If the installed
>   version *requires* an `llm` at `AgentSession` construction, pass `build_llm_plugin()` (it will never be
>   invoked because of `StopResponse`) and note it — do NOT let the harness generate a real reply.
> - **`session.say(..., add_to_chat_ctx=False)` mid-listen for cues is accepted and does not corrupt turn
>   state.** Confirm the hold-space / ladder cues don't get re-ingested as a user turn or reset the
>   turn-detector (talk-test: a cue fires, then the candidate's subsequent speech is still captured as the
>   next turn). If a mid-listen `say` is rejected or confuses state in the installed version, re-check via
>   `docs_search "session.say while listening"` before adapting.
> - **`user_away_timeout=None` disables the framework away-timeout** (docs: "Set to None to turn off").
>   Confirm no `away` state auto-fires (the manual ladder owns unresponsive). If `None` is not accepted in
>   the installed version, set it large enough to never precede the manual ladder and note it (fix #3).
> - `await session.say(...)` returns a `SpeechHandle`; awaiting it blocks until playout. The intro+Q1
>   sequence awaits each so they don't overlap. `allow_interruptions` defaults `True` (candidate can barge
>   in on the intro — correct per the one-directional invariant; `_pose_question` then resets the stray
>   `started_answering` flag).
> - The `close` event handler signature varies; `_ev: object` is defensive. If logging the summary inside
>   a sync handler is awkward, compute it after `await session.start(...)` returns / the run unwinds
>   instead — the requirement is only that the summary is logged once per session.
> - `time.monotonic()` is the clock for elapsed-silence math; `wall_ms` stays epoch-ms for the envelope
>   (matches v1).

- [ ] **Step 4: Run the pure-helper test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_harness_script.py -v`
Expected: PASS (the `BankScript` + `assemble_v2_keyterms` tests; the livekit wiring is talk-tested in Task 9).

- [ ] **Step 5: Confirm the package still imports + pure artifacts stay livekit-free**

Run: `docker compose run --rm nexus python -c "from app.modules.interview_engine_v2 import Directive, DirectiveController; print('pure ok'); from app.modules.interview_engine_v2.turn_taking import eou, floor, pacing; from app.modules.interview_engine_v2 import audio_metrics; print('turn-taking + metrics import without livekit')"`
Expected: prints both lines (no `ImportError` for livekit — the pure modules must not import it).

- [ ] **Step 6: Boundary lint + full v2 suite**

Run: `docker compose run --rm nexus pytest tests/test_module_boundaries.py tests/interview_engine_v2 -v`
Expected: PASS (no illegal cross-module deep import; all M1 + M3 unit tests green).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/agent.py backend/nexus/tests/interview_engine_v2/test_harness_script.py
git commit -m "feat(engine-v2): M3 canned listen-respond harness (audio + EOU + behavioral layer)"
```

---

## Task 8: TTS voice listen-test + decision (Sarvam vs OpenAI) — R5

**Files:**
- Create: `docs/superpowers/notes/2026-05-22-engine-v2-tts-voice-decision.md` (decision record)
- (No production code — TTS provider is an `AIConfig`/env swap; both paths already exist in `realtime.py`.)

> R5 / master §5 deliverable 6. The default is Sarvam `bulbul:v3` / speaker `shubh`; OpenAI
> `gpt-4o-mini-tts` is the alternate. This is a *listening* decision, recorded — not a code change.

- [ ] **Step 1: Listen-test Sarvam (default)**

Talk to the M3 harness (Task 9 procedure) on the default config and judge the Indian-English voice on:
naturalness, warmth, number/acronym pronunciation (e.g. "REST", "API", "twenty twenty-four"), and TTS
TTFB from the audio summary (`tts_ttfb_ms` p50/p95).

- [ ] **Step 2: Listen-test OpenAI**

Restart the engine with the OpenAI TTS env (per `realtime.py` `_build_tts_openai` + the `.env.example`
contract):
```bash
# in backend/nexus/.env (local dev only)
INTERVIEW_TTS_PROVIDER=openai
INTERVIEW_TTS_MODEL=gpt-4o-mini-tts
INTERVIEW_TTS_VOICE=ash
```
Then `docker compose restart nexus-engine` and re-run the talk-test. Compare on the same criteria.

- [ ] **Step 3: Record the decision**

Write `docs/superpowers/notes/2026-05-22-engine-v2-tts-voice-decision.md` with: the two configs tested,
subjective notes, the `tts_ttfb_ms` numbers from each summary, and the chosen provider/voice with a
one-line rationale. Set the chosen value as the v2 default (it is already the `interview_tts_*` default if
Sarvam wins; if OpenAI wins, update the defaults in `app/config.py` + `.env.example` in this task's commit).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/notes/2026-05-22-engine-v2-tts-voice-decision.md
# (+ app/config.py + .env.example only if the default changed)
git commit -m "docs(engine-v2): TTS voice listen-test decision (Sarvam vs OpenAI)"
```

---

## Task 9: Manual talk-test + instrumented latency gate (CMI-3) + v1 regression

**Files:** none (verification + acceptance). This is the user's primary validation method
(`feedback_manual_agent_testing`) — there is no CI eval suite here.

- [ ] **Step 1: Flip a throwaway test job to v2**

Pick a test job that has a **confirmed AI-screening bank** (so `build_session_config` produces questions
+ keyterms). Flip it:
```bash
docker compose run --rm nexus python -c "import asyncio; \
from app.database import get_bypass_session; from sqlalchemy import text; \
async def go():\n  async with get_bypass_session() as db:\n    await db.execute(text(\"UPDATE job_postings SET interview_engine_version='v2' WHERE id=:j\"), {'j': '<TEST_JOB_UUID>'});\n    await db.commit();\nasyncio.run(go())"
```

- [ ] **Step 2: Restart the engine + dial in**

```bash
docker compose up --build -d
docker compose restart nexus-engine     # load the M3 harness code
docker compose logs -f nexus-engine     # watch engine.v2.* events live
```
Dial the candidate session for that job and talk to it.

- [ ] **Step 3: Run the talk-test checklist (doc 08 scenarios)**

Confirm, by talking:
- **Think-pause:** pause ~2-4s mid-answer → the agent **waits** (no talk-over) and emits **one** warm
  "take your time" cue on a long pause; never on a complete answer.
- **[fix #1 acceptance] No hold-space on pre-answer silence:** right after the agent poses a question, stay
  silent → it must **NOT** say "take your time" at ~2.5s. Only the unresponsive ladder may speak
  ("whenever you're ready" at ~7s). The hold-space cue fires **only** once you've begun answering and then
  pause mid-thought.
- **[fix #2] No overlapping cues:** answer just as a ladder rung would fire → the next question is delivered
  cleanly, with no "whenever you're ready" spoken over it.
- **Never interrupts:** ramble for 60-90s → the agent does **not** cut in; it waits for your boundary.
- **Yield on barge-in:** start talking while the agent is delivering a question → it **yields** immediately.
- **Backchannel:** say "haan"/"yeah" while it talks → it **keeps the floor** (no spurious yield).
- **Unresponsive ladder:** go silent after a question → ~7s "whenever you're ready" → ~15s "are you still
  there?" → after 2 no-responses the session closes as `candidate_unresponsive` (see the log event).
- **EOU feel:** snappy on a clearly-finished answer; patient on a trailing "...I worked on, uh—".

- [ ] **Step 4: CMI-3 numeric latency gate — dump + read the audio summary**

On session close, the engine logs `engine.v2.audio_tuning_summary`. Capture it:
```bash
docker compose logs nexus-engine | grep audio_tuning_summary | tail -1
```
Confirm the numbers (NOT "feels conversational"):
- `latency.end_of_utterance_delay_ms` p50/p95 — **patient but not laggy** (within the v2 endpointing
  `[min_delay, max_delay]` band; recalibrate `ENGINE_V2_*` thresholds and re-test if EOU feels too
  eager/too slow for Indian ESL — R1).
- `latency.tts_ttfb_ms` p50/p95 — TTS first-byte for the canned `say()` lines, sanity-check vs the §3
  budget (real perceived-response p50 ≤ 1200 / p95 ≤ 1500 ms is gated fully in M4 once the mouth's
  `llm_ttft_ms` exists; M3 confirms EOU + TTS are within budget).
> The brain is async/off the critical path by design — the EOU+TTS gate here is the part M3 owns. Do not
> expect EOU alone near ~0.5s (master §3a CMI-3 caveat): the big perceived-latency win is decoupling the
> brain (M5), not EOU tuning.

- [ ] **Step 5: Confirm v1 is byte-for-byte unaffected**

```bash
# a v1 job (interview_engine_version NULL/'v1') still runs the legacy orchestrator:
docker compose exec -T nexus pytest tests/interview_engine tests/interview_runtime -m "not prompt_quality" -q
```
Expected: PASS — v1 suite green (the cutover backstop). Ignore the pre-existing failure
`tests/interview_engine/test_replay_failing_session.py` (missing untracked engine-events fixture — not ours).
Also dial a v1 job briefly and confirm it behaves exactly as before.

- [ ] **Step 6 (optional): coverage on the pure v2 turn-taking modules**

Per the backend CLAUDE.md "Coverage in Docker" workaround (pytest-cov segfaults under livekit/PyO3):
```bash
docker compose exec nexus python -m coverage run --branch \
  --source=app/modules/interview_engine_v2/turn_taking,app/modules/interview_engine_v2/audio_metrics \
  -m pytest tests/interview_engine_v2 -m "not prompt_quality" -q
docker compose exec nexus python -m coverage report --show-missing
```
Expected: the pure floor/eou/pacing/audio_metrics logic is ~100% branch (these are the load-bearing
turn-taking decisions; the livekit harness in `agent.py` is excluded — it's talk-tested).

---

## M3 acceptance checklist (run before declaring M3 done)

- [ ] `pytest tests/interview_engine_v2 -v` — all green (config + pacing + eou + floor + audio_metrics +
      turn-detector override + harness-script helpers).
- [ ] `pytest tests/test_module_boundaries.py` — green; no illegal cross-module deep import.
- [ ] Pure modules import with **no livekit** (Task 7 Step 5).
- [ ] `pytest tests/interview_engine tests/interview_runtime -m "not prompt_quality" -q` — v1 unchanged,
      green (Task 9 Step 5). `build_turn_detector()` no-arg call still byte-identical for v1.
- [ ] Talk-test (Task 9 Step 3): waits on think-pauses, never talks over the candidate, yields on barge-in,
      keeps the floor on backchannel, runs the unresponsive ladder to `candidate_unresponsive`.
- [ ] CMI-3 (Task 9 Step 4): `engine.v2.audio_tuning_summary` dumps real percentiles;
      `end_of_utterance_delay_ms` patient-but-not-laggy within the v2 endpointing band; `tts_ttfb_ms` sane.
- [ ] TTS voice decision recorded (Task 8); chosen default set.
- [ ] `git log --oneline` shows one focused commit per task; no unrelated churn; the pre-existing untracked
      `backend/nexus/scripts/export_job_agent_context.py` is **not** staged.

## Per-subagent git-scope guardrails (every task)

- After EVERY task the controller verifies `git symbolic-ref HEAD` is still `feat/interview-engine-v2-m3`.
- Reviewers inspect via `git show` / `git diff <base>..<head>` ONLY — **never** `git checkout` (detaches HEAD).
- Each subagent: `git add` ONLY the files listed for its task; ONE commit; NO
  branch/stash/reset/checkout/amend/clean/push; do NOT stage the pre-existing untracked
  `backend/nexus/scripts/export_job_agent_context.py`.
- Two-stage review per task: (1) spec compliance vs this plan, (2) code quality. Merge tightly-coupled
  steps into one dispatch (Task 7 already merges the harness wiring) to avoid a broken intermediate commit.

## Self-review notes

- **Spec coverage (master §5 M3 / DESIGN-SPEC §3+§4):** EOU tune (recalibratable `unlikely_threshold` +
  dynamic endpointing wired into v2, isolated knobs) = Tasks 1/6/7; floor-yield + one-directional
  interruption + barge-in scaffold = Task 4 + Task 7 wiring; hold-space + unresponsive ladder + backchannel
  gating = Tasks 2/3 + Task 7; v2 audio summary (CMI-3) = Task 5 + Task 7 + Task 9 Step 4; TTS listen-test
  (R5) = Task 8; manual talk-test harness (master §5 point 7) = Task 7 + Task 9. NO new model (locked).
- **Cross-milestone:** M3 implements CMI-3's EOU half (latency gate on EOU+TTS; the mouth+brain halves are
  M4/M5). M3 does **not** call `record_session_result` (CMI-1 / M5) — throwaway test jobs only, same as M1.
  M3 adds **no DB schema**, so the "create_all hides migration-only constraints" hazard does not apply.
- **No-regex / no-leak:** the backchannel gate is an allowlist + word-count (Task 3), not `re`; no rubric
  text crosses to the candidate (the harness speaks only bank text + tunable cue strings — no rubric is
  ever loaded by this engine path).
- **Type consistency:** `EndpointingSettings`/`HoldSpacePacer` (pacing), `EouConfig`/`UnresponsiveLadder`/
  `LadderAction`/`is_backchannel` (eou), `ResumptionSignals`/`ResumptionLabel`/`should_yield`/
  `classify_resumption` (floor), `percentile_stats`/`extract_ms`/`compute_audio_summary` (audio_metrics),
  `BankScript`/`assemble_v2_keyterms` (agent) are referenced identically across their tests + the harness.
- **No placeholders:** every code step has the actual code; commands have expected output; the livekit
  harness carries explicit "verify-against-installed-version" notes (R3) rather than guesses.
```
