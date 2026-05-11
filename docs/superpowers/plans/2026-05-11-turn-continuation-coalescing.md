# Turn Continuation Coalescing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add forward-coalescing of candidate utterance text in the interview orchestrator so multi-segment answers (where the candidate keeps talking past an EOU) are processed as a single Judge decision instead of producing a pile-up of openers without bodies.

**Architecture:** Pure-function decision (`_should_coalesce`) keyed on a `_PriorTurnSnapshot` captured at end-of-turn. When the previous turn's Speaker did not successfully deliver its body AND the prior `(instruction_kind, sub_context)` is in a frozen coalescible set AND the gap is within a configurable window, the new turn prepends the prior turn's candidate text to its own before calling the Judge. StateEngine mutations from the prior turn are NOT reverted — only the user-facing utterance text is merged. A new `turn.coalesced` audit event makes the merge explicit for replay tooling.

**Tech Stack:** Python 3.13, FastAPI, pydantic-settings, LiveKit Agents 1.5.x, pytest + `unittest.mock.patch`.

**Spec:** `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md`

---

## File Map

| File | Responsibility | Action |
|---|---|---|
| `backend/nexus/app/config.py` | Pydantic Settings — env-var truth | Modify (add 2 fields + validator) |
| `backend/nexus/app/ai/config.py` | AIConfig facade over Settings | Modify (expose new fields) |
| `backend/nexus/app/modules/interview_engine/event_kinds.py` | Event-kind string constants | Modify (add `TURN_COALESCED`) |
| `backend/nexus/app/modules/interview_engine/audit_events.py` | Pydantic payloads per event kind | Modify (add `TurnCoalescedPayload`) |
| `backend/nexus/app/modules/interview_engine/orchestrator.py` | Turn pipeline | Modify (add config fields, dataclass, frozenset, decision fn, capture fn, integration) |
| `backend/nexus/app/modules/interview_engine/agent.py` | Engine entrypoint | Modify (wire new OrchestratorConfig fields from settings) |
| `backend/nexus/.env.example` | Operator-facing env reference | Modify (document the new vars) |
| `backend/nexus/CLAUDE.md` | Backend Claude context | Modify (one paragraph on coalescing seam) |
| `backend/nexus/tests/test_engine_settings.py` | Settings + AIConfig tests | Modify (new field tests) |
| `backend/nexus/tests/interview_engine/test_event_kinds.py` | Event-kind registry tests | Modify (one assertion) |
| `backend/nexus/tests/interview_engine/test_audit_events.py` | Audit payload model tests | Modify (one new test) |
| `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py` *(new)* | Coalescing logic tests | Create |

---

### Task 1: Add `engine_coalesce_enabled` and `engine_coalesce_window_ms` to Settings + AIConfig

**Files:**
- Modify: `backend/nexus/app/config.py` — two new fields + `field_validator`
- Modify: `backend/nexus/app/ai/config.py` — two new property accessors
- Test: `backend/nexus/tests/test_engine_settings.py` — append five new tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/test_engine_settings.py`:

```python
def test_settings_have_coalesce_fields():
    """Coalescing-related Settings fields exist with sensible defaults."""
    fields = Settings.model_fields
    assert fields["engine_coalesce_enabled"].default is True
    assert fields["engine_coalesce_window_ms"].default == 5000


def test_settings_coalesce_window_rejects_below_one(monkeypatch):
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "0")
    with pytest.raises(ValidationError, match=r"must be in \[1, 30000\]"):
        Settings()


def test_settings_coalesce_window_rejects_above_thirty_thousand(monkeypatch):
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "30001")
    with pytest.raises(ValidationError, match=r"must be in \[1, 30000\]"):
        Settings()


def test_settings_coalesce_window_accepts_boundary_values(monkeypatch):
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "1")
    assert Settings().engine_coalesce_window_ms == 1
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "30000")
    assert Settings().engine_coalesce_window_ms == 30000


def test_aiconfig_exposes_coalesce_fields(monkeypatch):
    monkeypatch.setenv("ENGINE_COALESCE_ENABLED", "false")
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "2500")
    cfg = AIConfig()
    assert cfg.engine_coalesce_enabled is False
    assert cfg.engine_coalesce_window_ms == 2500
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/test_engine_settings.py::test_settings_have_coalesce_fields backend/nexus/tests/test_engine_settings.py::test_aiconfig_exposes_coalesce_fields -v
```

Expected: FAILED with `KeyError: 'engine_coalesce_enabled'` (or AttributeError on AIConfig).

- [ ] **Step 3: Add the Settings fields**

In `backend/nexus/app/config.py`, find the existing engine-knobs block (search for `engine_session_ended_message`). Immediately above that or in a logical position with other `engine_*` engine-tuning fields, add:

```python
    # Continuation coalescing (Lever 3 for noisy-office multi-segment answers,
    # 2026-05-11). When a new turn arrives within the window of the prior
    # turn's TURN_COMPLETED AND the prior turn's Speaker did not deliver its
    # body, the new turn's candidate text is prepended with the prior turn's
    # text before the Judge call. State mutations from the prior turn are
    # NOT reverted — only the user-utterance text is merged.
    engine_coalesce_enabled: bool = True
    # Generous safety net; the primary gate is whether the prior turn's
    # Speaker delivered its body. Range [1, 30000] enforced at startup.
    engine_coalesce_window_ms: int = 5000

    @field_validator("engine_coalesce_window_ms")
    @classmethod
    def _coalesce_window_range(cls, v: int) -> int:
        if not 1 <= v <= 30000:
            raise ValueError(
                f"engine_coalesce_window_ms must be in [1, 30000]; got {v}"
            )
        return v
```

- [ ] **Step 4: Add the AIConfig accessors**

In `backend/nexus/app/ai/config.py`, find the engine_* property block (search for `engine_judge_model`). Immediately after the last existing `engine_*` property, add:

```python
    @property
    def engine_coalesce_enabled(self) -> bool:
        return self._settings.engine_coalesce_enabled

    @property
    def engine_coalesce_window_ms(self) -> int:
        return self._settings.engine_coalesce_window_ms
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/test_engine_settings.py -v
```

Expected: all settings tests PASSED (the five new ones plus all existing ones).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/tests/test_engine_settings.py
git commit -m "$(cat <<'EOF'
feat(config): add coalescing settings for continuation merging

engine_coalesce_enabled (default True) and engine_coalesce_window_ms
(default 5000, range [1, 30000]) added to Settings + AIConfig. These
gate the new orchestrator feature that merges multi-segment candidate
utterances into a single Judge decision when the prior turn's Speaker
did not deliver its body. The window is a safety net; the primary gate
is speaker_emitted_content tracked on the orchestrator.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Register `TURN_COALESCED` event kind

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/event_kinds.py`
- Test: `backend/nexus/tests/interview_engine/test_event_kinds.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/interview_engine/test_event_kinds.py` (if the file exists; otherwise locate the existing assertions for `ALL_EVENT_KINDS` membership in the test file and follow the existing pattern):

```python
def test_turn_coalesced_in_registry():
    """TURN_COALESCED constant exists with expected value and is in ALL_EVENT_KINDS."""
    from app.modules.interview_engine.event_kinds import (
        ALL_EVENT_KINDS,
        TURN_COALESCED,
    )
    assert TURN_COALESCED == "turn.coalesced"
    assert TURN_COALESCED in ALL_EVENT_KINDS
```

If `tests/interview_engine/test_event_kinds.py` does not exist, first verify by running:

```bash
find /home/ishant/Projects/ProjectX/backend/nexus/tests -name "test_event_kinds*"
```

If no test file exists, locate the existing tests that reference `ALL_EVENT_KINDS` (search: `grep -rn "ALL_EVENT_KINDS" backend/nexus/tests/`) and add the assertion to whichever file already covers the registry. If no such file exists, create `backend/nexus/tests/interview_engine/test_event_kinds.py` with just this single test.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_event_kinds.py::test_turn_coalesced_in_registry -v
```

Expected: FAILED with `ImportError: cannot import name 'TURN_COALESCED'`.

- [ ] **Step 3: Add the constant and registry entry**

In `backend/nexus/app/modules/interview_engine/event_kinds.py`, find the line `TURN_COMPLETED = "turn.completed"` (around line 65). Immediately after it, add:

```python
TURN_COALESCED = "turn.coalesced"
```

Then locate the `ALL_EVENT_KINDS` frozenset (around line 91) and add `TURN_COALESCED` to the set body, alphabetically near `TURN_COMPLETED` for readability:

```python
ALL_EVENT_KINDS: frozenset[str] = frozenset({
    # ... existing kinds ...
    TURN_STARTED,
    TURN_COMPLETED,
    TURN_COALESCED,
    JUDGE_CALL,
    # ... rest unchanged ...
})
```

(The exact surrounding entries depend on the existing layout — preserve them as-is and add only `TURN_COALESCED` on its own line.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_event_kinds.py::test_turn_coalesced_in_registry -v
```

Expected: PASSED.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/modules/interview_engine/event_kinds.py backend/nexus/tests/interview_engine/test_event_kinds.py
git commit -m "$(cat <<'EOF'
feat(engine/events): register turn.coalesced event kind

Adds TURN_COALESCED constant and registers it in ALL_EVENT_KINDS so the
orchestrator can emit a typed audit event when it merges a new turn's
candidate utterance with the prior turn's text. The payload schema lands
in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add `TurnCoalescedPayload` Pydantic model

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/audit_events.py`
- Test: `backend/nexus/tests/interview_engine/test_audit_events.py` (if exists; otherwise locate equivalent)

- [ ] **Step 1: Verify whether an existing test file covers the payload models**

```bash
find /home/ishant/Projects/ProjectX/backend/nexus/tests -path '*interview_engine*' -name 'test_audit*'
```

If a test file exists, use it. If none does, create `backend/nexus/tests/interview_engine/test_audit_events.py` (a brand-new file) with a single header-import comment and the failing test below.

- [ ] **Step 2: Write the failing test**

In the located (or newly created) test file, append:

```python
def test_turn_coalesced_payload_roundtrip():
    """TurnCoalescedPayload serializes and validates correctly."""
    from app.modules.interview_engine.audit_events import TurnCoalescedPayload

    payload = TurnCoalescedPayload(
        prior_turn_id="prior-abc",
        current_turn_id="current-xyz",
        prior_text="First one, like, I would communicate with the client.",
        current_text="They are trying to achieve what their existing workflow is.",
        combined_text=(
            "First one, like, I would communicate with the client. "
            "They are trying to achieve what their existing workflow is."
        ),
        prior_instruction_kind="push_back",
        prior_sub_context="missing_specifics",
        gap_ms=850,
        coalesce_window_ms=5000,
    )
    dumped = payload.model_dump()
    assert dumped["prior_turn_id"] == "prior-abc"
    assert dumped["current_turn_id"] == "current-xyz"
    assert dumped["gap_ms"] == 850
    assert dumped["coalesce_window_ms"] == 5000
    assert dumped["prior_instruction_kind"] == "push_back"
    assert dumped["prior_sub_context"] == "missing_specifics"
    # Roundtrip
    restored = TurnCoalescedPayload.model_validate(dumped)
    assert restored == payload


def test_turn_coalesced_payload_rejects_negative_gap_ms():
    """gap_ms is a duration; negatives are model bugs."""
    import pytest
    from pydantic import ValidationError
    from app.modules.interview_engine.audit_events import TurnCoalescedPayload

    with pytest.raises(ValidationError):
        TurnCoalescedPayload(
            prior_turn_id="a",
            current_turn_id="b",
            prior_text="x",
            current_text="y",
            combined_text="x y",
            prior_instruction_kind="push_back",
            prior_sub_context="default",
            gap_ms=-1,
            coalesce_window_ms=5000,
        )
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_audit_events.py::test_turn_coalesced_payload_roundtrip -v
```

Expected: FAILED with `ImportError: cannot import name 'TurnCoalescedPayload'`.

- [ ] **Step 4: Add the model**

In `backend/nexus/app/modules/interview_engine/audit_events.py`, find the existing `TurnCompletedPayload` class (around line 23). Immediately after it, add:

```python
class TurnCoalescedPayload(BaseModel):
    """Audit payload for a coalesced turn — the new turn's candidate text was
    prepended with the prior turn's text before the Judge call because the
    prior turn's Speaker did not deliver its body and the (instruction_kind,
    sub_context) was eligible for coalescing.

    See ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
    """
    prior_turn_id: str
    current_turn_id: str
    prior_text: str             # prior turn's candidate utterance (redacted to length+hash in metadata mode)
    current_text: str           # new turn's candidate utterance pre-merge
    combined_text: str          # what the Judge actually sees
    prior_instruction_kind: str # InstructionKind.value as string
    prior_sub_context: str      # SubContext.value as string ("default" if none)
    gap_ms: int = Field(ge=0)   # ms between prior TURN_COMPLETED and this TURN_STARTED
    coalesce_window_ms: int = Field(ge=1, le=30000)  # config snapshot for audit clarity
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_audit_events.py -v
```

Expected: both new tests PASSED.

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/modules/interview_engine/audit_events.py backend/nexus/tests/interview_engine/test_audit_events.py
git commit -m "$(cat <<'EOF'
feat(engine/events): add TurnCoalescedPayload audit model

Typed Pydantic payload for the turn.coalesced event kind. Fields carry
prior + current + combined utterance text plus gap_ms and the
coalesce_window_ms config snapshot so forensic replay can reconstruct
exactly what the orchestrator saw at decision time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Structural prep — `OrchestratorConfig` fields, `_PriorTurnSnapshot` dataclass, `_COALESCIBLE_KINDS` frozenset, agent.py wiring

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/orchestrator.py`
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

This task is structural — it lands types and constants the next tasks consume. The behavior change is in Tasks 5–8.

- [ ] **Step 1: Add `_PriorTurnSnapshot` and `_COALESCIBLE_KINDS` to orchestrator.py**

In `backend/nexus/app/modules/interview_engine/orchestrator.py`, find the existing `@dataclass(slots=True) class OrchestratorConfig` block (around line 159). Immediately **above** it, add:

```python
@dataclass(frozen=True, slots=True)
class _PriorTurnSnapshot:
    """End-of-turn snapshot used by Continuation Coalescing.

    Captured at end of each turn, read at the start of the next. See
    ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
    """
    turn_id: str
    completed_monotonic: float
    candidate_text: str
    instruction_kind: str
    sub_context: str
    speaker_emitted_content: bool


# (instruction_kind, sub_context) pairs whose prior turn is eligible to be
# merged forward into the next turn's candidate utterance text. The check
# is on the PRIOR turn's resolved action — see the spec's classification
# table for the rationale of each entry.
_COALESCIBLE_KINDS: frozenset[tuple[str, str]] = frozenset({
    # deliver_question — fresh main question
    ("deliver_question", "default"),
    ("deliver_question", "post_cap_advance"),
    # deliver_probe — follow-up probe
    ("deliver_probe", "default"),
    # push_back — the Judge wanted more specifics
    ("push_back", "vague_answer"),
    ("push_back", "deflection"),
    ("push_back", "missing_specifics"),
    ("push_back", "unanswered_subquestion"),
    # clarify — Judge wanted to rephrase
    ("clarify", "default"),
    # acknowledge_no_experience — Judge confirmed no-experience routing
    ("acknowledge_no_experience", "default"),
    # redirect — only social_or_greeting; off_topic/abusive/injection are
    # explicit behavioral judgments and not coalescible
    ("redirect", "social_or_greeting"),
})
```

- [ ] **Step 2: Extend `OrchestratorConfig` with coalesce fields**

In the same file, modify the existing `OrchestratorConfig` dataclass (lines 159–171) by adding two fields at the end (preserve all existing fields):

```python
@dataclass(slots=True)
class OrchestratorConfig:
    checkpoint_turns: int = 10
    checkpoint_seconds: int = 30
    session_ended_message: str = (
        "Thanks for your time. This session has ended; the recruitment "
        "team will be in contact with you."
    )
    # Continuation coalescing — see _should_coalesce + _PriorTurnSnapshot
    # above, and the spec at
    # docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md
    coalesce_enabled: bool = True
    coalesce_window_ms: int = 5000
```

- [ ] **Step 3: Initialize `_last_turn` on the orchestrator**

In `InterviewOrchestrator.__init__` (the `__init__` method body that starts around line 190), find the line `self._shutdown_scheduled: bool = False` (around line 208). Immediately **below** it, add:

```python
        # Continuation coalescing — see _should_coalesce / _capture_prior_turn_snapshot.
        # Populated at end of each turn; consulted at the start of the next turn.
        self._last_turn: _PriorTurnSnapshot | None = None
```

- [ ] **Step 4: Wire the new OrchestratorConfig fields from agent.py**

In `backend/nexus/app/modules/interview_engine/agent.py`, find the `OrchestratorConfig(...)` construction inside `entrypoint` (search for `OrchestratorConfig(`). Add the two new fields, sourced from settings:

```python
        config=OrchestratorConfig(
            checkpoint_turns=settings.engine_checkpoint_turns,
            checkpoint_seconds=settings.engine_checkpoint_seconds,
            session_ended_message=settings.engine_session_ended_message,
            coalesce_enabled=settings.engine_coalesce_enabled,
            coalesce_window_ms=settings.engine_coalesce_window_ms,
        ),
```

- [ ] **Step 5: Sanity-check that the orchestrator still imports and constructs**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && docker compose run --rm nexus python -c "
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig, _PriorTurnSnapshot, _COALESCIBLE_KINDS
)
cfg = OrchestratorConfig()
assert cfg.coalesce_enabled is True
assert cfg.coalesce_window_ms == 5000
assert ('push_back', 'missing_specifics') in _COALESCIBLE_KINDS
assert ('redirect', 'off_topic') not in _COALESCIBLE_KINDS
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 6: Run the existing orchestrator-composition tests for regression**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_composition.py -v
```

Expected: all existing tests still PASSED.

- [ ] **Step 7: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/modules/interview_engine/orchestrator.py backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(engine): orchestrator structural prep for coalescing

Lands _PriorTurnSnapshot, _COALESCIBLE_KINDS, two new OrchestratorConfig
fields (coalesce_enabled / coalesce_window_ms), and a _last_turn state
field initialized to None. agent.py wires the new fields from
Settings.engine_coalesce_*. No behavior change yet — Tasks 5-7 add the
decision + capture + integration logic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Pure decision function `_should_coalesce` with matrix tests

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/orchestrator.py` (add `_CoalesceDecision` + `_should_coalesce`)
- Test: `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py` *(create new)*

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py`:

```python
"""Tests for Continuation Coalescing decision + integration.

Spec: docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.orchestrator import (
    _CoalesceDecision,
    _PriorTurnSnapshot,
    _should_coalesce,
)


def _snapshot(
    *,
    completed_monotonic: float = 1000.0,
    speaker_emitted_content: bool = False,
    instruction_kind: str = "push_back",
    sub_context: str = "missing_specifics",
) -> _PriorTurnSnapshot:
    return _PriorTurnSnapshot(
        turn_id="prior-1",
        completed_monotonic=completed_monotonic,
        candidate_text="First one, like, I would communicate with the client.",
        instruction_kind=instruction_kind,
        sub_context=sub_context,
        speaker_emitted_content=speaker_emitted_content,
    )


class TestShouldCoalesce:
    def test_no_prior_turn_returns_false(self):
        decision = _should_coalesce(
            prior=None,
            now_monotonic=1000.0,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "no_prior_turn"

    def test_disabled_returns_false_even_when_otherwise_eligible(self):
        decision = _should_coalesce(
            prior=_snapshot(),
            now_monotonic=1000.1,
            coalesce_enabled=False,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "disabled"

    def test_prior_speaker_delivered_returns_false(self):
        decision = _should_coalesce(
            prior=_snapshot(speaker_emitted_content=True),
            now_monotonic=1000.1,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_gap_exceeds_window_returns_false(self):
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0),
            now_monotonic=1006.0,  # 6000ms after — beyond 5000ms window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    def test_gap_exactly_at_window_returns_false(self):
        """Boundary: gap_ms == coalesce_window_ms is NOT coalesced (strict <)."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0),
            now_monotonic=1005.0,  # exactly 5000ms after
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    @pytest.mark.parametrize("kind,sub_ctx", [
        ("redirect", "off_topic"),
        ("redirect", "abusive"),
        ("redirect", "injection"),
        ("polite_close", "default"),
        ("polite_close", "knockout"),
        ("repeat", "default"),
        ("deliver_first_question", "default"),
    ])
    def test_non_coalescible_kinds_return_false(self, kind, sub_ctx):
        decision = _should_coalesce(
            prior=_snapshot(instruction_kind=kind, sub_context=sub_ctx),
            now_monotonic=1000.5,  # 500ms — well within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "kind_not_coalescible"

    @pytest.mark.parametrize("kind,sub_ctx", [
        ("push_back", "vague_answer"),
        ("push_back", "deflection"),
        ("push_back", "missing_specifics"),
        ("push_back", "unanswered_subquestion"),
        ("clarify", "default"),
        ("deliver_question", "default"),
        ("deliver_question", "post_cap_advance"),
        ("deliver_probe", "default"),
        ("acknowledge_no_experience", "default"),
        ("redirect", "social_or_greeting"),
    ])
    def test_coalescible_kinds_with_undelivered_speaker_return_true(self, kind, sub_ctx):
        decision = _should_coalesce(
            prior=_snapshot(
                instruction_kind=kind,
                sub_context=sub_ctx,
                speaker_emitted_content=False,
            ),
            now_monotonic=1000.5,  # 500ms — well within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is True
        assert decision.reason == "coalesced"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py::TestShouldCoalesce -v
```

Expected: FAILED with `ImportError: cannot import name '_CoalesceDecision'`.

- [ ] **Step 3: Add the decision dataclass + pure function**

In `backend/nexus/app/modules/interview_engine/orchestrator.py`, immediately **after** the `_COALESCIBLE_KINDS` frozenset definition added in Task 4, add:

```python
@dataclass(frozen=True, slots=True)
class _CoalesceDecision:
    """Outcome of the coalescing decision plus a string reason for audit logging."""
    should: bool
    reason: str


def _should_coalesce(
    *,
    prior: _PriorTurnSnapshot | None,
    now_monotonic: float,
    coalesce_enabled: bool,
    coalesce_window_ms: int,
) -> _CoalesceDecision:
    """Pure decision function for Continuation Coalescing.

    Returns ``_CoalesceDecision(should=True, reason="coalesced")`` when ALL of:

    * Coalescing is enabled (``coalesce_enabled=True``).
    * A prior turn exists.
    * The prior turn's Speaker did not successfully deliver its body
      (``prior.speaker_emitted_content is False``).
    * The gap from the prior turn's TURN_COMPLETED to now is strictly
      less than ``coalesce_window_ms``.
    * The prior turn's ``(instruction_kind, sub_context)`` is in
      ``_COALESCIBLE_KINDS``.

    Otherwise returns ``should=False`` with a reason string identifying
    which gate failed. The reason is consumed by audit logging and tests.
    """
    if not coalesce_enabled:
        return _CoalesceDecision(False, "disabled")
    if prior is None:
        return _CoalesceDecision(False, "no_prior_turn")
    if prior.speaker_emitted_content:
        return _CoalesceDecision(False, "speaker_delivered")
    gap_ms = (now_monotonic - prior.completed_monotonic) * 1000
    if gap_ms >= coalesce_window_ms:
        return _CoalesceDecision(False, "window_expired")
    if (prior.instruction_kind, prior.sub_context) not in _COALESCIBLE_KINDS:
        return _CoalesceDecision(False, "kind_not_coalescible")
    return _CoalesceDecision(True, "coalesced")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py::TestShouldCoalesce -v
```

Expected: all parametrized cases PASSED (roughly 20+ test cases due to parametrization).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/modules/interview_engine/orchestrator.py backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py
git commit -m "$(cat <<'EOF'
feat(engine): pure decision function for coalescing

_should_coalesce() takes a _PriorTurnSnapshot + clock + config and
returns a _CoalesceDecision (should + reason). The reason string is
load-bearing for both audit logs and tests — each non-coalesce path
is identified by its gate (disabled / no_prior_turn / speaker_delivered
/ window_expired / kind_not_coalescible). The full (kind, sub_context)
matrix is covered by parametrized tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Populate `_last_turn` at end of every turn

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/orchestrator.py`
- Test: `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py`

This task adds the `_capture_prior_turn_snapshot()` method and wires its calls at all end-of-turn paths.

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py`:

```python
class TestCapturePriorTurnSnapshot:
    """Verify _last_turn is populated correctly at every turn-completion path."""

    def test_capture_records_speaker_delivered_when_text_present_and_not_interrupted(
        self,
    ):
        from unittest.mock import MagicMock

        # Construct a bare orchestrator instance (no need for full DI) — we
        # only exercise _capture_prior_turn_snapshot which is a pure method
        # on self._last_turn.
        orch = MagicMock()
        orch._last_turn = None

        # Import the method as an unbound callable and bind to our mock.
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator,
        )
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-1",
            completed_monotonic=42.0,
            candidate_text="hello",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            final_text="What specifically did you set up first?",
            interrupted=False,
        )
        snap = orch._last_turn
        assert snap is not None
        assert snap.turn_id == "t-1"
        assert snap.candidate_text == "hello"
        assert snap.instruction_kind == "push_back"
        assert snap.sub_context == "missing_specifics"
        assert snap.speaker_emitted_content is True

    def test_capture_records_interrupted_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-2",
            completed_monotonic=43.0,
            candidate_text="basic slips",
            instruction_kind="push_back",
            sub_context="vague_answer",
            final_text="What specifically",  # partial text but interrupted
            interrupted=True,
        )
        assert orch._last_turn.speaker_emitted_content is False

    def test_capture_records_empty_speaker_output_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-3",
            completed_monotonic=44.0,
            candidate_text="something",
            instruction_kind="clarify",
            sub_context="default",
            final_text="",
            interrupted=False,
        )
        assert orch._last_turn.speaker_emitted_content is False

    def test_capture_records_whitespace_only_speaker_output_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-4",
            completed_monotonic=45.0,
            candidate_text="x",
            instruction_kind="clarify",
            sub_context="default",
            final_text="   \n  ",
            interrupted=False,
        )
        assert orch._last_turn.speaker_emitted_content is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py::TestCapturePriorTurnSnapshot -v
```

Expected: FAILED with `AttributeError: type object 'InterviewOrchestrator' has no attribute '_capture_prior_turn_snapshot'`.

- [ ] **Step 3: Add the `_capture_prior_turn_snapshot` method**

In `backend/nexus/app/modules/interview_engine/orchestrator.py`, find the `InterviewOrchestrator` class. Locate the existing `_append` helper method (around line 914 per the explorer findings; search for `def _append`). Immediately **above** `_append`, add:

```python
    def _capture_prior_turn_snapshot(
        self,
        *,
        turn_id: str,
        completed_monotonic: float,
        candidate_text: str,
        instruction_kind: str,
        sub_context: str,
        final_text: str,
        interrupted: bool,
    ) -> None:
        """Record the just-finished turn so the next turn can consult it for
        Continuation Coalescing.

        ``speaker_emitted_content`` is True iff the Speaker produced
        non-whitespace output AND was not interrupted — i.e., the candidate
        actually heard a body, not just an opener. Cache-replay branches
        (``repeat``) pass the cached utterance as ``final_text`` with
        ``interrupted=False``, so they correctly count as delivered.

        See ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
        """
        delivered = (not interrupted) and bool(final_text.strip())
        self._last_turn = _PriorTurnSnapshot(
            turn_id=turn_id,
            completed_monotonic=completed_monotonic,
            candidate_text=candidate_text,
            instruction_kind=instruction_kind,
            sub_context=sub_context,
            speaker_emitted_content=delivered,
        )
```

- [ ] **Step 4: Wire the call into `on_user_turn_completed`**

In the same file, find the `TURN_COMPLETED` event emission inside `on_user_turn_completed` (search for `TURN_COMPLETED` — around line 464). The emission looks like:

```python
        self._append(TURN_COMPLETED, TurnCompletedPayload(
            turn_id=turn_id,
            turn_index=self._turn_index,
            duration_ms=int(self._elapsed_ms() - elapsed_ms),
        ).model_dump())
```

Immediately **before** that block, add (using the variables already in scope at that point in the function — `turn_id`, `candidate_text`, `decision`, `sub_ctx`, and the local `final_text` / `interrupted` if present in the happy path):

```python
        # Capture this turn for potential Continuation Coalescing on the next
        # turn. The `repeat` branch and the `redirect` paths set final_text /
        # interrupted differently; collect both safely here.
        self._capture_prior_turn_snapshot(
            turn_id=turn_id,
            completed_monotonic=time.monotonic(),
            candidate_text=candidate_text,
            instruction_kind=decision.instruction_kind.value,
            sub_context=sub_ctx.value if sub_ctx is not None else "default",
            final_text=locals().get("final_text", "") or "",
            interrupted=bool(locals().get("interrupted_flag", False)),
        )
```

Notes for the implementer:
- `final_text` is the local variable inside `_stream_speaker_and_say`; if the orchestrator's variable naming differs at the `TURN_COMPLETED` site, capture the same value by tracking it as a method-scope variable assigned earlier in `on_user_turn_completed` (e.g., `final_text_for_snapshot = "..."` set in each branch). Use that consistent variable name instead of `locals().get(...)`.
- For `repeat`: at the cache-replay branch (search for `SpeakerCachedPayload`), set `final_text_for_snapshot = utterance` (the cached question text) and `interrupted_for_snapshot = False`.
- For `_handle_post_close_turn`: do **not** populate `_last_turn`. The hard-stop already routes those turns out of `on_user_turn_completed` before reaching the snapshot site.
- For `_handle_interrupted_speaker`: set `final_text_for_snapshot = ""` and `interrupted_for_snapshot = True`.
- For `_handle_empty_speaker_output`: set `final_text_for_snapshot = ""` and `interrupted_for_snapshot = False`.

Adjust the call site to use whichever local-variable convention the implementer chooses. The contract is: the snapshot must reflect whether the candidate actually heard a body.

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py::TestCapturePriorTurnSnapshot -v
```

Expected: 4 PASSED.

- [ ] **Step 6: Run the orchestrator-composition tests for regression**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_composition.py -v
```

Expected: all PASSED.

- [ ] **Step 7: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/modules/interview_engine/orchestrator.py backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py
git commit -m "$(cat <<'EOF'
feat(engine): capture prior-turn snapshot at end of every turn

Adds InterviewOrchestrator._capture_prior_turn_snapshot which populates
self._last_turn with the turn_id, completed_monotonic, candidate text,
instruction kind, sub context, and speaker_emitted_content
(= not interrupted AND non-whitespace final_text). Called from all
turn-completion paths (happy path, interrupted Speaker, empty Speaker
output, repeat cache replay) so the next turn can consult it for
Continuation Coalescing. Post-close turns are explicitly NOT recorded.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Apply coalescing in `on_user_turn_completed`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/orchestrator.py`
- Test: `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py`:

```python
class TestCoalescingIntegration:
    """End-to-end check that the orchestrator merges adjacent turns correctly.

    Uses a thin in-test helper that exercises just the coalesce-application
    block at the top of on_user_turn_completed, bypassing the full Judge /
    Speaker pipeline. The pure decision function is already covered by
    TestShouldCoalesce; the snapshot capture is covered by
    TestCapturePriorTurnSnapshot. This test focuses on the wiring between
    them — that a populated _last_turn + a new utterance produces the
    correct combined_text, emits TURN_COALESCED, and clears _last_turn.
    """

    def test_coalesces_two_utterances_and_emits_audit_event(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )
        from app.modules.interview_engine.event_kinds import TURN_COALESCED

        orch = MagicMock(spec=InterviewOrchestrator)
        # Pre-populate _last_turn as if a prior push_back turn finished
        # without delivering its Speaker body.
        prior_text = "First one, like, I would communicate with the client."
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-1",
            completed_monotonic=100.0,
            candidate_text=prior_text,
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=False,
        )
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._collector = MagicMock()
        orch._append = MagicMock()

        new_text = "They are trying to achieve what their existing workflow is."
        # Bind the real method to our mock orchestrator and call it.
        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-1",
            candidate_text=new_text,
            now_monotonic=100.5,  # 500ms after prior → within window
        )
        assert result == prior_text + " " + new_text
        # _last_turn was cleared after coalescing
        assert orch._last_turn is None
        # TURN_COALESCED emitted with correct payload
        orch._append.assert_called_once()
        (kind_arg, payload_arg) = orch._append.call_args.args
        assert kind_arg == TURN_COALESCED
        assert payload_arg["prior_turn_id"] == "prior-1"
        assert payload_arg["current_turn_id"] == "new-1"
        assert payload_arg["combined_text"] == prior_text + " " + new_text
        assert payload_arg["prior_instruction_kind"] == "push_back"
        assert payload_arg["prior_sub_context"] == "missing_specifics"
        assert payload_arg["gap_ms"] == 500
        assert payload_arg["coalesce_window_ms"] == 5000

    def test_no_coalesce_when_speaker_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-2",
            completed_monotonic=200.0,
            candidate_text="prior text",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=True,  # ← delivered
        )
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._append = MagicMock()

        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-2",
            candidate_text="new text",
            now_monotonic=200.5,
        )
        assert result == "new text"
        # _last_turn is NOT cleared by the no-coalesce path
        assert orch._last_turn is not None
        orch._append.assert_not_called()

    def test_no_coalesce_when_disabled(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-3",
            completed_monotonic=300.0,
            candidate_text="prior text",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=False,
        )
        orch._config = MagicMock(coalesce_enabled=False, coalesce_window_ms=5000)
        orch._append = MagicMock()

        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-3",
            candidate_text="new text",
            now_monotonic=300.5,
        )
        assert result == "new text"
        orch._append.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py::TestCoalescingIntegration -v
```

Expected: FAILED with `AttributeError: type object 'InterviewOrchestrator' has no attribute '_maybe_coalesce'`.

- [ ] **Step 3: Add the `_maybe_coalesce` method**

In `backend/nexus/app/modules/interview_engine/orchestrator.py`, immediately **before** the `_capture_prior_turn_snapshot` method added in Task 6, add:

```python
    def _maybe_coalesce(
        self,
        *,
        current_turn_id: str,
        candidate_text: str,
        now_monotonic: float,
    ) -> str:
        """Apply Continuation Coalescing if eligible. Returns the candidate
        text the Judge should see — either ``candidate_text`` unchanged or
        the prior turn's text prepended.

        When coalescing fires:
        * Emits a ``turn.coalesced`` audit event with the full merge context.
        * Clears ``self._last_turn`` so a third consecutive fragment doesn't
          double-merge. The new turn's outcome will repopulate
          ``self._last_turn`` for the next decision.

        See ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
        """
        decision = _should_coalesce(
            prior=self._last_turn,
            now_monotonic=now_monotonic,
            coalesce_enabled=self._config.coalesce_enabled,
            coalesce_window_ms=self._config.coalesce_window_ms,
        )
        if not decision.should:
            return candidate_text

        # decision.should is True implies self._last_turn is not None — invariant
        # from _should_coalesce. Pull it out so type-checkers and readers see it.
        prior = self._last_turn
        assert prior is not None
        gap_ms = int((now_monotonic - prior.completed_monotonic) * 1000)
        combined_text = prior.candidate_text + " " + candidate_text

        self._append(TURN_COALESCED, TurnCoalescedPayload(
            prior_turn_id=prior.turn_id,
            current_turn_id=current_turn_id,
            prior_text=prior.candidate_text,
            current_text=candidate_text,
            combined_text=combined_text,
            prior_instruction_kind=prior.instruction_kind,
            prior_sub_context=prior.sub_context,
            gap_ms=gap_ms,
            coalesce_window_ms=self._config.coalesce_window_ms,
        ).model_dump())

        # Clear so a third fragment doesn't double-merge. The new turn's
        # _capture_prior_turn_snapshot call at end-of-turn repopulates this.
        self._last_turn = None
        return combined_text
```

Also ensure the imports at the top of `orchestrator.py` include the new audit-payload class. Find the existing import line that brings in `TurnStartedPayload`, `TurnCompletedPayload`, etc. (search: `from app.modules.interview_engine.audit_events import`), and add `TurnCoalescedPayload` to that list. Similarly, in the event-kinds import (search: `from app.modules.interview_engine.event_kinds import`), add `TURN_COALESCED`.

- [ ] **Step 4: Wire the call into `on_user_turn_completed`**

In the same file, find `on_user_turn_completed` and the lifecycle hard-stop check at line ~333 (search for `lifecycle_snap.state.value in ("closing", "closed")`). The hard-stop block currently looks like:

```python
        if lifecycle_snap.state.value in ("closing", "closed"):
            await self._handle_post_close_turn(...)
            return
```

Immediately **after** the hard-stop block (so post-close turns never reach coalescing) and **before** `self._turn_index += 1` (around line 341), add:

```python
        # Continuation Coalescing — if the prior turn's Speaker did not
        # deliver its body and this turn arrived within the window, prepend
        # the prior turn's candidate text. See spec at
        # docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md
        candidate_text = self._maybe_coalesce(
            current_turn_id=str(uuid.uuid4()),  # for audit event; the real turn_id is assigned below
            candidate_text=candidate_text,
            now_monotonic=time.monotonic(),
        )
```

A subtle point: at this stage in the function, `turn_id` for THIS turn has not yet been generated — that happens at line 340. Pass a pre-generated UUID for the `current_turn_id` in the audit event, then assign that same value as `turn_id` for the rest of the function so the audit envelope's `TURN_COALESCED.current_turn_id` matches the `TURN_STARTED.turn_id` that fires immediately after. Refactor:

```python
        # Pre-generate turn_id so the coalesce-audit event references the
        # same value that TURN_STARTED will carry below.
        turn_id = str(uuid.uuid4())
        candidate_text = self._maybe_coalesce(
            current_turn_id=turn_id,
            candidate_text=candidate_text,
            now_monotonic=time.monotonic(),
        )
        self._turn_index += 1
```

And update the existing turn_id assignment (currently `turn_id = str(uuid.uuid4())` at around line 340) to **not** generate a new one — keep using the pre-generated value.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py -v
```

Expected: all 3 new integration tests PASSED plus the existing decision-function + capture tests still green.

- [ ] **Step 6: Full regression sweep**

```bash
cd /home/ishant/Projects/ProjectX && docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/ backend/nexus/tests/test_engine_settings.py -v
```

Expected: all PASSED (or pre-existing failures unrelated to this change, e.g. the `test_replay_failing_session.py` fixture-file misses from prior sessions).

- [ ] **Step 7: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/app/modules/interview_engine/orchestrator.py backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py
git commit -m "$(cat <<'EOF'
feat(engine): apply Continuation Coalescing in on_user_turn_completed

Wires _maybe_coalesce() into the orchestrator's turn entry point. Runs
AFTER the lifecycle hard-stop check (post-close turns route to
_handle_post_close_turn first) and BEFORE _turn_index increment so the
new turn gets its own clean state. When coalescing fires, the new
turn's candidate text is prepended with the prior turn's text, a
turn.coalesced audit event is emitted with the full merge context,
and self._last_turn is cleared to prevent double-merging across a
third fragment.

turn_id generation moved up so the TURN_COALESCED.current_turn_id
matches the TURN_STARTED.turn_id that fires immediately after.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Document the new env vars in `.env.example`

**Files:**
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Find the engine knobs section**

The `.env.example` has an engine knobs section grouped around the existing `ENGINE_AGENT_NAME`, `ENGINE_CHECKPOINT_TURNS`, etc. Open the file and locate the cluster.

```bash
grep -n "ENGINE_CHECKPOINT_TURNS\|ENGINE_SESSION_ENDED_MESSAGE\|ENGINE_AGENT_NAME" /home/ishant/Projects/ProjectX/backend/nexus/.env.example
```

- [ ] **Step 2: Add the two new env vars at a logical position in that section**

Pick the natural neighbor (e.g. immediately after `ENGINE_CHECKPOINT_SECONDS=...` or near other tuning knobs). Add:

```
# Continuation Coalescing (Lever 3 — 2026-05-11). When a new turn arrives
# within the window of the prior turn's TURN_COMPLETED AND the prior turn's
# Speaker did not deliver its body, the new turn's candidate text is
# prepended with the prior turn's text before the Judge call.
#
# Setting ENGINE_COALESCE_ENABLED=false disables the feature entirely and
# reverts to one-EOU-per-turn behavior. ENGINE_COALESCE_WINDOW_MS is the
# safety-net upper bound on the gap between the prior TURN_COMPLETED and
# the new turn start; the primary gate is whether the prior turn's Speaker
# delivered. Range [1, 30000] enforced at startup.
ENGINE_COALESCE_ENABLED=true
ENGINE_COALESCE_WINDOW_MS=5000
```

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/.env.example
git commit -m "$(cat <<'EOF'
docs(env): document ENGINE_COALESCE_ENABLED + ENGINE_COALESCE_WINDOW_MS

Operators copying .env.example see the new coalescing knobs with the
rationale (Lever 3, primary gate is speaker_emitted_content, window is
safety net). Range [1, 30000] documented inline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Note the coalescing seam in `backend/nexus/CLAUDE.md`

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1: Locate the Phase 3D.engine section**

```bash
grep -n "Phase 3D.engine\|interview_engine" /home/ishant/Projects/ProjectX/backend/nexus/CLAUDE.md | head -10
```

- [ ] **Step 2: Add a short paragraph noting the new seam**

In the "Phase 3D.engine" section (or the closest section that describes the orchestrator's turn lifecycle), append a single paragraph:

> **Continuation Coalescing (2026-05-11).** When a new user turn arrives in `on_user_turn_completed` within `engine_coalesce_window_ms` of the prior turn's `TURN_COMPLETED` AND the prior turn's Speaker did not deliver its body, the orchestrator prepends the prior turn's candidate text to the new turn's text before the Judge call. The prior turn's StateEngine mutations are NOT reverted — only the user-utterance text is merged. The pure decision function `_should_coalesce` and the eligible-kind frozenset `_COALESCIBLE_KINDS` live near the top of `orchestrator.py`; the snapshot of the prior turn lives on `InterviewOrchestrator._last_turn`. A `turn.coalesced` audit event makes the merge explicit for replay tooling. See `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md` for the full design.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX && git add backend/nexus/CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude): note Continuation Coalescing seam in Phase 3D.engine

One paragraph in the backend CLAUDE.md so future contributors see the
seam exists, where the pure decision function lives, and what the
state field on the orchestrator is named.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Smoke verification (manual)

**Files:** none (verification only)

- [ ] **Step 1: Restart the engine**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && docker compose restart nexus-engine
```

Look in the engine logs for a clean boot — no startup errors.

- [ ] **Step 2: Run a real session and reproduce the multi-segment-answer scenario**

In a browser session via `frontend/session`, deliberately answer in two short bursts with a brief (≈1 second) pause between them. Speak something like:

1. "First, I would communicate with the client to understand their existing workflow."
2. *(pause)*
3. "Then I would design the Jira issue types based on what I learned."

- [ ] **Step 3: Inspect the audit envelope for `turn.coalesced`**

```bash
ls -lt /home/ishant/Projects/ProjectX/backend/nexus/engine-events/ | head -3
cat /home/ishant/Projects/ProjectX/backend/nexus/engine-events/<latest-session-id>.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
coalesced = [e for e in d['events'] if e['kind'] == 'turn.coalesced']
print(f'turn.coalesced events: {len(coalesced)}')
for e in coalesced:
    p = e['payload']
    print(f'  prior_turn={p[\"prior_turn_id\"][:8]} current_turn={p[\"current_turn_id\"][:8]} gap={p[\"gap_ms\"]}ms')
    print(f'    prior_text={p[\"prior_text\"]!r}')
    print(f'    current_text={p[\"current_text\"]!r}')
    print(f'    combined_text={p[\"combined_text\"]!r}')
    print(f'    prior_kind={p[\"prior_instruction_kind\"]}/{p[\"prior_sub_context\"]}')
"
```

Expected: at least one `turn.coalesced` event with the combined text containing both of your spoken segments.

- [ ] **Step 4: Verify the Judge saw the combined utterance**

In the same envelope, find the `judge.call` event whose `turn_id` matches the `current_turn_id` from the `turn.coalesced` event. Its `output.evidence_quote` field should reference the COMBINED utterance, not just the second segment.

- [ ] **Step 5: Sanity-check kill switch**

Set `ENGINE_COALESCE_ENABLED=false` in `.env`, restart, repeat the same scenario. Verify NO `turn.coalesced` events appear and the orchestrator emits separate openers for each segment (the prior behavior). Then re-enable.

- [ ] **Step 6: No commit needed**

The plan is complete once Step 5 confirms both regimes work.

---

## Self-Review Checklist (run by the implementer at the end of the plan)

- [ ] `grep -rn "_should_coalesce\|_maybe_coalesce\|_capture_prior_turn_snapshot\|_COALESCIBLE_KINDS\|_last_turn" backend/nexus/app/modules/interview_engine/orchestrator.py` — every symbol introduced in the plan exists in the implementation.
- [ ] `docker compose run --rm nexus pytest backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py backend/nexus/tests/test_engine_settings.py backend/nexus/tests/interview_engine/test_audit_events.py -v` — all green.
- [ ] `docker compose run --rm nexus python -c "from app.config import Settings; s = Settings(); print(s.engine_coalesce_enabled, s.engine_coalesce_window_ms)"` — prints `True 5000`.
- [ ] One real session produces a `turn.coalesced` event with sensible `combined_text`.
- [ ] Setting `ENGINE_COALESCE_ENABLED=false` removes all `turn.coalesced` events and restores prior behavior.
