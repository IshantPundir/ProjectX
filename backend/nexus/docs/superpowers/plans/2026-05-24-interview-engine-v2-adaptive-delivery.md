# Adaptive Spoken Delivery + Floor-Aware Clarify — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the interviewer deliver bank questions as natural spoken Indian English (mouth renders, never rewrites the graded canonical), let the brain attach a benign orienting clause to abstract scenario questions, and fix the clarify-drift bug by giving the brain "the exact line on the floor."

**Architecture:** Strict separation of CONTENT (brain: question selection + rubric-aware `spoken_setup`, graded & recorded) from RENDERING (mouth: clarity/Indian-English/segmentation, every specific term preserved, no new content). A `FloorRef` in the brain tracks the last question-bearing line actually asked (main or probe) so clarify/repeat/confirm target it. Spec: `docs/superpowers/specs/2026-05-24-interview-engine-v2-adaptive-delivery-design.md`.

**Tech Stack:** Python 3.13, Pydantic v2, instructor + OpenAI (`gpt-5.4-mini`), LiveKit Agents 1.5.9, pytest (`@prompt_quality` opt-in real-API evals), Docker Compose (`nexus` for tests, `nexus-engine` for the live worker).

**Pre-req / git state:** This builds on the **uncommitted double-opener fix** currently in the working tree (mouth `input_builder.py` + 8 act-blocks + `brain.system.txt` + evals). Commit or branch that first; do all work for this plan on a feature branch. Per `[[user_solo_dev]]` the user controls the final merge/push. Per `[[feedback_subagent_git_scope]]`, never stage `scripts/export_job_agent_context.py`; verify `git symbolic-ref HEAD` and `git show --stat` after each task.

**Test commands (reference):**
- Unit (excludes real-API): `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q <paths>`
- Real-API evals: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q <nodeids>`
- Lint: `docker compose exec -T nexus ruff check --no-cache <files>`
- Engine restart (after prompt/glue changes): `docker compose up -d --force-recreate nexus-engine`

**Conventions:** unit tests mock the LLM at `_call_brain`/`_call_triage` (the app/ai boundary). Prompts are validated by `@prompt_quality` evals (the eval IS the test). `agent.py` glue is talk-test-validated, not unit-tested (`[[feedback_manual_agent_testing]]`). No runtime text-matching for intent (`[[feedback_no_regex]]`); exact-substring assertions in tests are fine.

---

## Task 1: `Directive.spoken_setup` field + no-leak coverage

**Files:**
- Modify: `app/modules/interview_engine_v2/directive.py`
- Test: `tests/interview_engine_v2/test_directive.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine_v2/test_directive.py` (create the file if absent, importing `Directive, DirectiveAct, RubricLeakError`):

```python
import pytest
from app.modules.interview_engine_v2.directive import Directive, DirectiveAct, RubricLeakError


def test_spoken_setup_defaults_none_and_round_trips():
    d = Directive(id="d1", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="How would you design the recipe?")
    assert d.spoken_setup is None
    d2 = Directive(id="d2", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                   say="How would you design the recipe?",
                   spoken_setup="Say tickets arrive from a system like Jira.")
    assert d2.spoken_setup == "Say tickets arrive from a system like Jira."


def test_spoken_setup_is_no_leak_validated():
    with pytest.raises(RubricLeakError):
        Directive(id="d3", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="How would you design the recipe?",
                  spoken_setup="Remember the rubric wants idempotency.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_directive.py -k spoken_setup`
Expected: FAIL — `Directive` has no `spoken_setup` field (and the leak test doesn't raise).

- [ ] **Step 3: Add the field and extend the no-leak haystack**

In `directive.py`, add the field after `compose_hint` (around line 93):

```python
    spoken_setup: str | None = Field(
        default=None,
        description=(
            "Optional brain-authored benign orienting clause, spoken BEFORE the question "
            "(rubric-aware, no-leak). The mouth leads with it then voices the question. "
            "Null for most asks."
        ),
    )
```

In `_validate_act_invariants`, extend the haystack (line ~113):

```python
        haystack = " ".join(
            p for p in (self.say, self.compose_hint, self.spoken_setup) if p
        ).lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_directive.py -k spoken_setup`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine_v2/directive.py tests/interview_engine_v2/test_directive.py
git commit -m "feat(engine-v2): add Directive.spoken_setup (no-leak validated)"
```

---

## Task 2: `BrainDecision.spoken_setup` field

**Files:**
- Modify: `app/modules/interview_engine_v2/brain/decision.py`
- Test: `tests/interview_engine_v2/test_brain_decision.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine_v2/test_brain_decision.py` (create if absent):

```python
from app.modules.interview_engine_v2.brain.decision import BrainDecision, BrainMove, CandidateIntent


def test_spoken_setup_optional_defaults_none():
    d = BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer, move=BrainMove.advance)
    assert d.spoken_setup is None


def test_spoken_setup_round_trips():
    d = BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                      move=BrainMove.advance, bank_question_id="q3",
                      spoken_setup="Say tickets arrive from a system like Jira.")
    assert d.spoken_setup == "Say tickets arrive from a system like Jira."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_decision.py -k spoken_setup`
Expected: FAIL — `BrainDecision` has no `spoken_setup`.

- [ ] **Step 3: Add the field**

In `decision.py`, add right after the `composed_say` field (around line 116):

```python
    spoken_setup: str | None = Field(
        default=None,
        description=(
            "Optional ONE benign orienting clause for a technical_scenario advance/ask: the "
            "scenario's WHAT/WHERE (e.g. 'Say tickets arrive from a system like Jira'), NEVER "
            "the HOW/solution and never a rubric term. Spoken before the question. Null for "
            "non-scenario kinds and self-contained questions."
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_decision.py -k spoken_setup`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine_v2/brain/decision.py tests/interview_engine_v2/test_brain_decision.py
git commit -m "feat(engine-v2): add BrainDecision.spoken_setup (scenario orienting clause)"
```

---

## Task 3: Policy scrub of `spoken_setup`

**Files:**
- Modify: `app/modules/interview_engine_v2/brain/policy.py`
- Test: `tests/interview_engine_v2/test_brain_policy.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine_v2/test_brain_policy.py`:

```python
from app.modules.interview_engine_v2.brain.decision import BrainDecision, BrainMove, CandidateIntent
from app.modules.interview_engine_v2.brain.policy import evaluate_policy


def _adv(setup):
    return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                         move=BrainMove.advance, bank_question_id="q3", spoken_setup=setup)


def test_benign_spoken_setup_is_preserved():
    res = evaluate_policy(_adv("Say tickets arrive from a system like Jira."))
    assert res.sanitized_setup == "Say tickets arrive from a system like Jira."
    assert "setup_leak" not in res.violations


def test_leaky_spoken_setup_is_dropped():
    res = evaluate_policy(_adv("The rubric wants idempotency and retries."))
    assert res.sanitized_setup is None
    assert "setup_leak" in res.violations


def test_none_spoken_setup_is_fine():
    res = evaluate_policy(_adv(None))
    assert res.sanitized_setup is None
    assert "setup_leak" not in res.violations
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_policy.py -k spoken_setup`
Expected: FAIL — `PolicyResult` has no `sanitized_setup`.

- [ ] **Step 3: Implement the scrub**

In `policy.py`, add `sanitized_setup` to `PolicyResult` (after `sanitized_say`):

```python
    sanitized_setup: str | None = None   # spoken_setup after no-leak scrub (None if leaked/absent)
```

In `evaluate_policy`, after the Gate-3 no-leak block on `say` (after `sanitized = say`), add:

```python
    # --- Gate 3b: no-leak on the optional orienting setup ---
    setup = decision.spoken_setup
    if _leaks(setup):
        violations.append("setup_leak")
        setup = None
```

And include it in the returned `PolicyResult`:

```python
    return PolicyResult(
        ok=not violations,
        effective_move=move,
        sanitized_say=sanitized,
        sanitized_setup=setup,
        checks=checks,
        violations=violations,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_policy.py -k spoken_setup`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine_v2/brain/policy.py tests/interview_engine_v2/test_brain_policy.py
git commit -m "feat(engine-v2): policy scrubs spoken_setup for rubric leaks"
```

---

## Task 4: Floor State (the HIGH clarify-drift fix)

**Files:**
- Modify: `app/modules/interview_engine_v2/brain/service.py` (FloorRef + `_floor` tracking)
- Modify: `app/modules/interview_engine_v2/brain/input_builder.py` (`# ON THE FLOOR` block)
- Test: `tests/interview_engine_v2/test_brain_floor.py`

**Responsibility:** `ControlPlane` records the exact last question-bearing line it produced; `build_brain_messages` surfaces it so clarify/repeat/confirm target it. Grading still uses the ACTIVE QUESTION rubric.

- [ ] **Step 1: Write the failing test**

Create `tests/interview_engine_v2/test_brain_floor.py`. Reuse the `_config`/`_q` helpers pattern from `test_brain_service.py` (import or copy a minimal builder). This test mocks `_call_brain` via monkeypatch (the established pattern):

```python
import pytest
from app.modules.interview_engine_v2 import DirectiveAct
from app.modules.interview_engine_v2.brain import ControlPlane
from app.modules.interview_engine_v2.brain import service as brain_service
from app.modules.interview_engine_v2.brain.decision import BrainDecision, BrainMove, CandidateIntent
from app.modules.interview_engine_v2.brain.input_builder import build_brain_messages
from app.modules.interview_engine_v2.coverage import CoverageTracker
# Reuse the SessionConfig builders (`_config`, `_q`) defined in the brain eval module:
from tests.interview_engine_v2.prompt_evals.test_brain_evals import _config, _q  # type: ignore

pytestmark = pytest.mark.asyncio


def _plane():
    cfg = _config([
        _q("q1", "rest", "How would you design a connector to a rate-limited REST API?",
           follow_ups=["How would you page through large result sets?"], pos=0),
        _q("q2", "json", "How would you transform and validate a JSON payload?", pos=1),
    ])
    cov = CoverageTracker(signals=list(cfg.signals),
                          mandatory_signals=[q.primary_signal for q in cfg.stage.questions],
                          soft_probe_cap=2)
    return ControlPlane(config=cfg, coverage=cov)


async def test_opener_seeds_floor_to_first_question(monkeypatch):
    plane = _plane()
    plane.opener()
    assert plane._floor is not None
    assert plane._floor.kind == "main"
    assert "REST" in plane._floor.canonical_text


async def test_probe_updates_floor_to_the_followup_text(monkeypatch):
    plane = _plane()
    plane.opener()

    async def fake(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.probe, grade="thin", bank_follow_up_index=0)
    monkeypatch.setattr(brain_service, "_call_brain", fake)
    await plane.decide(turn_ref="t-1", active_question_id="q1", transcript_window=[],
                       candidate_utterance="we throttle calls")
    assert plane._floor.kind == "probe"
    assert "page through large result sets" in plane._floor.canonical_text


def test_on_the_floor_block_renders_the_probe_line():
    from app.modules.interview_engine_v2.brain.service import FloorRef
    msgs = build_brain_messages(
        stable_prefix="PREFIX", transcript_window=[], coverage_summary="cov",
        active_question=None, candidate_utterance="what do you mean by large result sets?",
        floor=FloorRef(canonical_text="How would you page through large result sets?",
                       kind="probe", thread_question_id="q1"))
    suffix = msgs[-1]["content"]
    assert "ON THE FLOOR" in suffix
    assert "page through large result sets" in suffix
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_floor.py`
Expected: FAIL — `FloorRef` / `_floor` / `floor=` param don't exist.

- [ ] **Step 3: Implement FloorRef + tracking in `service.py`**

Add the dataclass near the top of `service.py` (after imports):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class FloorRef:
    """The exact question-bearing line currently awaiting an answer (single source of truth for
    'what is being asked'). `canonical_text` is the brain's intent (the mouth re-renders it);
    `kind` ∈ {main, probe, clarify}; `thread_question_id` is the bank question being graded."""
    canonical_text: str
    kind: str
    thread_question_id: str | None
```

In `ControlPlane.__init__`, add: `self._floor: FloorRef | None = None`.

In `opener()`, after setting `_active_question_id`, seed the floor:

```python
        self._floor = FloorRef(canonical_text=first.text, kind="main", thread_question_id=first.id)
```

Add a helper to map a delivered directive to a floor update:

```python
    _FLOOR_KIND: dict[DirectiveAct, str] = {
        DirectiveAct.ASK: "main", DirectiveAct.ACK_ADVANCE: "main",
        DirectiveAct.PROBE: "probe", DirectiveAct.CLARIFY: "clarify",
        DirectiveAct.REDIRECT: "clarify",
    }

    def _update_floor(self, directive: Directive) -> None:
        """Record the question-bearing line just produced. Non-question acts leave the floor."""
        kind = self._FLOOR_KIND.get(directive.act)
        if kind is None or not directive.say:
            return
        self._floor = FloorRef(canonical_text=directive.say, kind=kind,
                               thread_question_id=self._active_question_id)
```

In `decide()`, pass the *current* floor into `build_brain_messages` (before any update), and update it after the directive is built. Modify the `build_brain_messages(...)` call to add `floor=self._floor`, and after the `directive = self._build_directive(...)` + the `_active_question_id` update block, add:

```python
        self._update_floor(directive)
```

- [ ] **Step 4: Implement the `# ON THE FLOOR` block in `input_builder.py`**

In `build_brain_messages`, add a `floor` keyword param (default None). Import is not needed (typed as object/`"FloorRef | None"` via `from __future__ import annotations`; use duck typing on `.canonical_text`/`.kind`). Build a floor block and insert it just BEFORE the `# ACTIVE QUESTION` line in `suffix`:

```python
    floor_block = ""
    if floor is not None and getattr(floor, "canonical_text", None):
        floor_block = (
            f"# ON THE FLOOR (the EXACT question you last asked aloud — a clarify / repeat / "
            f"confirm must address THIS line, not a different question)\n"
            f"[{getattr(floor, 'kind', 'main')}] {floor.canonical_text}\n\n"
        )
```

Then insert `f"{floor_block}"` into the `suffix` f-string immediately before `# ACTIVE QUESTION`. Add `floor=None` to the signature:

```python
def build_brain_messages(
    *,
    stable_prefix: str,
    transcript_window: list[tuple[str, str]],
    coverage_summary: str,
    active_question: "QuestionConfig | None",
    candidate_utterance: str,
    asked_question_ids: list[str] | None = None,
    max_transcript_turns: int = _DEFAULT_WINDOW,
    active_probe_count: int = 0,
    floor: object | None = None,
) -> list[dict[str, str]]:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_floor.py`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the existing brain unit suite (no regressions)**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_service.py`
Expected: PASS (all existing brain tests still green).

- [ ] **Step 7: Commit**

```bash
git add app/modules/interview_engine_v2/brain/service.py app/modules/interview_engine_v2/brain/input_builder.py tests/interview_engine_v2/test_brain_floor.py
git commit -m "fix(engine-v2): brain tracks the line ON THE FLOOR (clarify targets the probe, not the parent)"
```

---

## Task 5: Attach `spoken_setup` to the advance directive (override-discard)

**Files:**
- Modify: `app/modules/interview_engine_v2/brain/service.py`
- Test: `tests/interview_engine_v2/test_brain_setup_attach.py`

**Responsibility:** carry the policy-sanitized setup onto the `ACK_ADVANCE` directive, but ONLY when the resolved target equals the brain's own pick (drop it on a mandatory-first override).

- [ ] **Step 1: Write the failing test**

Create `tests/interview_engine_v2/test_brain_setup_attach.py`:

```python
import pytest
from app.modules.interview_engine_v2 import DirectiveAct
from app.modules.interview_engine_v2.brain import ControlPlane
from app.modules.interview_engine_v2.brain import service as brain_service
from app.modules.interview_engine_v2.brain.decision import BrainDecision, BrainMove, CandidateIntent
from app.modules.interview_engine_v2.coverage import CoverageTracker
from tests.interview_engine_v2.prompt_evals.test_brain_evals import _config, _q  # type: ignore

pytestmark = pytest.mark.asyncio


def _plane(questions, mandatory):
    cfg = _config(questions)
    cov = CoverageTracker(signals=list(cfg.signals), mandatory_signals=mandatory, soft_probe_cap=2)
    return ControlPlane(config=cfg, coverage=cov)


async def test_setup_attached_when_pick_matches_resolved_target(monkeypatch):
    plane = _plane([_q("q1", "a", "Q1?", pos=0), _q("q2", "b", "Q2 scenario?", pos=1)],
                   mandatory=[])  # no mandatory -> brain's pick is honored
    plane.opener()  # active=q1, asked={q1}

    async def fake(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.advance, grade="concrete", bank_question_id="q2",
                             spoken_setup="Say you have a typical ticket queue.")
    monkeypatch.setattr(brain_service, "_call_brain", fake)
    directive, _ = await plane.decide(turn_ref="t-1", active_question_id="q1",
                                      transcript_window=[], candidate_utterance="done")
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert directive.spoken_setup == "Say you have a typical ticket queue."


async def test_setup_dropped_on_mandatory_first_override(monkeypatch):
    # q2 is the brain's pick, but q1 (mandatory) is still unasked -> resolver forces q1 -> drop setup
    plane = _plane([_q("q1", "a", "Mandatory Q1?", pos=0), _q("q2", "b", "Q2 scenario?", pos=1)],
                   mandatory=["a"])

    async def fake(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.advance, grade="concrete", bank_question_id="q2",
                             spoken_setup="Say you have a typical ticket queue.")
    monkeypatch.setattr(brain_service, "_call_brain", fake)
    directive, _ = await plane.decide(turn_ref="t-1", active_question_id=None,
                                      transcript_window=[], candidate_utterance="hi")
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert "Mandatory Q1" in (directive.say or "")
    assert directive.spoken_setup is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_setup_attach.py`
Expected: FAIL — `directive.spoken_setup` is always None (not yet wired).

- [ ] **Step 3: Wire the setup through `_build_directive`**

In `decide()`, the policy result already exists (`policy = evaluate_policy(decision)`). Pass the sanitized setup into `_build_directive`:

```python
        directive = self._build_directive(
            turn_ref=turn_ref, move=move, decision=decision,
            sanitized_say=policy.sanitized_say, active_question_id=aqid,
            sanitized_setup=policy.sanitized_setup,
        )
```

In `_build_directive`, add the `sanitized_setup: str | None` parameter, and in the `advance` branch, compute the attach decision after `target_id` is resolved:

```python
        if move is BrainMove.advance:
            target_id = self._resolve_advance_target(decision.bank_question_id,
                                                     active_question_id=active_question_id)
            if target_id is None:
                return Directive(... CLOSE ...)   # unchanged
            self._pending_advance_id = target_id
            say = self._questions[target_id].text
            # attach setup ONLY when the resolved target is the brain's OWN pick (no override)
            setup = sanitized_setup if target_id == decision.bank_question_id else None
```

Then thread `setup` into the final `Directive(...)` construction for the advance path. Because the function has a single shared `return Directive(...)` at the bottom, introduce a local `spoken_setup: str | None = None` initialized at the top of the function, set it to `setup` in the advance branch, and add `spoken_setup=spoken_setup` to the final `Directive(...)`:

```python
    def _build_directive(self, *, turn_ref, move, decision, sanitized_say,
                         active_question_id, sanitized_setup=None):
        ...
        spoken_setup: str | None = None
        say: str | None
        if move is BrainMove.advance:
            ...
            spoken_setup = sanitized_setup if target_id == decision.bank_question_id else None
        elif ...:
            ...
        return Directive(
            id=self._new_id(), turn_ref=turn_ref, act=act, say=say,
            compose_hint=None, tone=tone, is_terminal=is_terminal, spoken_setup=spoken_setup,
        )
```

**Also** forward the new param in the probe-branch recursion: both `return self._build_directive(turn_ref=..., move=BrainMove.advance, decision=decision, sanitized_say=sanitized_say, active_question_id=active_question_id)` calls (the "no valid follow-up index" and "all follow-ups used" degrade paths) must add `sanitized_setup=sanitized_setup`, so a probe→advance degrade re-evaluates the pick==target guard rather than silently dropping setup to the default `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_setup_attach.py`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the brain unit suite (no regressions)**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_service.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine_v2/brain/service.py tests/interview_engine_v2/test_brain_setup_attach.py
git commit -m "feat(engine-v2): attach spoken_setup to advance (dropped on mandatory-first override)"
```

---

## Task 6: Thread `spoken_setup` into the mouth messages

**Files:**
- Modify: `app/modules/interview_engine_v2/mouth/input_builder.py`
- Modify: `app/modules/interview_engine_v2/mouth/service.py`
- Test: `tests/interview_engine_v2/test_mouth_input_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine_v2/test_mouth_input_builder.py`:

```python
def test_spoken_setup_surfaces_as_lead_in():
    from app.modules.interview_engine_v2.mouth.input_builder import build_mouth_messages
    from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
    msgs = build_mouth_messages(
        directive=Directive(id="d", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                            say="How would you design the recipe?"),
        persona_preamble="P", act_block="A", candidate_utterance=None, last_question=None,
        spoken_setup="Say tickets arrive from a system like Jira.")
    suffix = msgs[2]["content"]
    assert "SPOKEN SETUP" in suffix
    assert "Say tickets arrive from a system like Jira." in suffix


def test_conversation_plane_forwards_directive_spoken_setup():
    from app.ai.prompts import PromptLoader
    from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
    from app.modules.interview_engine_v2.mouth.service import ConversationPlane
    plane = ConversationPlane(loader=PromptLoader(version="v3"),
                              persona_name="Arjun", job_title="Backend Engineer")
    msgs = plane.build_turn_messages(
        Directive(id="d", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="How would you design the recipe?",
                  spoken_setup="Say tickets arrive from a system like Jira."),
        candidate_utterance="ok")
    assert "Say tickets arrive from a system like Jira." in msgs[-1]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_mouth_input_builder.py -k spoken_setup`
Expected: FAIL — `build_mouth_messages` has no `spoken_setup` param.

- [ ] **Step 3: Add `spoken_setup` to `build_mouth_messages`**

In `mouth/input_builder.py`, add `spoken_setup: str | None = None` to the signature, and insert this block in the dynamic-suffix `lines` assembly — AFTER the `YOU ALREADY SAID` block and BEFORE `lines.append("DELIVER THIS NOW:")`:

```python
    if spoken_setup and spoken_setup.strip():
        lines.append(f"SPOKEN SETUP: «{spoken_setup.strip()}»")
        lines.append(
            "Say this short orienting line FIRST (in your own natural spoken words), then deliver "
            "the question below. It sets the scene; do not treat it as part of the question text.")
        lines.append("")
```

- [ ] **Step 4: Forward it from `ConversationPlane.build_turn_messages`**

In `mouth/service.py` `build_turn_messages`, pass the directive's field through to `build_mouth_messages`:

```python
        messages = build_mouth_messages(
            directive=directive,
            persona_preamble=self._persona_preamble,
            act_block=act_block,
            candidate_utterance=candidate_utterance,
            last_question=self._last_question,
            just_said_filler=just_said_filler,
            spoken_setup=directive.spoken_setup,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_mouth_input_builder.py`
Expected: PASS (all, including the two new tests).

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine_v2/mouth/input_builder.py app/modules/interview_engine_v2/mouth/service.py tests/interview_engine_v2/test_mouth_input_builder.py
git commit -m "feat(engine-v2): thread spoken_setup into the mouth prompt as a lead-in"
```

---

## Task 7: Brain prompt — floor-aware clarify + author `spoken_setup`

**Files:**
- Modify: `prompts/v3/engine/brain.system.txt`
- Test: `tests/interview_engine_v2/prompt_evals/test_brain_evals.py` (real-API, `@prompt_quality`)

- [ ] **Step 1: Write the failing evals**

Append to `test_brain_evals.py`. The clarify-on-floor eval seeds a probe (so `_floor.kind=="probe"`) then asks "what do you mean?"; assert the clarify `say` addresses the PROBE term, not the next/parent question:

```python
async def test_clarify_after_probe_addresses_the_floor_line(monkeypatch):
    """4137c1bb [62]/[63]/[67]: a 'what do you mean?' after a probe must clarify the PROBE on the
    floor (e.g. 'page through large result sets'), NOT re-pose a different bank question."""
    cfg = _config([
        _q("q1", "rest", "How would you design a connector to a rate-limited REST API?",
           follow_ups=["How would you page through large result sets?"], pos=0),
        _q("q2", "json", "How would you transform and validate a JSON payload?", pos=1),
    ])
    plane = _plane(cfg)
    plane.opener()
    # Turn 1: candidate answers -> brain probes (paging) -> floor becomes the probe line.
    await plane.decide(turn_ref="t-1", active_question_id="q1", transcript_window=[],
                       candidate_utterance="I keep calls under the rate limit.")
    # Turn 2: candidate asks what the probe means.
    directive, record = await plane.decide(
        turn_ref="t-2", transcript_window=[],
        candidate_utterance="What do you mean by large result sets?")
    assert directive.act is DirectiveAct.CLARIFY, record.move
    low = (directive.say or "").lower()
    assert ("result set" in low or "page" in low or "records" in low), directive.say
    assert "json" not in low, f"clarify drifted to the JSON question: {directive.say!r}"
    _assert_no_rubric_leak(directive)


async def test_scenario_spoken_setup_is_benign_no_leak():
    """The brain may add ONE orienting clause to a scenario advance; it must be benign (no rubric
    token, no solution component like retries/backoff for a rate-limit question)."""
    cfg = _config([
        _q("q1", "exp", "How many years have you worked with Workato?", pos=0),
        _q("q2", "rest", "You're building a connector to a rate-limited REST API. How would you "
           "design around the limit to avoid dropped calls?", pos=1)],
        signals=["exp", "rest"])
    plane = _plane(cfg, mandatory=[])
    plane.opener()
    directive, record = await plane.decide(
        turn_ref="t-1", active_question_id="q1", transcript_window=[],
        candidate_utterance="About two years with Workato, hands on.")
    if directive.spoken_setup:
        s = directive.spoken_setup.lower()
        for leak in ("retry", "retries", "backoff", "429", "pagination", "idempoten", "rubric"):
            assert leak not in s, f"spoken_setup leaked a solution component: {directive.spoken_setup!r}"
```

(`_plane` may need a no-arg-config overload; if the existing `_plane(config, *, mandatory=None)` is used, call `_plane(cfg)`.)

- [ ] **Step 2: Run evals to verify they fail (or are flaky) on the current prompt**

Run: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_brain_evals.py -k "after_probe_addresses_the_floor or scenario_spoken_setup"`
Expected: the floor eval FAILS (brain clarifies the wrong question — the bug) and/or `spoken_setup` is never set. Capture the failing `say` for the record.

- [ ] **Step 3: Edit `brain.system.txt`**

(a) In the `clarify` bullet, prepend a floor-targeting instruction:

```
- clarify: the candidate misunderstood or asked what something means. Clarify the EXACT line shown
  under "# ON THE FLOOR" in the turn data — that is the question you last asked aloud, which may be
  a follow-up, NOT the main question. Re-pose THAT line more simply (an everyday example is fine);
  never switch to a different bank question. [...existing clarify guidance continues...]
```

(b) In the `advance` bullet (or the Output section), add the setup-authoring instruction:

```
  When you advance to a `technical_scenario` question that is abstract, you MAY set `spoken_setup`
  to ONE short, plain orienting clause that makes the scenario concrete — the WHAT/WHERE only
  (e.g. "Say the tickets arrive from a system like Jira", "Assume a standard REST API that limits
  requests per minute"). NEVER the HOW/solution, never a rubric term, never a hint. Leave
  `spoken_setup` null for experience/behavioral/compliance questions and for any question that is
  already self-contained.
```

- [ ] **Step 4: Run the evals to verify they pass (×3 for stability)**

Run (3 times): `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_brain_evals.py -k "after_probe_addresses_the_floor or scenario_spoken_setup"`
Expected: PASS all 3 runs.

- [ ] **Step 5: Run the FULL brain eval suite (no regression incl. F1/knockout/no-leak)**

Run: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_brain_evals.py`
Expected: PASS (tolerate only known-flaky cases; re-run a failure once to confirm flakiness).

- [ ] **Step 6: Commit**

```bash
git add prompts/v3/engine/brain.system.txt tests/interview_engine_v2/prompt_evals/test_brain_evals.py
git commit -m "feat(engine-v2): brain clarifies the line ON THE FLOOR + authors benign scenario setup"
```

---

## Task 8: Mouth prompts — spoken rendering contract + Indian-English persona

**Files:**
- Modify: `prompts/v3/engine/mouth/{ask,ack_advance,probe,clarify,redirect,confirm,hint,answer_meta}.txt`
- Modify: `prompts/v3/engine/mouth/_persona.txt`
- Test: `tests/interview_engine_v2/prompt_evals/test_mouth_evals.py` (real-API)

- [ ] **Step 1: Write the failing eval**

Append to `test_mouth_evals.py` a fidelity eval that drives a long verbatim scenario `say` and asserts the rendering (a) preserves the specific terms by exact substring, (b) adds no solution, (c) one question, (d) not longer; plus a setup-lead-in eval:

```python
@pytest.mark.asyncio
async def test_rendering_preserves_specific_terms_and_adds_no_solution():
    say = ("You're building a Workato recipe that calls an AI to auto-triage IT tickets. "
           "How would you design the flow so the AI's decision reliably routes the ticket?")
    out = await _voice(Directive(id="d", turn_ref="t1", act=DirectiveAct.ACK_ADVANCE, say=say))
    low = out.lower()
    for term in ("workato", "ai", "ticket", "route"):           # specific terms must survive
        assert term in low, f"dropped specific term {term!r}: {out!r}"
    assert out.count("?") <= 1                                   # exactly one question
    for leak in ("retry", "backoff", "confidence threshold", "human in the loop"):  # no solution
        assert leak not in low, f"rendering added a solution hint: {out!r}"


@pytest.mark.asyncio
async def test_rendering_leads_with_spoken_setup():
    say = ("You're building a connector to a rate-limited REST API. How would you design around "
           "the limit to avoid dropped calls?")
    out = await _voice(Directive(id="d", turn_ref="t1", act=DirectiveAct.ACK_ADVANCE, say=say,
                                 spoken_setup="Say a standard REST API that limits requests per minute."))
    low = out.lower()
    assert "rest" in low and ("rate" in low or "limit" in low)
    assert "minute" in low or "per minute" in low               # the setup scene made it in
    assert out.count("?") <= 1
```

(`_voice` already exists; it calls `build_turn_messages(directive, candidate_utterance=...)` which now forwards `directive.spoken_setup` — confirm `_voice` passes the directive object.)

- [ ] **Step 2: Run eval to verify behavior on current prompt**

Run: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_mouth_evals.py -k "preserves_specific_terms or leads_with_spoken_setup"`
Expected: the setup-lead-in eval likely FAILS (act-blocks still say "AS WRITTEN"; setup not consumed naturally). Capture output.

- [ ] **Step 3: Edit the 8 act-blocks — replace "AS WRITTEN" with the rendering contract**

In each of `ask.txt, ack_advance.txt, probe.txt, clarify.txt, redirect.txt, confirm.txt, hint.txt, answer_meta.txt`, replace the "deliver ... AS WRITTEN / do NOT reword" instruction with this shared contract (adapt the verb to the act):

```
Render the line in `say` as you would SAY it aloud in natural Indian English: short sentences, one
idea at a time, plain everyday words. PRESERVE every specific term exactly as given — product names,
protocols, formats, and the precise thing being asked (do not drop, rename, or generalise them). Do
NOT add any new fact, example, number, or hint, do NOT change how hard the question is, and ask
EXACTLY ONE question. If a "SPOKEN SETUP" line is present in the turn data, say it first (one short
clause, in your own words), then the question.
```

Keep each act's existing intent line + the `YOU ALREADY SAID` double-opener handling (unchanged). Update each `Good (...)` example to show a *spoken, segmented* rendering rather than a verbatim echo.

- [ ] **Step 4: Strengthen `_persona.txt` (Indian-English register)**

In `_persona.txt`, under "HOW YOU SPEAK", add bullets:

```
- Speak the way a person speaks, not the way a form reads. Break a long or clause-heavy question
  into one or two short spoken sentences. One idea per sentence.
- Use plain, concrete words an Indian-English speaker hears naturally; if a question is abstract,
  ground it (the brain may hand you a SPOKEN SETUP line for exactly this). Never add technical
  content the instruction didn't give you.
```

- [ ] **Step 5: Run the eval to verify it passes (×3 for stability)**

Run (3 times): `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_mouth_evals.py -k "preserves_specific_terms or leads_with_spoken_setup"`
Expected: PASS all 3.

- [ ] **Step 6: Run the FULL mouth eval suite (no regression, incl. double-opener guards)**

Run: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_mouth_evals.py`
Expected: PASS (re-run a failure once to confirm any known flakiness, e.g. `test_no_raw_digits`).

- [ ] **Step 7: Commit**

```bash
git add prompts/v3/engine/mouth/*.txt tests/interview_engine_v2/prompt_evals/test_mouth_evals.py
git commit -m "feat(engine-v2): mouth renders questions as natural spoken Indian English (terms preserved, no new content)"
```

---

## Task 9: Record the actual spoken rendering in the transcript (glue)

**Files:**
- Modify: `app/modules/interview_engine_v2/agent.py`

**Responsibility:** capture what the candidate ACTUALLY heard (the mouth's rendered output) into `_result_transcript`, replacing the premature canonical appends. Centralises agent-spoken recording in `llm_node`. Per `[[feedback_manual_agent_testing]]` this is glue → validated by ruff + import-smoke + restart + talk-test, not a unit test.

- [ ] **Step 1: Capture the streamed rendering in `llm_node`**

In `_MouthAgent.llm_node`, accumulate the streamed text and record it after playout. Replace the final loop with:

```python
        spoken_parts: list[str] = []
        async for chunk in Agent.default.llm_node(self, ctx, tools, model_settings):
            # LiveKit 1.5.9: verify the chunk text accessor against the installed SDK before relying
            # on it (do NOT trust this memorised shape — `chunk.delta.content` for ChatChunk).
            delta = getattr(getattr(chunk, "delta", None), "content", None)
            if isinstance(delta, str):
                spoken_parts.append(delta)
            yield chunk
        spoken = "".join(spoken_parts).strip()
        if spoken:
            self._result_transcript.append(
                TranscriptEntry(role="agent", text=spoken, timestamp_ms=self._t_ms()))
```

- [ ] **Step 2: Remove the now-duplicate canonical appends**

- In `_on_brain_done`, delete the `self._result_transcript.append(TranscriptEntry(role="agent", text=directive.say, ...))` line (keep `self._transcript.append(("agent", directive.say))` — that's the brain's canonical window, still needed).
- In `on_enter`, delete the `self._result_transcript.append(TranscriptEntry(role="agent", text=d.say, ...))` line (keep the `self._transcript.append(("agent", d.say))`); otherwise the opener ASK is double-recorded (canonical here + spoken in `llm_node`). The INTRO (say=None) was never recorded before and now its spoken greeting IS captured by `llm_node` — a bonus fix for the known "INTRO not persisted" gap.
- In `_deliver_repeat`, delete the `self._result_transcript.append(TranscriptEntry(role="agent", text=replayed, ...))` block (the REPEAT now records via `llm_node` when the mouth voices it).
- Keep the filler/hold-cue appends in `_say_filler` / `_say_hold_cue` (those are `session.say`, not `llm_node`).

- [ ] **Step 3: Verify the chunk accessor against the installed SDK**

Run: `docker compose exec -T nexus python -c "import inspect, livekit.agents as a; from livekit.agents.llm import ChatChunk; print(ChatChunk.__annotations__)"`
Confirm the delta/content path; adjust Step 1's accessor if the installed shape differs.

- [ ] **Step 4: Import-smoke + ruff**

Run: `docker compose exec -T nexus python -c "import app.modules.interview_engine_v2.agent"`
Run: `docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/agent.py`
Expected: import OK; ruff clean (the 2 pre-existing E501s in agent.py are NOT yours — do not touch them).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine_v2/agent.py
git commit -m "feat(engine-v2): record the mouth's actual spoken rendering in the transcript"
```

---

## Task 10: Brain & triage prompt-cache instrumentation (glue)

**Files:**
- Modify: `app/modules/interview_engine_v2/brain/service.py` (`_call_brain`)
- Modify: `app/modules/interview_engine_v2/triage/service.py` (`_call_triage`)

**Responsibility:** log `prompt_tokens` / `cached_tokens` so brain/triage prompt-cache effectiveness is measurable (today completely unobservable — `llm.http_response` logs neither).

- [ ] **Step 1: Switch `_call_brain` to `create_with_completion` + log usage**

In `brain/service.py` `_call_brain`, replace the `create` call:

```python
    decision, completion = await client.chat.completions.create_with_completion(**create_kwargs)
    usage = getattr(completion, "usage", None)
    if usage is not None:
        details = getattr(usage, "prompt_tokens_details", None)
        log.info(
            "engine.v2.brain.usage",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            correlation_id=correlation_id,
        )
    return decision
```

(Verify `create_with_completion` exists on the installed instructor `AsyncInstructor` first:
`docker compose exec -T nexus python -c "import instructor,inspect; print(hasattr(instructor.AsyncInstructor,'chat'))"` and check the method — if the installed instructor uses a different accessor for the raw completion, use that. Do NOT trust this memorised API.)

- [ ] **Step 2: Same for `_call_triage`**

In `triage/service.py` `_call_triage`, mirror the change (return the `TriageDecision`, log `engine.v2.triage.usage`). `_call_triage` has no `correlation_id` in its body args — it does (`correlation_id` is a kwarg); include it.

- [ ] **Step 3: Real-API probe (usage actually logs)**

Run a single brain eval and grep the logs for the usage line:

Run: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_brain_evals.py -k thin_answer_probes 2>&1 | grep -i "brain.usage" | head`
Expected: a `engine.v2.brain.usage` line with `prompt_tokens` and `cached_tokens` populated (cached_tokens may be 0 on a cold first call; non-zero on warm repeats).

- [ ] **Step 4: ruff + brain/triage unit suites (no regression)**

Run: `docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/brain/service.py app/modules/interview_engine_v2/triage/service.py`
Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2/test_brain_service.py tests/interview_engine_v2/test_triage_service.py`
Expected: ruff clean; unit suites green (mocks return a model — if a mock now needs to return a (model, completion) tuple for the brain, update the mock helper to use `create_with_completion`; check `test_brain_service.py`'s mock and adjust if it patches `create` directly. If tests patch `_call_brain` (not the client), no change needed.)

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine_v2/brain/service.py app/modules/interview_engine_v2/triage/service.py
git commit -m "feat(engine-v2): log brain/triage prompt-cache usage (prompt_tokens + cached_tokens)"
```

---

## Task 11: Full regression gate + live talk-test handoff

**Files:** none (verification only)

- [ ] **Step 1: Full v2 unit + boundary + runtime suite**

Run: `docker compose exec -T nexus python -m pytest -p no:cacheprovider -q tests/interview_engine_v2 tests/test_module_boundaries.py`
Expected: all green (prompt_quality deselected by default).

- [ ] **Step 2: Full prompt-eval suites (mouth + brain)**

Run: `docker compose exec -T nexus python -m pytest -m prompt_quality -p no:cacheprovider -q tests/interview_engine_v2/prompt_evals/test_mouth_evals.py tests/interview_engine_v2/prompt_evals/test_brain_evals.py`
Expected: green (re-run any single failure once to confirm known flakiness, e.g. `test_no_raw_digits`).

- [ ] **Step 3: ruff on all touched files**

Run: `docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/ tests/interview_engine_v2/`
Expected: clean except the 2 pre-existing agent.py E501s (not ours).

- [ ] **Step 4: Full-branch git-scope check**

Run: `git log --oneline <base>..HEAD` and `git diff --stat <base>..HEAD`
Expected: only the files this plan names; `scripts/export_job_agent_context.py` NOT staged; `git stash list` empty; `git symbolic-ref HEAD` is the feature branch.

- [ ] **Step 5: Restart the engine + hand off to the user's live talk-test**

Run: `docker compose up -d --force-recreate nexus-engine` and confirm healthy.
Talk-test checklist for the user (capture `engine-events/<session_id>.json`):
- Scenario questions sound like natural spoken Indian English (segmented, plain), with the specific terms intact and no added hints.
- Proactive `spoken_setup` appears on abstract scenario questions → fewer "can you give me context" requests vs `4137c1bb`.
- A "what do you mean?" after a probe clarifies THE PROBE (no "two questions at once").
- The transcript now shows the spoken rendering; logs show `engine.v2.brain.usage` / `engine.v2.triage.usage` with cache hits on warm turns.
- The double-opener stays gone by ear.

- [ ] **Step 6: After the user is happy → `superpowers:finishing-a-development-branch`** (the user controls the merge/push).

---

## Notes for the executor
- Honor `[[feedback_subagent_review_cadence]]`: combined spec+quality review on the mechanical pure tasks (1, 2, 3, 6); split spec→quality review on the meaty/behavioral ones (4, 5, 7, 8, 9). A final whole-impl review before Task 11.
- Honor `[[feedback_subagent_git_scope]]`: guardrail every implementer prompt; inspect via `git show`/`git diff`, never `git checkout <sha>`; verify HEAD + `git show --stat` after each commit.
- The mouth model is `gpt-5.4-mini` (capable); prompt fixes are reliable. Brain/triage budgets unchanged.
- Latency: `spoken_setup` is one short clause and the brain output is async/masked by the triage filler; no critical-path regression expected (`[[feedback_quality_before_latency]]` — defer any latency tuning).
