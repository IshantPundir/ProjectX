# Speaker intent layer — clarify_kind discriminator + deterministic opener pruning

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce an explicit conversational-intent layer (`ClarifyKind`) on the Judge↔Speaker boundary, rewrite the Speaker clarify dispatch to bring business context (not just rephrase), enforce opener variation deterministically at the input builder, and fix `is_post_cap_advance` propagation for Judge-voluntary advances at the push_back cap.

**Architecture:** The Judge gains a 5-value `ClarifyKind` enum on `ClarifyPayload`. A new Judge decision sub-tree picks the kind from the candidate utterance shape; the State Engine propagates it unchanged to `SpeakerInput`. A new `speaker/openers.py` helper computes the per-turn `available_openers` (rotation minus recently-used) which flows in the user message. The rewritten `clarify.txt` dispatches on `clarify_kind` with 5 PATH sections; PATH E (concept_explanation) carries a load-bearing anti-leak boundary with an explicit forbidden-verb list and an informational `solution_leak_suspected` naturalness flag.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI, asyncpg, OpenAI SDK via `instructor`, LiveKit AgentSession, pytest.

**Spec:** `docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md` (commit `5698f87`).

---

## Working notes for the implementer

- All commands run from `backend/nexus/` unless stated otherwise.
- Use the existing pytest invocation pattern: tests live under `tests/interview_engine/`, run with `docker compose run nexus pytest <path>` or `pytest -m "not prompt_quality" -q` for the fast set.
- Engine code under test imports from `app.modules.interview_engine.*`; prompts live at `prompts/v2/engine/`.
- The `judge.system.txt` and Speaker prompts are **versioned files** loaded at startup via `app.ai.prompts.PromptLoader`. Editing them requires no migration — just save the file.
- Existing slug truncation lives at `app/modules/interview_engine/speaker/naturalness.py:28-29` (3-word slug). This plan deduplicates it into `speaker/openers.py`.
- Session a13ec188 will be the primary replay fixture. It lives at `backend/nexus/engine-events/a13ec188-ebf4-4b5e-95fa-912555a556a7.json`.
- Each task ends with a commit. Use the `sentry-skills:commit` style only if explicitly invoked by the user; otherwise plain conventional-commit messages.

---

## File map

**Create:**
- `backend/nexus/app/modules/interview_engine/speaker/openers.py` — `opener_slug()` + `filter_available_openers()` helpers.
- `backend/nexus/tests/interview_engine/speaker/test_openers.py` — unit tests for the new helpers.

**Modify:**
- `backend/nexus/app/modules/interview_engine/models/judge.py` — add `ClarifyKind` enum + `clarify_kind` field on `ClarifyPayload`.
- `backend/nexus/app/modules/interview_engine/models/speaker.py` — add `clarify_kind` + `available_openers` fields on `SpeakerInput`.
- `backend/nexus/app/modules/interview_engine/speaker/input_builder.py` — populate `available_openers` for every kind and propagate `clarify_kind` for clarify.
- `backend/nexus/app/modules/interview_engine/speaker/naturalness.py` — replace local `_slug` with import from `openers.py`; extend `_SOFT_TARGETS` to tuple keys with backward-compatible lookup; add `detect_solution_leak`.
- `backend/nexus/app/modules/interview_engine/state/engine.py` — set `is_post_cap_advance` whenever advance fires with prior `push_back_count >= 2` (currently only set on the SE-forced downgrade path).
- `backend/nexus/prompts/v2/engine/judge.system.txt` — expanded §1.3 CLARIFY sub-tree + "give me an example" disambiguation rule + 3 new worked examples (G, H, I).
- `backend/nexus/prompts/v2/engine/speaker/clarify.txt` — full rewrite with 5 PATH dispatch sections.
- `backend/nexus/prompts/v2/engine/speaker/_preamble.txt` — append `available_openers` paragraph to §OPENINGS & ANTI-REPETITION.
- `backend/nexus/prompts/v2/engine/CHANGELOG.md` — append changelog entry.
- `backend/nexus/tests/interview_engine/judge/test_judge_output_validation.py` — schema tests for `clarify_kind`.
- `backend/nexus/tests/interview_engine/speaker/test_input_builder.py` — tests for `clarify_kind` propagation + `available_openers` population.
- `backend/nexus/tests/interview_engine/speaker/test_naturalness.py` — tests for tuple-keyed soft targets + `detect_solution_leak`.
- `backend/nexus/tests/interview_engine/state/test_engine.py` — test for the Judge-voluntary `is_post_cap_advance` case.

**Total: 14 files (2 new, 12 modified).**

---

## Task ordering rationale

Tasks land in this order for bisectability and to keep each commit independently mergeable:

1. **`ClarifyKind` enum + schema** — additive; no consumer yet.
2. **Speaker schema fields** — additive; no consumer yet.
3. **`openers.py` helper + tests** — new module, no consumer yet.
4. **`naturalness.py` refactor** — switches to `openers.py`; behavior preserved.
5. **`naturalness.py` extensions** — adds tuple-keyed soft targets + `solution_leak_suspected`; backward-compatible.
6. **`input_builder.py` integration** — `available_openers` flows; `clarify_kind` flows; SE still doesn't set it yet but field is nullable.
7. **State Engine — `is_post_cap_advance` fix** — pure bug fix, no schema dependency.
8. **State Engine — `clarify_kind` propagation** — pull from Judge payload, pass through.
9. **Judge prompt rewrite** — Judge starts emitting `clarify_kind`; Speaker still uses old dispatch (clarify.txt unchanged at this point).
10. **Speaker prompt rewrite** — clarify.txt 5-PATH dispatch. **Visible behavior change lands here.**
11. **Preamble change** — adds `available_openers` paragraph.
12. **CHANGELOG entry + replay test on a13ec188**.

Each step has its own tests. The replay test in step 12 is the integration safety net.

---

## Task 1: Add `ClarifyKind` enum and extend `ClarifyPayload`

**Files:**
- Modify: `app/modules/interview_engine/models/judge.py:114-115`
- Test: `tests/interview_engine/judge/test_judge_output_validation.py` (extend existing file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine/judge/test_judge_output_validation.py`:

```python
import pytest
from pydantic import ValidationError

from app.modules.interview_engine.models.judge import (
    ClarifyKind,
    ClarifyPayload,
    JudgeOutput,
    NextAction,
    TurnMetadata,
)


def _base_judge_output_kwargs() -> dict:
    return {
        "reasoning": "x" * 30,
        "observations": [],
        "candidate_claims": [],
        "next_action": NextAction.clarify,
        "turn_metadata": TurnMetadata(),
    }


def test_clarify_payload_requires_clarify_kind() -> None:
    # Bare {"kind": "clarify"} without clarify_kind must raise.
    with pytest.raises(ValidationError):
        ClarifyPayload(**{})  # type: ignore[arg-type]


def test_clarify_payload_accepts_all_five_clarify_kinds() -> None:
    for kind in (
        ClarifyKind.term_definition,
        ClarifyKind.concept_explanation,
        ClarifyKind.use_case_anchor,
        ClarifyKind.broad_rephrase,
        ClarifyKind.probe_context,
    ):
        payload = ClarifyPayload(clarify_kind=kind)
        assert payload.clarify_kind == kind
        assert payload.kind == "clarify"


def test_judge_output_with_clarify_payload_concept_explanation_validates() -> None:
    payload = ClarifyPayload(clarify_kind=ClarifyKind.concept_explanation)
    out = JudgeOutput(
        **_base_judge_output_kwargs(),
        next_action_payload=payload,
    )
    assert out.next_action == NextAction.clarify
    assert isinstance(out.next_action_payload, ClarifyPayload)
    assert out.next_action_payload.clarify_kind == ClarifyKind.concept_explanation


def test_clarify_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ClarifyPayload(clarify_kind="invalid_kind")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_output_validation.py -v -k "clarify_kind or concept_explanation"
```

Expected: FAIL with `ImportError: cannot import name 'ClarifyKind'` and `clarify_kind` field unrecognized.

- [ ] **Step 3: Add `ClarifyKind` enum and extend `ClarifyPayload`**

Edit `app/modules/interview_engine/models/judge.py`. At the top of the file, after the existing `NextAction(StrEnum)` definition (around line 19), add:

```python
class ClarifyKind(StrEnum):
    """Sub-classification of a clarify action — see judge prompt §1.3.

    Five sub-cases of "candidate doesn't understand or needs context":

    - ``term_definition``     — asked about a specific term ("What is X?")
    - ``concept_explanation`` — engaged with topic, asks WHY a concept
                                matters / asks for failure mode
    - ``use_case_anchor``     — asks for business or operational setting
                                (including bare "give me an example")
    - ``broad_rephrase``      — generic confusion, no specific ask
    - ``probe_context``       — confused after a probe was delivered
    """
    term_definition     = "term_definition"
    concept_explanation = "concept_explanation"
    use_case_anchor     = "use_case_anchor"
    broad_rephrase      = "broad_rephrase"
    probe_context       = "probe_context"
```

Then replace the existing `ClarifyPayload` (lines 114-115):

```python
class ClarifyPayload(BaseModel):
    kind: Literal["clarify"] = "clarify"
    clarify_kind: ClarifyKind = Field(
        description=(
            "Sub-classification picked by the Judge per prompt §1.3. "
            "Drives Speaker dispatch in clarify.txt (5 PATH sections). "
            "Default `broad_rephrase` is the safest fallback path — "
            "the synthesizer fallback uses it when validation fails on "
            "a clarify-shaped output."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_output_validation.py -v
```

Expected: all tests PASS (the new four + the existing suite still green).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/models/judge.py \
        backend/nexus/tests/interview_engine/judge/test_judge_output_validation.py
git commit -m "schema(judge): add ClarifyKind enum + clarify_kind on ClarifyPayload

Five-value discriminator on the Judge->Speaker boundary for clarify
intent: term_definition / concept_explanation / use_case_anchor /
broad_rephrase / probe_context. No consumer yet; Judge prompt and
Speaker dispatch land in subsequent commits.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 2: Extend `SpeakerInput` with `clarify_kind` and `available_openers`

**Files:**
- Modify: `app/modules/interview_engine/models/speaker.py:31-93`
- Test: `tests/interview_engine/speaker/test_input_builder.py` (extend existing file)

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/speaker/test_input_builder.py`:

```python
from app.modules.interview_engine.models.judge import ClarifyKind
from app.modules.interview_engine.models.speaker import (
    InstructionKind, SpeakerInput,
)


def test_speaker_input_accepts_clarify_kind_and_available_openers() -> None:
    si = SpeakerInput(
        instruction_kind=InstructionKind.clarify,
        persona_name="Arjun",
        clarify_kind=ClarifyKind.concept_explanation,
        available_openers=["See —", "Right, so —", "Hmm —"],
    )
    assert si.clarify_kind == ClarifyKind.concept_explanation
    assert si.available_openers == ["See —", "Right, so —", "Hmm —"]


def test_speaker_input_defaults_clarify_kind_to_none_and_openers_to_empty() -> None:
    si = SpeakerInput(
        instruction_kind=InstructionKind.deliver_question,
        persona_name="Arjun",
    )
    assert si.clarify_kind is None
    assert si.available_openers == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py -v -k "clarify_kind or available_openers"
```

Expected: FAIL — `clarify_kind` and `available_openers` are unknown fields.

- [ ] **Step 3: Extend `SpeakerInput`**

Edit `app/modules/interview_engine/models/speaker.py`. Add the import at line 14 (after the existing `from app.modules.interview_engine.models.judge import TurnMetadata`):

```python
from app.modules.interview_engine.models.judge import ClarifyKind, TurnMetadata
```

Then, inside the `SpeakerInput` class (after `is_post_cap_advance` field at line 80-92), add:

```python
    clarify_kind: ClarifyKind | None = Field(
        default=None,
        description=(
            "Sub-classification of the clarify intent — see judge prompt "
            "§1.3. Populated by build_speaker_input ONLY when "
            "instruction_kind == clarify; None for all other kinds. "
            "Drives PATH dispatch inside speaker/clarify.txt."
        ),
    )
    available_openers: list[str] = Field(
        default_factory=list,
        description=(
            "PersonaSpec.opener_rotation filtered against "
            "recent_reply_starts for THIS turn. Populated for every kind. "
            "The Speaker scaffold prefers an opener from this list — the "
            "system rotation is the master persona, available_openers is "
            "the per-turn allowed subset that respects the anti-repetition "
            "rule deterministically."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py -v
```

Expected: PASS (new two + existing suite green).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/models/speaker.py \
        backend/nexus/tests/interview_engine/speaker/test_input_builder.py
git commit -m "schema(speaker): add clarify_kind + available_openers on SpeakerInput

clarify_kind propagates the Judge's sub-classification through to the
Speaker. available_openers carries the per-turn-filtered opener rotation
so anti-repetition is deterministic at the input layer (model can't
ignore a 'NEVER use these' instruction). No consumer yet.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 3: Create `openers.py` helper module

**Files:**
- Create: `app/modules/interview_engine/speaker/openers.py`
- Test: `tests/interview_engine/speaker/test_openers.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/interview_engine/speaker/test_openers.py`:

```python
"""Unit tests for opener slug + filter helpers."""
from app.modules.interview_engine.speaker.openers import (
    filter_available_openers,
    opener_slug,
)


# ---------------- opener_slug ----------------


def test_opener_slug_truncates_to_three_words_lowercase() -> None:
    assert opener_slug("Mm, OK — kindly walk me") == "mm, ok —"
    assert opener_slug("See — kindly walk me through") == "see — kindly"
    assert opener_slug("Right, so — for this case") == "right, so —"


def test_opener_slug_handles_whitespace_and_punctuation() -> None:
    assert opener_slug("   See   —   walk  ") == "see — walk"
    assert opener_slug("") == ""
    assert opener_slug("   ") == ""


def test_opener_slug_under_three_words_returns_full() -> None:
    assert opener_slug("See") == "see"
    assert opener_slug("See —") == "see —"


# ---------------- filter_available_openers ----------------


_ROTATION: tuple[str, ...] = (
    "See —",
    "Right, so —",
    "Mm, OK —",
    "Let me put it this way —",
    "Thanks for that. Now —",
    "Got it. Let's —",
    "Fair enough —",
    "I see —",
    "Hmm —",
)


def test_filter_empty_recent_returns_full_rotation() -> None:
    assert filter_available_openers(_ROTATION, []) == list(_ROTATION)


def test_filter_removes_used_openers_by_slug() -> None:
    used = ["Mm, OK — kindly walk", "See — kindly walk me"]
    fresh = filter_available_openers(_ROTATION, used)
    assert "Mm, OK —" not in fresh
    assert "See —" not in fresh
    assert "Right, so —" in fresh
    assert "Hmm —" in fresh


def test_filter_case_insensitive() -> None:
    used = ["mm, ok — walk me"]
    fresh = filter_available_openers(_ROTATION, used)
    assert "Mm, OK —" not in fresh


def test_filter_empty_safety_fallback_returns_full_rotation() -> None:
    # If every opener's 3-word slug matches a recent start, the safety
    # branch returns the full rotation rather than emitting an empty list.
    used = [opener_slug(op) for op in _ROTATION]
    fresh = filter_available_openers(_ROTATION, used)
    assert fresh == list(_ROTATION)


def test_filter_ignores_whitespace_only_recent_entries() -> None:
    used = ["   ", "", "Mm, OK — let's"]
    fresh = filter_available_openers(_ROTATION, used)
    assert "Mm, OK —" not in fresh
    # The whitespace entries did NOT cause anything else to be filtered.
    assert "See —" in fresh
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_openers.py -v
```

Expected: FAIL with `ModuleNotFoundError: app.modules.interview_engine.speaker.openers`.

- [ ] **Step 3: Create `openers.py`**

Create `app/modules/interview_engine/speaker/openers.py`:

```python
"""Opener slug computation + per-turn filtering.

Single source of truth for the 3-word slug used by both:

- ``naturalness.detect_repeated_opener`` (post-hoc flagging)
- ``input_builder.build_speaker_input`` (pre-prompt filtering of
  PersonaSpec.opener_rotation against recent_reply_starts)

Lifting this here removes the duplication that previously had
``naturalness.py`` owning the slug definition while ``input_builder.py``
needed its own copy.
"""
from __future__ import annotations


def opener_slug(text: str) -> str:
    """Lowercase first-3-word slug.

    Matches naturalness.detect_repeated_opener's slug definition (3
    words, post LiveKit/Vapi guidance — see naturalness.py docstring).
    Whitespace-normalized; empty input returns empty string.
    """
    return " ".join(text.strip().split()[:3]).lower()


def filter_available_openers(
    rotation: tuple[str, ...],
    recent_reply_starts: list[str],
) -> list[str]:
    """Return ``rotation`` pruned of openers whose 3-word slug matches
    any entry in ``recent_reply_starts``.

    Safety: at current dimensions (9 openers in
    ``PersonaSpec.opener_rotation``, 3 entries in
    ``recent_reply_starts`` per StateEngine._RECENT_REPLY_WINDOW), the
    filter cannot empty the rotation. If a future change inverts this
    (rotation shrinks or window grows), the safety branch returns the
    full rotation rather than emitting an empty bullet list — the
    Speaker prompt requires non-empty available_openers.
    """
    used = {opener_slug(s) for s in recent_reply_starts if s.strip()}
    fresh = [op for op in rotation if opener_slug(op) not in used]
    return fresh or list(rotation)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_openers.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speaker/openers.py \
        backend/nexus/tests/interview_engine/speaker/test_openers.py
git commit -m "feat(speaker): add openers.py helper for slug + filter

Lifts the 3-word slug definition out of naturalness.py into a shared
module so input_builder and naturalness use the same definition.
filter_available_openers prunes opener_rotation by recent_reply_starts;
safety branch falls back to full rotation if the filter would otherwise
empty the list.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 4: Refactor `naturalness.py` to use `openers.py` (no behavior change)

**Files:**
- Modify: `app/modules/interview_engine/speaker/naturalness.py:12-35`
- Test: `tests/interview_engine/speaker/test_naturalness.py` (existing tests must still pass)

- [ ] **Step 1: Verify existing tests pass before refactor**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_naturalness.py -v
```

Expected: all existing tests pass (baseline).

- [ ] **Step 2: Refactor `detect_repeated_opener` to use shared slug**

Edit `app/modules/interview_engine/speaker/naturalness.py`. Replace lines 12-35 (the entire `detect_repeated_opener` function, which has its own local `_slug`) with:

```python
def detect_repeated_opener(
    output: str, recent_reply_starts: list[str],
) -> bool:
    """True iff the first 3 words of ``output`` match the first 3 words
    of any recent opener.

    Anti-repetition signal. Per LiveKit/Vapi: the highest-frequency
    'sounds AI' tell across turns is the same opener appearing on
    consecutive turns. 3-word slug comparison (was 4-word) so that
    'Mm, OK — an' and 'Mm, OK — iPaaS' both register as the same
    opener — the candidate hears "Mm, OK —" repeat regardless of
    the divergent 4th word.

    Slug definition delegated to ``speaker.openers.opener_slug`` so the
    pre-prompt filter (``filter_available_openers``) and this post-hoc
    detector share one implementation.
    """
    if not output or not recent_reply_starts:
        return False
    from app.modules.interview_engine.speaker.openers import opener_slug
    output_slug = opener_slug(output)
    return any(
        output_slug == opener_slug(start)
        for start in recent_reply_starts if start.strip()
    )
```

- [ ] **Step 3: Run tests to verify they still pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_naturalness.py -v
```

Expected: every existing test still passes (the refactor changed implementation, not behavior).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speaker/naturalness.py
git commit -m "refactor(speaker): delegate detect_repeated_opener slug to openers.py

No behavior change. Removes the duplicate _slug definition; the shared
opener_slug helper is now the single source of truth.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 5: Extend `_SOFT_TARGETS` to tuple keys + add `detect_solution_leak`

**Files:**
- Modify: `app/modules/interview_engine/speaker/naturalness.py:71-99`
- Test: `tests/interview_engine/speaker/test_naturalness.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine/speaker/test_naturalness.py`:

```python
from app.modules.interview_engine.speaker.naturalness import (
    detect_exceeded_soft_target,
    detect_solution_leak,
)


# ---------------- soft target tuple keys ----------------


def test_clarify_concept_explanation_soft_target_is_fifty() -> None:
    # 75 words ≤ 50 * 1.5 → no flag
    output_75 = " ".join(["word"] * 75)
    assert detect_exceeded_soft_target(
        output_75, "clarify", clarify_kind="concept_explanation",
    ) is False
    # 76 words > 50 * 1.5 → flag
    output_76 = " ".join(["word"] * 76)
    assert detect_exceeded_soft_target(
        output_76, "clarify", clarify_kind="concept_explanation",
    ) is True


def test_clarify_term_definition_soft_target_is_twenty_five() -> None:
    output_37 = " ".join(["word"] * 37)  # 25 * 1.5 = 37.5
    assert detect_exceeded_soft_target(
        output_37, "clarify", clarify_kind="term_definition",
    ) is False
    output_38 = " ".join(["word"] * 38)
    assert detect_exceeded_soft_target(
        output_38, "clarify", clarify_kind="term_definition",
    ) is True


def test_clarify_with_none_clarify_kind_falls_back_to_legacy() -> None:
    # Legacy fallback: (clarify, None) -> 35 words. 52 words triggers.
    output_52 = " ".join(["word"] * 52)
    assert detect_exceeded_soft_target(
        output_52, "clarify", clarify_kind=None,
    ) is True
    output_51 = " ".join(["word"] * 51)
    assert detect_exceeded_soft_target(
        output_51, "clarify", clarify_kind=None,
    ) is False


def test_non_clarify_kind_unaffected_by_clarify_kind_param() -> None:
    # deliver_question target = 25 words; clarify_kind irrelevant
    output_38 = " ".join(["word"] * 38)
    assert detect_exceeded_soft_target(
        output_38, "deliver_question", clarify_kind=None,
    ) is True
    output_37 = " ".join(["word"] * 37)
    assert detect_exceeded_soft_target(
        output_37, "deliver_question", clarify_kind="anything",
    ) is False


# ---------------- detect_solution_leak ----------------


def test_solution_leak_fires_on_use_verb_last_sentence() -> None:
    output = (
        "See — retries can re-fire after partial success, so you'd "
        "want to use an idempotency key on the order ID."
    )
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is True


def test_solution_leak_fires_on_implement_verb() -> None:
    output = "Mm — duplicates can happen. Implement a conditional upsert."
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is True


def test_solution_leak_does_not_fire_on_correct_open_ended_question() -> None:
    output = (
        "See — your trigger fires once per new order ID, but the i-Paa-S "
        "workflow itself can retry after a network blip. The retry now "
        "fires the same order ID into the E-R-P again. So — given that, "
        "what would you put in your design to handle it?"
    )
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is False


def test_solution_leak_only_fires_for_concept_explanation_kind() -> None:
    leaky_output = "See — use an idempotency key on the order ID."
    for kind in ("term_definition", "use_case_anchor", "broad_rephrase",
                 "probe_context", None):
        assert detect_solution_leak(leaky_output, clarify_kind=kind) is False
    assert detect_solution_leak(leaky_output, clarify_kind="concept_explanation") is True


def test_solution_leak_inspects_only_last_sentence() -> None:
    # Same verbs in middle sentences are fine.
    output = (
        "See — at-least-once delivery can use the same event twice. "
        "But what would you check first to confirm the duplicates?"
    )
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_naturalness.py -v -k "soft_target or solution_leak"
```

Expected: FAIL with `TypeError: detect_exceeded_soft_target() got an unexpected keyword argument 'clarify_kind'` and `ImportError` for `detect_solution_leak`.

- [ ] **Step 3: Replace `_SOFT_TARGETS` and `detect_exceeded_soft_target`; add `detect_solution_leak`**

Edit `app/modules/interview_engine/speaker/naturalness.py`. Replace the existing `_SOFT_TARGETS` dict (lines 74-84) and `detect_exceeded_soft_target` function (lines 87-99) with:

```python
# Per-kind soft target ceiling (words). Output exceeding this by >50%
# triggers the exceeded_soft_target flag. Keys are
# (instruction_kind, clarify_kind | None) — the second element is None
# for non-clarify kinds and for the legacy clarify fallback when the
# Judge hasn't supplied a clarify_kind (back-compat with pre-intent-layer
# audit envelopes).
_SOFT_TARGETS: dict[tuple[str, str | None], int] = {
    # clarify: per-kind targets reflect the speaker shape required.
    # concept_explanation needs space for a failure scenario; term is
    # one clause + re-ask; probe is short.
    ("clarify", "term_definition"):     25,
    ("clarify", "concept_explanation"): 50,
    ("clarify", "use_case_anchor"):     40,
    ("clarify", "broad_rephrase"):      35,
    ("clarify", "probe_context"):       25,
    ("clarify", None):                  35,  # legacy fallback
    # Non-clarify kinds keep their existing targets unchanged.
    ("deliver_first_question", None):     22,
    ("deliver_question", None):           25,
    ("deliver_probe", None):              18,
    ("push_back", None):                  18,
    ("redirect", None):                   18,
    ("acknowledge_no_experience", None):  12,
    ("polite_close", None):               20,
    ("repeat", None):                     99999,
}


def detect_exceeded_soft_target(
    output: str,
    instruction_kind: str,
    clarify_kind: str | None = None,
) -> bool:
    """True iff ``output`` exceeds the per-kind soft target by >50%.

    Soft caps are not hard caps. The flag fires when the model is
    rambling (1.5x the target), not for normal overrun. Per user
    feedback: hard-capping produces choppy speech.

    Lookup is two-step: first ``(instruction_kind, clarify_kind)``, then
    fallback to ``(instruction_kind, None)``. ``clarify_kind`` is only
    meaningful when ``instruction_kind == 'clarify'`` — for every other
    kind, the second-element-None entry is the target.
    """
    target = (
        _SOFT_TARGETS.get((instruction_kind, clarify_kind))
        or _SOFT_TARGETS.get((instruction_kind, None))
    )
    if not target or not output:
        return False
    return len(output.split()) > int(target * 1.5)


# ---------------- detect_solution_leak ----------------


# Last-sentence leak indicators for PATH E (concept_explanation). When
# the Speaker output's last sentence contains one of these verbs (with
# trailing space to avoid sub-word matches), the model has crossed the
# anti-leak boundary by proposing the fix rather than handing the
# question back. Informational — does not block emission; surfaces in
# the audit envelope so we can grep across sessions.
_LEAK_INDICATOR_VERBS: tuple[str, ...] = (
    "use ", "implement ", "add a ", "store a ", "key on ",
    "track ", "deduplicate by ",
)


def detect_solution_leak(
    output: str,
    clarify_kind: str | None,
) -> bool:
    """True iff PATH E (concept_explanation) output's last sentence
    contains a leak-indicator verb.

    Anti-leak signal specific to concept_explanation: the Speaker is
    supposed to describe ONE failure scenario and hand the question
    back, NOT propose the fix. A last sentence containing "use",
    "implement", "add a", etc. almost always means the model proposed
    the solution.

    Returns False for any other clarify_kind (the signal is meaningless
    outside concept_explanation) and for empty output.
    """
    if clarify_kind != "concept_explanation" or not output:
        return False
    # Take everything after the last period; strip trailing punctuation
    # so a final "." or "?" doesn't interfere with substring matching.
    last_sentence = output.rstrip(".!?").rsplit(".", 1)[-1].lower()
    return any(verb in last_sentence for verb in _LEAK_INDICATOR_VERBS)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_naturalness.py -v
```

Expected: all existing tests + new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speaker/naturalness.py \
        backend/nexus/tests/interview_engine/speaker/test_naturalness.py
git commit -m "feat(speaker): per-clarify_kind soft targets + solution_leak detector

_SOFT_TARGETS now keyed by (instruction_kind, clarify_kind | None) with
backward-compatible (kind, None) fallback. PATH E (concept_explanation)
target is 50 words; term/probe stay at 25; use_case_anchor at 40;
broad_rephrase at 35.

detect_solution_leak is an informational regex post-check that fires
when PATH E output's last sentence contains a leak-indicator verb
('use', 'implement', 'add a', etc.) — the Speaker proposed the fix
instead of handing the question back. Flag-only, no regenerate.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 6: Wire `available_openers` and `clarify_kind` through `input_builder.py`

**Files:**
- Modify: `app/modules/interview_engine/speaker/input_builder.py:80-230`
- Test: `tests/interview_engine/speaker/test_input_builder.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine/speaker/test_input_builder.py`:

```python
from app.modules.interview_engine.models.judge import (
    ClarifyKind, ClarifyPayload, JudgeOutput, NextAction, TurnMetadata,
)
from app.modules.interview_engine.speaker.input_builder import (
    build_speaker_input,
)
from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA
from app.modules.interview_engine.state.claims import CandidateClaimsPool
from app.modules.interview_engine.state.queue import QuestionQueue


def _empty_queue() -> QuestionQueue:
    return QuestionQueue.from_initial(questions=[])


def _judge_clarify(kind: ClarifyKind) -> JudgeOutput:
    return JudgeOutput(
        reasoning="x" * 30,
        observations=[],
        candidate_claims=[],
        next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=kind),
        turn_metadata=TurnMetadata(),
    )


def test_input_builder_propagates_clarify_kind_when_clarify() -> None:
    si = build_speaker_input(
        instruction_kind=InstructionKind.clarify,
        judge_output=_judge_clarify(ClarifyKind.concept_explanation),
        active_question=None,
        queue=_empty_queue(),
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Arjun",
        last_candidate_utterance="why does idempotency matter here?",
    )
    assert si.clarify_kind == ClarifyKind.concept_explanation


def test_input_builder_clarify_kind_none_for_non_clarify_kinds() -> None:
    si = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_judge_clarify(ClarifyKind.concept_explanation),  # payload ignored
        active_question=None,
        queue=_empty_queue(),
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Arjun",
        last_candidate_utterance=None,
    )
    assert si.clarify_kind is None


def test_input_builder_populates_available_openers_for_every_kind() -> None:
    # With three recent_reply_starts that all begin with "Mm, OK —",
    # the filter prunes that opener and keeps the other 8.
    recent = ["Mm, OK — kindly", "Mm, OK — let's", "Mm, OK — that's"]
    for kind in (InstructionKind.deliver_question,
                 InstructionKind.clarify,
                 InstructionKind.push_back,
                 InstructionKind.redirect,
                 InstructionKind.polite_close):
        si = build_speaker_input(
            instruction_kind=kind,
            judge_output=_judge_clarify(ClarifyKind.broad_rephrase),
            active_question=None,
            queue=_empty_queue(),
            claims_pool=CandidateClaimsPool(max_size=50),
            recent_turns=[],
            persona_name="Arjun",
            last_candidate_utterance=None,
            recent_reply_starts=recent,
        )
        assert "Mm, OK —" not in si.available_openers
        assert len(si.available_openers) == len(DEFAULT_PERSONA.opener_rotation) - 1
        # Other openers preserved.
        assert "See —" in si.available_openers


def test_input_builder_available_openers_full_when_recent_empty() -> None:
    si = build_speaker_input(
        instruction_kind=InstructionKind.deliver_first_question,
        judge_output=_judge_clarify(ClarifyKind.broad_rephrase),
        active_question=None,
        queue=_empty_queue(),
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Arjun",
        last_candidate_utterance=None,
        recent_reply_starts=[],
    )
    assert si.available_openers == list(DEFAULT_PERSONA.opener_rotation)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py -v -k "clarify_kind or available_openers"
```

Expected: FAIL — `build_speaker_input` doesn't populate the new fields yet.

- [ ] **Step 3: Modify `build_speaker_input`**

Edit `app/modules/interview_engine/speaker/input_builder.py`. At the top of the file (after the existing imports around line 14), add:

```python
from app.modules.interview_engine.models.judge import (
    ClarifyKind, ClarifyPayload,
)
from app.modules.interview_engine.speaker.openers import filter_available_openers
from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA
```

Then in the body of `build_speaker_input`, after the `reply_starts_payload: list[str] = list(recent_reply_starts or [])` line (around line 197), add:

```python
    # Per-turn opener pruning. PersonaSpec.opener_rotation is the
    # master persona; available_openers is the subset valid for THIS
    # turn (rotation minus recently-used). Populated for every kind —
    # the rotation is a persona-wide concern, not clarify-specific.
    available_openers_payload = filter_available_openers(
        DEFAULT_PERSONA.opener_rotation,
        reply_starts_payload,
    )

    # clarify_kind propagation. The Judge picks the kind on the
    # ClarifyPayload; we surface it on SpeakerInput so the Speaker
    # prompt can dispatch deterministically. Populated ONLY when the
    # instruction is clarify — None for every other kind.
    clarify_kind_payload: ClarifyKind | None = None
    if instruction_kind == InstructionKind.clarify and isinstance(
        judge_output.next_action_payload, ClarifyPayload,
    ):
        clarify_kind_payload = judge_output.next_action_payload.clarify_kind
```

Then update the `return SpeakerInput(...)` call at the end of the function (currently around line 217-230) to add the two new fields:

```python
    return SpeakerInput(
        instruction_kind=instruction_kind,
        bank_text=bank_text,
        last_candidate_utterance=last_candidate_utterance,
        recent_turns=recent_turns_payload,
        claims_pool_snapshot=claims_payload,
        persona_name=persona_name,
        candidate_name=candidate_name,
        failed_signal_value=failed_signal_value,
        turn_metadata=turn_metadata,
        push_back_reason_code=push_back_reason_code,
        recent_reply_starts=reply_starts_payload,
        is_post_cap_advance=post_cap_payload,
        clarify_kind=clarify_kind_payload,
        available_openers=available_openers_payload,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py -v
```

Expected: all tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speaker/input_builder.py \
        backend/nexus/tests/interview_engine/speaker/test_input_builder.py
git commit -m "feat(speaker): populate clarify_kind + available_openers in input_builder

clarify_kind flows from JudgeOutput.next_action_payload only when
instruction_kind == clarify; None otherwise. available_openers is the
PersonaSpec rotation pruned by recent_reply_starts for THIS turn,
populated unconditionally.

Speaker prompt still uses the old single-PATH clarify.txt — dispatch on
clarify_kind lands in a subsequent commit.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 7: Fix `is_post_cap_advance` propagation for Judge-voluntary advances

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py:472-530`
- Test: `tests/interview_engine/state/test_engine.py` (extend existing file)

**Context:** Session a13ec188 T8 — Judge picked `advance` voluntarily knowing push_back_count was at the cap, but `is_post_cap_advance` stayed False because the SE-forced downgrade branch (line 694) was the only place that set it True. This task fixes that.

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/state/test_engine.py`:

```python
from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, PushBackPayload, TurnMetadata,
    Observation, CoverageQuality, CoverageTransition,
)


def test_is_post_cap_advance_fires_on_judge_voluntary_advance_at_cap(
    state_engine_factory,
) -> None:
    """Session a13ec188 T8 reproducer: Judge picked advance knowing the
    push_back cap was reached. is_post_cap_advance must fire so the
    Speaker scaffold adds the topic-shift segue.
    """
    eng = state_engine_factory(num_questions=2)
    # Seed: advance to Q1, then push_back twice to reach cap (=2).
    eng.initialize_for_session_start()
    eng.process_judge_output(
        turn_id="t1",
        judge_output=JudgeOutput(
            reasoning="x" * 30,
            observations=[
                Observation(
                    signal_value="signal_a",  # first signal in fixture
                    anchor_id=0,
                    evidence_quote="some text",
                    coverage_transition=CoverageTransition.none_to_partial,
                    quality=CoverageQuality.thin,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="missing_specifics"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="thin answer",
        elapsed_ms=1000,
    )
    eng.process_judge_output(
        turn_id="t2",
        judge_output=JudgeOutput(
            reasoning="x" * 30,
            observations=[
                Observation(
                    signal_value="signal_a",
                    anchor_id=0,
                    evidence_quote="more thin",
                    coverage_transition=CoverageTransition.partial_to_partial,
                    quality=CoverageQuality.thin,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="missing_specifics"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="still thin",
        elapsed_ms=2000,
    )
    # Now: Judge VOLUNTARILY picks advance knowing the cap is reached.
    # Pre-fix this set is_post_cap_advance=False (only the SE-downgrade
    # path raised it). Post-fix it should fire True.
    decision = eng.process_judge_output(
        turn_id="t3",
        judge_output=JudgeOutput(
            reasoning="x" * 30,
            observations=[],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q_2"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="another thin one",
        elapsed_ms=3000,
    )
    assert decision.speaker_input.is_post_cap_advance is True, (
        "Judge picked advance with push_back_count already at cap; "
        "Speaker must receive is_post_cap_advance=True so it adds the "
        "topic-shift segue (per design 2026-05-18 §9)."
    )


def test_is_post_cap_advance_false_on_clean_advance_below_cap(
    state_engine_factory,
) -> None:
    """Sanity check: advance with push_back_count < 2 must NOT raise
    the flag.
    """
    eng = state_engine_factory(num_questions=2)
    eng.initialize_for_session_start()
    # One push_back (count = 1, below cap).
    eng.process_judge_output(
        turn_id="t1",
        judge_output=JudgeOutput(
            reasoning="x" * 30,
            observations=[
                Observation(
                    signal_value="signal_a",
                    anchor_id=0,
                    evidence_quote="concrete",
                    coverage_transition=CoverageTransition.none_to_partial,
                    quality=CoverageQuality.concrete,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="missing_specifics"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="partial",
        elapsed_ms=1000,
    )
    decision = eng.process_judge_output(
        turn_id="t2",
        judge_output=JudgeOutput(
            reasoning="x" * 30,
            observations=[
                Observation(
                    signal_value="signal_a",
                    anchor_id=1,
                    evidence_quote="more concrete",
                    coverage_transition=CoverageTransition.partial_to_sufficient,
                    quality=CoverageQuality.concrete,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q_2"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="now concrete",
        elapsed_ms=2000,
    )
    assert decision.speaker_input.is_post_cap_advance is False
```

NOTE: this test references a `state_engine_factory` fixture. Inspect `tests/interview_engine/state/test_engine.py` for the actual fixture name in this codebase before running — if it differs (likely `_make_engine` or similar), update both new tests to use the existing fixture pattern. If no factory exists, copy the engine-construction pattern from `tests/interview_engine/state/test_inverse_quality_gate.py` (which has a similar push_back-cap setup).

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/state/test_engine.py -v -k "is_post_cap_advance"
```

Expected: the cap-reproducer FAILS (flag is False where it should be True). The sanity check should pass already.

- [ ] **Step 3: Fix the State Engine**

Edit `app/modules/interview_engine/state/engine.py`. In the `NextAction.advance` branch (starts at line 480), the existing block computes `quality_downgrade` and either downgrades or calls `advance_to(target)`. The `is_post_cap_advance` variable is declared at line 472 and is set to True only inside the push_back-cap branch (line 694) — never inside the `advance` branch.

Capture the push_back_count BEFORE the queue mutation (since `advance_to` resets the per-question counter for the next question). Replace lines 480-530 (the entire `if action == NextAction.advance:` block) with:

```python
        if action == NextAction.advance:
            target = judge_output.next_action_payload.target_question_id
            # Capture push_back_count BEFORE any queue mutation. When the
            # Judge picks advance VOLUNTARILY at the cap (session a13ec188
            # T8 reproducer: "With push-back already at the cap, I should
            # not continue looping; the cleanest move is to advance"), the
            # SE-forced downgrade branch below never fires — so without
            # this we miss the post-cap signal entirely. Speaker scaffold
            # needs the flag to emit the topic-shift segue ("Thanks for
            # that. Now —") instead of jumping cold.
            prior_push_back_count = (
                self._queue.active_push_back_count()
                if self._queue.active_state() is not None else 0
            )
            # Quality gate (Phase 9.2): downgrade `advance` to `push_back`
            # when no observation on the active question has reached
            # `concrete` or `strong`. The Judge prompt §4.5 instructs the
            # model to emit push_back itself in this case; this gate is
            # the deterministic backstop. Skipped on the synthetic
            # session-start advance (no active question yet) and when the
            # cap is already reached (avoid infinite downgrade loops).
            quality_downgrade = (
                self._queue.active_state() is not None
                and not self._queue.active_has_quality_at_least_concrete()
                and self._queue.active_push_back_count() < 2
            )
            if quality_downgrade:
                warnings.append(ValidationWarning(
                    code="quality_gated_advance",
                    level="warning",
                    details={
                        "active_question_id": self._queue.active_question_id(),
                        "original_target": target,
                        "downgraded_to": "push_back",
                        "reason_code": "missing_specifics",
                        "reason": (
                            "advance requires at least one observation on "
                            "the active question to have quality 'concrete' "
                            "or 'strong'; all observations were 'thin'. "
                            "Downgraded to push_back so the Speaker asks "
                            "for specifics. To unblock advance, the next "
                            "Judge turn must emit a concrete observation."
                        ),
                    },
                ))
                self._queue.increment_active_push_back_count()
                instruction = InstructionKind.push_back
                # Mutate the JudgeOutput in-place so build_speaker_input
                # picks up the synthetic push_back payload + reason_code.
                judge_output.next_action = NextAction.push_back
                judge_output.next_action_payload = PushBackPayload(
                    reason_code="missing_specifics",
                )
            else:
                try:
                    self._queue.advance_to(target, at_turn=self._turn_count)
                    instruction = self._first_or_continuing_instruction()
                    # Set the post-cap flag whenever the prior question
                    # had push_back_count at the cap, regardless of who
                    # chose advance (Judge or the SE-forced downgrade in
                    # the push_back branch below). Mirrors the existing
                    # is_post_cap_advance=True at the SE-downgrade site
                    # for consistency in Speaker behavior.
                    if prior_push_back_count >= 2:
                        is_post_cap_advance = True
                except QueueError as exc:
                    warnings.append(ValidationWarning(
                        code="invalid_target_question_id",
                        details={"target": target, "reason": str(exc)},
                    ))
                    instruction = self._fallback_advance_to_next_pending(warnings)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/state/test_engine.py -v -k "is_post_cap_advance" && \
  docker compose run --rm nexus pytest tests/interview_engine/state/ -v
```

Expected: new tests PASS; full state/ test suite still green.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/state/engine.py \
        backend/nexus/tests/interview_engine/state/test_engine.py
git commit -m "fix(state): is_post_cap_advance fires on Judge-voluntary advance at cap

Session a13ec188 T8 reproducer: Judge picked advance directly knowing
push_back_count was at cap (\"the cleanest move is to advance to the
next mandatory question\"). The SE-forced downgrade path never ran, so
is_post_cap_advance stayed False — and the Speaker had to discover the
topic shift on its own.

Now capture prior_push_back_count before the queue mutation in the
NextAction.advance branch; if >= 2, set is_post_cap_advance=True.
Mirrors the existing flag-set in the push_back-cap downgrade branch.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md §9"
```

---

## Task 8: Judge prompt — expanded §1.3 CLARIFY sub-tree + worked examples

**Files:**
- Modify: `backend/nexus/prompts/v2/engine/judge.system.txt:36-42` (§1.3 CLARIFY)
- Modify: `backend/nexus/prompts/v2/engine/judge.system.txt` (§4 CLARIFY description, §8 worked examples)
- Test: `tests/interview_engine/judge/test_judge_prompt_loadable.py` (smoke test only)

- [ ] **Step 1: Inspect the prompt-loadable smoke test**

```bash
cat tests/interview_engine/judge/test_judge_prompt_loadable.py
```

Confirm the loadable test asserts the prompt parses (likely a hash/byte read). The Judge prompt is currently 650 lines; we're appending ~80 lines.

- [ ] **Step 2: Update §1.3 — CLARIFY**

Edit `prompts/v2/engine/judge.system.txt`. Find the existing §1.3 block (line 36-42):

```
3. CLARIFY   — candidate doesn't understand:
                a. asked about a specific term ("what do you mean by
                   validators?")
                b. generically confused ("I don't understand", "can
                   you rephrase?")
              Never use clarify for greetings (redirect) or for
              hint-fishing (redirect).
```

Replace with:

```
3. CLARIFY   — candidate doesn't understand or needs context to
                engage. Pick ONE clarify_kind for the
                next_action_payload:

                a. term_definition — candidate asked about a SPECIFIC
                   TERM in the question.
                   Trigger: "What is X?", "What do you mean by Y?",
                   "What's an ERP?"

                b. concept_explanation — candidate is ENGAGED with
                   the topic but asks WHY a concept matters / asks
                   for the FAILURE MODE of a premise they have
                   stated.
                   Trigger: "Why would there be duplicates if IDs
                   are unique?", "Help me understand why X is a
                   concern here", "How is it that X is a concern
                   given Y?"
                   Disambiguation vs push_back: the candidate is
                   NOT refusing or evading — they are framing the
                   conceptual gap that's blocking their answer. If
                   push_back_count > 0 on this question and the
                   confusion is conceptual (asks WHY / HOW) rather
                   than evasive (deflects, restates without
                   specifics), prefer concept_explanation over
                   another push_back.
                   Disambiguation vs meta_confession:
                   meta_confession says "I cannot answer this"
                   (gives up); concept_explanation says "help me
                   understand WHY" (asks for grounding to answer).

                c. use_case_anchor — candidate asks for the
                   BUSINESS or OPERATIONAL setting.
                   Trigger: "What is the use case for X in this
                   org?", "What kind of company is this?", "What
                   does the team do?", "Can you give me an
                   example?", "Can you give me a specific case?"
                   (bare ask, no qualifier).

                d. broad_rephrase — generic confusion with no
                   specific ask.
                   Trigger: "I don't understand", "Can you
                   rephrase?", "Can you say it differently?",
                   "I'm not following."

                e. probe_context — candidate is confused after the
                   agent just delivered a PROBE (bank_text is
                   short, follow-up shape — the input_builder
                   passes the probe text as bank_text in this
                   case).

                "Give me an example" disambiguation rule:
                  - Bare ("Can you give me an example?")
                    → use_case_anchor.
                  - Qualified with "of why X" / "of how X breaks"
                    / "of a case where X" → concept_explanation.
                  - Qualified with "of what you're looking for"
                    / "of an answer" / "of what's right"
                    → redirect with candidate_off_topic=true
                    (rubric fishing — §1.1).

                Never use clarify for greetings (redirect) or for
                hint-fishing (redirect). The
                consecutive_dont_know_count >= 1 escalation still
                applies regardless of clarify_kind — at that
                count, emit acknowledge_no_experience.
```

- [ ] **Step 3: Update §4 CLARIFY description**

Find the existing §4 CLARIFY section (around line 197-204):

```
CLARIFY
  Two sub-cases:
    1. Candidate asked about a specific term in the active question.
    2. Candidate is generically confused.
  In both cases, the Speaker handles the response — for case 2, the
  Speaker rephrases the whole question (or the probe if a probe was
  the most recent agent utterance — the input_builder handles that).
  Hard rule: if active_question_consecutive_dont_know_count >= 1, do
  NOT emit clarify again — escalate to ACKNOWLEDGE_NO_EXPERIENCE.
```

Replace with:

```
CLARIFY
  Five sub-cases — pick ONE clarify_kind:
    1. term_definition       — asked about a specific term.
    2. concept_explanation   — engaged but asks WHY a concept
                               matters / asks for the failure mode.
    3. use_case_anchor       — asks for business or operational
                               setting (includes bare "give me an
                               example").
    4. broad_rephrase        — generic confusion, no specific ask.
    5. probe_context         — confused after a probe delivery.
  In ALL cases the Speaker handles the response — it dispatches on
  clarify_kind in clarify.txt (5 PATH sections). The Judge picks
  the kind from the candidate's utterance shape per §1.3 above.
  Hard rule: if active_question_consecutive_dont_know_count >= 1,
  do NOT emit clarify again regardless of kind — escalate to
  ACKNOWLEDGE_NO_EXPERIENCE.
```

- [ ] **Step 4: Add §8 worked examples G, H, I**

Find the end of §8 (after EXAMPLE F at the end of the file before §9 FINAL DISCIPLINE). Insert three new examples just before §9:

```
EXAMPLE G — concept_explanation on a follow-up (session a13ec188 T12).

Active question: Q3 (nightly upsert duplicates triage). Mandatory.
push_back_count=0. Coverage: partial. Probes remain.

Candidate: "That's what I need to understand, how is it that
idempotency is a concern here because we are taking unique order IDs
as a trigger for the recipes. So it's very hard for me to understand
how a unique order ID being used as a trigger would lead to a
situation where idempotency has to be enforced."

{
  "reasoning": "Candidate is engaged with the active question and
  asks WHY idempotency is a concern given unique order IDs as the
  trigger — that's a conceptual gap, not evasion. They are framing
  the failure mode they don't understand. Per §1.3 disambiguation:
  this is concept_explanation, not push_back. Action: clarify with
  clarify_kind=concept_explanation; the Speaker will describe ONE
  failure scenario without naming the fix.",
  "observations": [],
  "candidate_claims": [],
  "next_action": "clarify",
  "next_action_payload": {"kind": "clarify",
                          "clarify_kind": "concept_explanation"},
  "turn_metadata": {}
}

EXAMPLE H — use_case_anchor from a bare example ask.

Active question: Q1 (iPaaS order sync). Coverage: none.

Candidate: "Can you give me an example?"

{
  "reasoning": "Bare ask for an example with no qualifier. Per §1.3
  disambiguation: bare 'give me an example' → use_case_anchor. The
  Speaker provides the business setting (domain, operational
  dependence) that grounds the abstract question. No content yet to
  observe.",
  "observations": [],
  "candidate_claims": [],
  "next_action": "clarify",
  "next_action_payload": {"kind": "clarify",
                          "clarify_kind": "use_case_anchor"},
  "turn_metadata": {}
}

EXAMPLE I — concept_explanation chosen over further push_back.

Active question: Q1 (iPaaS order sync). Mandatory. push_back_count=1.
Coverage: partial. Probes remain.

Candidate: "Why would there be duplicates for the same order ID? I
mean, every order created in the Salesforce has their own unique
order ID. Why would I mean, help me understand why would the same
order ID be created twice?"

{
  "reasoning": "Candidate stays on-topic and frames a conceptual
  gap: 'help me understand why would the same order ID be created
  twice'. push_back_count is already 1, and the confusion is
  conceptual (asks WHY) rather than evasive. Per §1.3
  disambiguation: prefer concept_explanation over another
  push_back. Action: clarify with clarify_kind=concept_explanation.",
  "observations": [],
  "candidate_claims": [],
  "next_action": "clarify",
  "next_action_payload": {"kind": "clarify",
                          "clarify_kind": "concept_explanation"},
  "turn_metadata": {}
}
```

- [ ] **Step 5: Run the prompt-loadable smoke test**

```bash
docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_prompt_loadable.py -v
```

Expected: PASS (the prompt parses; the smoke test just confirms the file exists and is valid).

- [ ] **Step 6: Sanity-check the prompt grew**

```bash
wc -l backend/nexus/prompts/v2/engine/judge.system.txt
```

Expected: ~720+ lines (was ~650 pre-change).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/prompts/v2/engine/judge.system.txt
git commit -m "prompts(judge): expand CLARIFY sub-tree to 5 clarify_kind cases

§1.3 now distinguishes term_definition / concept_explanation /
use_case_anchor / broad_rephrase / probe_context with explicit
disambiguation rules:

- concept_explanation vs push_back: engaged-but-conceptual ask is not
  evasion; route to clarify when push_back_count > 0 and the candidate
  is asking WHY/HOW rather than deflecting.
- concept_explanation vs meta_confession: 'I cannot answer' (gives up)
  vs 'help me understand WHY' (asks for grounding).
- 'give me an example' trichotomy: bare → use_case_anchor; qualified
  'of why X' → concept_explanation; qualified 'of what you're looking
  for' → redirect (rubric fishing).

§4 CLARIFY description rewritten to reflect 5 sub-cases. Three new
worked examples (G/H/I) cover concept_explanation on follow-up,
use_case_anchor from bare example ask, and concept_explanation chosen
over further push_back.

Speaker dispatch on clarify_kind lands in the next commit.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md §5"
```

---

## Task 9: Speaker clarify.txt rewrite — 5 PATH dispatch

**Files:**
- Modify: `backend/nexus/prompts/v2/engine/speaker/clarify.txt` (full rewrite, 100 → ~300 lines)
- Test: `tests/interview_engine/speaker/test_speaker_prompt_loadable.py` (smoke only)

- [ ] **Step 1: Inspect the existing smoke test**

```bash
cat tests/interview_engine/speaker/test_speaker_prompt_loadable.py
```

Confirm scope: just ensures prompts load. No content asserts to maintain.

- [ ] **Step 2: Replace `clarify.txt` with the 5-PATH version**

Replace the entire contents of `backend/nexus/prompts/v2/engine/speaker/clarify.txt` with:

```
# TASK
The candidate needs help. The Judge has classified WHAT KIND of help
via `clarify_kind` in your input. Dispatch on clarify_kind. ONE PATH
only.

# SHAPE BY PATH (soft targets — flagged at 1.5×, not enforced)
  term_definition       ≈ 25 words   one clause + re-ask
  concept_explanation   ≈ 50 words   one failure scenario + hand-back
  use_case_anchor       ≈ 40 words   business setting + re-ask
  broad_rephrase        ≈ 35 words   domain + volume + contrast + re-ask
  probe_context         ≈ 25 words   short definition + probe re-ask

# OPENERS
Pick from `available_openers` in your input — the rotation already
pruned of openers you used recently. NEVER use anything in
`recent_reply_starts`. The system rotation is the master persona;
available_openers is the per-turn allowed subset.

# ARJUN'S SHAPE
- ONE final ask per turn. NO stacked questions.
- Indian English register: "See —", "Kindly", "itself", "Let us".
- NEVER reveal rubric content. You expose the gap, never fill it.

# ANTI-ENUMERATION (load-bearing — applies to ALL paths)
Do NOT enumerate the sub-components of bank_text. Forbidden shapes:
  • "How does it fetch data AND handle errors AND retry?" (four-part
    question disguised as one)
  • "Where would you model the steps, what would you attach, and how
    would you make it reusable?" (three explicit rubric criteria)
Pick the BROADEST scenario shape and let the candidate surface the
criteria themselves.

# PATH DISPATCH

## PATH A — clarify_kind: term_definition
Candidate asked about a SPECIFIC TERM in the question.
  ("What is X?", "What do you mean by Y?")

Shape:
  1. Define the term plainly in one short clause.
  2. Re-ask in the broadest form.

Soft target: 25 words.

## PATH B — clarify_kind: broad_rephrase
Generic confusion, no specific ask.
  ("I don't understand", "Can you rephrase?", "I'm not following.")

Shape:
  Rephrase by anchoring to a concrete business setting. Bring THREE
  moves explicitly:
    1. Domain  — e-commerce / fintech / healthcare / SaaS / logistics
    2. Volume  — a concrete number ("about 10k orders a day")
    3. Contrast — what the answer is NOT ("minutes, not a nightly batch")
  Then re-ask in the broadest single form.

  This is NOT a rephrasing — it's a context-bringing. If your output
  is just a shorter version of the bank_text, you've missed the path.

Soft target: 35 words.

## PATH C — clarify_kind: probe_context
Candidate is confused after a probe delivery.
  (bank_text is short, < 30 words, follow-up shape)

Shape:
  1. Define the term-of-art from the probe (e.g., "by metrics I mean
     error rate, p95, MTTR").
  2. Re-ask the PROBE — do NOT widen back to the main question.

Soft target: 25 words.

## PATH D — clarify_kind: use_case_anchor
Candidate asks for the business or operational setting.
  ("What is the use case?", "What kind of company is this?",
   "Can you give me an example?")

Shape:
  Provide the business or operational setting that grounds the
  question. Three components:
    1. Domain ("a digital-native retailer", "a B2B SaaS company")
    2. The operational role of each system in scope
    3. What goes wrong without the integration (the stakeholder pain)
  Then re-ask in the broadest single form.

  Anti-leak boundary: setting is allowed; design criteria are NOT.
  Do not say "the candidate should think about reliability" or
  "consider integration patterns" — that's rubric coaching.

Soft target: 40 words.

## PATH E — clarify_kind: concept_explanation [LOAD-BEARING ANTI-LEAK]
Candidate is engaged with the topic but asks WHY a concept matters /
asks for the failure mode of a premise they have stated.
  ("Why would there be duplicates if IDs are unique?",
   "How is it that idempotency is a concern given X?",
   "Help me understand why X matters here.")

Shape:
  1. Acknowledge the premise the candidate stated.
  2. Describe ONE concrete failure scenario where that premise
     breaks. Generic infra reality is fair game (retries, network
     partitions, partial writes, at-least-once delivery, race
     conditions, ordering). Pick ONE scenario, not a menu.
  3. Hand the question back — open-ended, no nudge toward the fix.

Anti-leak boundary specific to PATH E (load-bearing):
  ALLOWED:   "Retries can re-fire after a partial success."
  ALLOWED:   "At-least-once delivery means the same event can land twice."
  FORBIDDEN: "...so you'd want an idempotency key on the order ID."
  FORBIDDEN: "...the fix is a conditional upsert."
  FORBIDDEN: enumerating multiple solutions ("retries OR dedup OR ...").

RULE: After describing the failure, the candidate must still have
work to do. If your last sentence proposes a fix or names a design
pattern, you've leaked. Trim and re-emit.

WORDS that almost always indicate leak in the last sentence:
"use", "implement", "add a", "store a", "key on", "track",
"deduplicate by". If your output contains these in the last
sentence, you've crossed the boundary.

Soft target: 50 words.

# EXEMPLARS

## PATH A — term_definition
EXAMPLE A1:
  last_candidate_utterance: "What is an ERP?"
  bank_text: "How would you build a near-real-time order sync from
     Salesforce to an on-prem ERP?"
  → Mm — an E-R-P is the system a company uses for back-office
    work — orders, inventory, accounting. With that in mind, how
    would you build the sync?

EXAMPLE A2:
  last_candidate_utterance: "What do you mean by upsert?"
  bank_text: "A nightly job that upserts invoices to NetSuite has
     started producing duplicates."
  → See — an upsert is insert-or-update keyed on a business key.
    With that in mind, walk me through your triage approach.

## PATH B — broad_rephrase (REWRITTEN: bring domain + volume + contrast)
EXAMPLE B1:
  last_candidate_utterance: "I couldn't understand the question."
  bank_text: "Using your primary iPaaS, how would you implement a
     near-real-time order sync from Salesforce to an on-prem E-R-P?"
  → Mm — picture a digital-native retailer doing about 10k orders
    a day, all flowing from Salesforce into their on-prem E-R-P.
    Fulfillment needs them visible in minutes, not in a nightly
    batch. How would you design that integration end-to-end?

EXAMPLE B2:
  last_candidate_utterance: "Can you rephrase?"
  bank_text: "Inheriting a portfolio of ~40-50 integrations across
     cloud and on-prem — first 90 days, how would you assess,
     stabilize, and standardize?"
  → See — say you joined a logistics company tomorrow, ~40 production
    integrations between their TMS and their carriers, half of them
    failing silently every week. What's your first move?

## PATH C — probe_context
EXAMPLE C1:
  bank_text (probe): "Which specific metrics would you weigh most?"
  last_candidate_utterance: "I didn't quite get the question."
  → Mm, OK — by metrics I mean error rate, p95 latency, M-T-T-R.
    Which of those would matter most when picking which integration
    to fix first?

## PATH D — use_case_anchor (NEW)
EXAMPLE D1:
  last_candidate_utterance: "What is the use case that Salesforce is
     being used in the organization?"
  bank_text: "How would you build a near-real-time order sync from
     Salesforce to an on-prem E-R-P?"
  → See — say you're working with a digital-native retailer.
    Salesforce is their customer-facing order desk; the E-R-P is the
    back-office that fulfillment, finance, and inventory all live in.
    Without the sync, fulfillment doesn't see new orders. With that
    setting in mind, how would you design the flow?

EXAMPLE D2:
  last_candidate_utterance: "Can you give me an example?"
  bank_text: "Inheriting ~40-50 integrations — first 90 days, how
     would you stabilize?"
  → Right, so — picture a mid-market B2B SaaS company. About 40
    integrations between their Salesforce, their NetSuite, and a
    couple of warehouse systems. Half are nightly batches written
    five years ago; a few break silently every Friday. With that
    estate in mind, what's your first move?

## PATH E — concept_explanation (NEW, load-bearing anti-leak)
EXAMPLE E1 — CORRECT (session a13ec188 T12 scenario):
  last_candidate_utterance: "how is it that idempotency is a concern
     here because we are taking unique order IDs as a trigger"
  bank_text: "A nightly job that upserts invoices to NetSuite has
     started producing duplicates. Walk through your triage."
  → See — your trigger fires once per new order ID, but the i-Paa-S
    workflow itself can retry after a network blip. Imagine the
    first attempt completed the E-R-P write just before the timeout.
    The retry now fires the same order ID into the E-R-P again. So
    — given that, what would you put in your design to handle it?

EXAMPLE E2 — CORRECT (different failure mode):
  last_candidate_utterance: "Why would the order ever land out of
     order?"
  bank_text: "Walk through end-to-end reliability for the sync."
  → Mm — say two updates to the same order fire close together: a
    cancellation right after a creation, both within a few hundred
    milliseconds. If the iPaaS dispatches them on parallel workers,
    the cancel might land in the E-R-P before the create.
    So — what would you put in your design to make sure the order
    is right?

EXAMPLE E3 — WRONG (anti-pattern, with rationale):
  Output: "See — retries can re-fire after a partial success, so
    you'd want an idempotency key on the order ID to prevent
    duplicate writes."
  Why wrong: the last clause ("so you'd want an idempotency key on
    the order ID") proposes the solution. Candidate has nothing to
    think about. Trim everything after "partial success" and hand
    the question back instead — e.g., "...partial success. So —
    given that, what would you put in your design to handle it?"

# REMINDER
Expose the gap, never fill it. Per-PATH soft targets. ONE final ask.
NO rubric leak. Pick from available_openers.
```

- [ ] **Step 3: Run the smoke test**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py -v
```

Expected: PASS.

- [ ] **Step 4: Verify file sizing**

```bash
wc -l backend/nexus/prompts/v2/engine/speaker/clarify.txt
```

Expected: ~250-300 lines.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/prompts/v2/engine/speaker/clarify.txt
git commit -m "prompts(speaker): rewrite clarify.txt with 5 PATH dispatch

PATH A (term_definition) and PATH C (probe_context) preserve current
behavior with one new exemplar each. PATH B (broad_rephrase) rewritten
to bring domain+volume+contrast in every exemplar instead of restating
bank_text. PATH D (use_case_anchor, NEW) provides business setting
without leaking design criteria. PATH E (concept_explanation, NEW)
carries the load-bearing anti-leak boundary: describe ONE failure
scenario, hand the question back open-ended, never propose the fix.
Explicit forbidden-verb list ('use', 'implement', 'add a', etc.) plus
a WRONG-with-rationale exemplar.

Soft targets per kind: term=25 / concept=50 / use_case=40 /
broad=35 / probe=25 (matches naturalness._SOFT_TARGETS).

The Speaker dispatches on the clarify_kind field that input_builder
now propagates (Task 6). With this commit the visible behavior change
lands.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md §6"
```

---

## Task 10: Append `available_openers` paragraph to `_preamble.txt`

**Files:**
- Modify: `backend/nexus/prompts/v2/engine/speaker/_preamble.txt:67-70`
- Modify: `backend/nexus/prompts/v2/engine/CHANGELOG.md`

- [ ] **Step 1: Append to §OPENINGS & ANTI-REPETITION**

Edit `prompts/v2/engine/speaker/_preamble.txt`. Find the existing §OPENINGS & ANTI-REPETITION block (lines 67-70):

```
# OPENINGS & ANTI-REPETITION
recent_reply_starts (in your input) lists the first 3-4 words of your
last several replies. NEVER open this turn with any string in that
list. Rotate through your discourse markers.
```

Append (after the existing paragraph, still under the same §OPENINGS heading):

```

For THIS turn, your input includes `available_openers` — the rotation
already pruned of openers you used recently. Pick one from there. The
system rotation above is the master persona; available_openers is the
per-turn allowed subset. recent_reply_starts is the forbidden list.
```

- [ ] **Step 2: Append CHANGELOG entry**

Edit `backend/nexus/prompts/v2/engine/CHANGELOG.md`. Add a new section at the top under the v2 banner:

```markdown
## 2026-05-18 — Intent layer (clarify_kind + opener pruning)

**Judge:**
- §1.3 CLARIFY sub-tree expanded from 2 sub-cases to 5
  (term_definition / concept_explanation / use_case_anchor /
  broad_rephrase / probe_context).
- Added disambiguation rules: concept_explanation vs push_back,
  vs meta_confession; "give me an example" trichotomy.
- §4 CLARIFY description updated.
- §8 worked examples G, H, I added (concept_explanation,
  use_case_anchor, concept_explanation chosen over push_back).

**Speaker:**
- `clarify.txt` rewrites the dispatch into 5 PATH sections keyed
  on the new `clarify_kind` field in SpeakerInput. PATH B
  rewritten to model domain+volume+contrast. PATH D and PATH E
  are new. PATH E carries a load-bearing anti-leak boundary
  with an explicit forbidden-verb list.
- `_preamble.txt` §OPENINGS gains a paragraph referencing
  `available_openers` — the per-turn pruned rotation supplied
  in the user message.

**Code (not prompt, but tied to this rev):**
- New helper `app/modules/interview_engine/speaker/openers.py`
  with `opener_slug` + `filter_available_openers`. Lifted out
  of `naturalness.py`.
- `SpeakerInput.clarify_kind` and `SpeakerInput.available_openers`
  added.
- `_SOFT_TARGETS` keyed by `(instruction_kind, clarify_kind|None)`
  with backward-compat fallback.
- `detect_solution_leak` informational flag for PATH E.
- State Engine fix: `is_post_cap_advance` now fires whenever
  `push_back_count >= 2` before an advance, regardless of
  whether the Judge or the SE chose advance.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md
```

- [ ] **Step 3: Smoke-test prompt loading**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py tests/interview_engine/judge/test_judge_prompt_loadable.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v2/engine/speaker/_preamble.txt \
        backend/nexus/prompts/v2/engine/CHANGELOG.md
git commit -m "prompts(speaker): preamble references available_openers + CHANGELOG

_preamble.txt §OPENINGS gains a paragraph telling the model that
available_openers is the per-turn allowed subset and recent_reply_starts
is the forbidden list. Master rotation in the persona stays unchanged.

CHANGELOG entry covers the full 2026-05-18 intent-layer rev across
Judge prompt + Speaker prompts + supporting code.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md"
```

---

## Task 11: Replay test on session a13ec188

**Files:**
- Create: `tests/interview_engine/test_replay_a13ec188.py`

**Context:** Integration safety net. Replays the four load-bearing turns from session a13ec188 (T2, T5, T8, T12) and asserts the Judge picks the expected `clarify_kind`. Does not test exact Speaker strings (LLM nondeterminism); the unit tests in earlier tasks cover the Speaker-input plumbing.

The replay uses the existing JudgeService against the new prompt. Mark this test under the existing `prompt_quality` marker so it is gated off the default suite.

- [ ] **Step 1: Write the replay test**

Create `tests/interview_engine/test_replay_a13ec188.py`:

```python
"""Replay assertion: session a13ec188 load-bearing turns must route
to the expected clarify_kind under the v2 intent-layer prompts.

Gated under `pytest -m prompt_quality` (LLM-in-the-loop, costs OpenAI
credits, nondeterministic). Run by hand after major Judge prompt
changes.

Fixture: backend/nexus/engine-events/a13ec188-ebf4-4b5e-95fa-912555a556a7.json
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.modules.interview_engine.models.judge import ClarifyKind, NextAction


SESSION_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "engine-events"
    / "a13ec188-ebf4-4b5e-95fa-912555a556a7.json"
)


def _load_judge_inputs_by_turn_index() -> dict[int, dict]:
    """Index judge.call input_summary payloads by their turn_index."""
    with SESSION_PATH.open() as f:
        data = json.load(f)
    events = data["events"]

    # Build turn_index -> turn_id map from turn.started.
    turn_index_by_id: dict[str, int] = {}
    for e in events:
        if e.get("kind") == "turn.started":
            p = e["payload"]
            turn_index_by_id[p["turn_id"]] = p["turn_index"]

    # Pull judge.call input_summary per turn_id.
    inputs_by_index: dict[int, dict] = {}
    for e in events:
        if e.get("kind") != "judge.call":
            continue
        p = e["payload"]
        idx = turn_index_by_id.get(p["turn_id"])
        if idx is not None:
            inputs_by_index[idx] = p.get("input_summary", {})
    return inputs_by_index


@pytest.mark.prompt_quality
@pytest.mark.parametrize(
    "turn_index, candidate_utterance, expected_kind",
    [
        (
            1,
            "Okay I couldn't understand the question. Can you?",
            ClarifyKind.broad_rephrase,
        ),
        (
            4,
            "What is the use case that Salesforce is being used in the "
            "Organization That helps me to set up the trigger, exact "
            "trigger, which is why I'm asking the question.",
            ClarifyKind.use_case_anchor,
        ),
        (
            7,
            "Why would there be duplicates for the same order ID? I mean, "
            "every order created in the Salesforce has their own unique "
            "order ID. Why would I mean, help me understand why would the "
            "same order ID be created twice?",
            ClarifyKind.concept_explanation,
        ),
        (
            11,
            "That's what I need to understand, how is it that ad "
            "impotency is a concern here because we are taking unique "
            "order IDs as a trigger for the recipes. So Uh, it's very "
            "hard for me to understand how a unique order ID being used "
            "as a trigger would lead to a situation where idempotency "
            "has to be enforced.",
            ClarifyKind.concept_explanation,
        ),
    ],
)
def test_a13ec188_routes_clarify_kind_correctly(
    turn_index: int,
    candidate_utterance: str,
    expected_kind: ClarifyKind,
) -> None:
    """Each load-bearing turn from session a13ec188 must route to the
    expected clarify_kind under the new Judge prompt.

    Implementation note: this test calls JudgeService directly with the
    same input_summary the original session captured, then asserts the
    Judge picks clarify with the expected clarify_kind. Other actions
    (push_back, advance) are FAIL — those are the misclassifications
    the rewrite is designed to fix.
    """
    inputs_by_index = _load_judge_inputs_by_turn_index()
    judge_input_summary = inputs_by_index.get(turn_index)
    assert judge_input_summary, (
        f"No judge.call event for turn_index={turn_index} in fixture"
    )

    # Reconstruct a JudgeInputPayload-like dict the JudgeService can use.
    # The fixture's input_summary captures the exact fields the Judge
    # saw. Override only the candidate_utterance (it was already in
    # the audit but explicit here for clarity).
    judge_input_summary = {**judge_input_summary,
                           "candidate_utterance": candidate_utterance}

    # NOTE: this test depends on a JudgeService dispatcher that can be
    # called with a raw input_summary dict. If the existing harness
    # uses a Pydantic-typed JudgeInputPayload, adapt the dict to the
    # model first. The exact call is:
    #
    #   from app.modules.interview_engine.judge.service import JudgeService
    #   service = JudgeService(...)
    #   out = asyncio.run(service.call(judge_input_payload))
    #
    # Inspect `app/modules/interview_engine/judge/service.py` to confirm
    # the exact entrypoint signature before implementing this assertion.
    from app.modules.interview_engine.judge.service import JudgeService
    # ... harness setup left as a one-time wiring task for the implementer
    # following the patterns in tests/interview_engine/judge/test_service.py.
    pytest.skip(
        "Wire JudgeService harness using patterns from "
        "tests/interview_engine/judge/test_service.py — this is the only "
        "LLM-in-the-loop test in this plan."
    )

    # Once wired:
    # assert out.next_action == NextAction.clarify
    # assert isinstance(out.next_action_payload, ClarifyPayload)
    # assert out.next_action_payload.clarify_kind == expected_kind
```

- [ ] **Step 2: Run the test (will skip)**

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_replay_a13ec188.py -m prompt_quality -v
```

Expected: 4 tests SKIPPED (the harness wiring is a one-time follow-up; the test scaffold is committed so the next prompt-quality pass picks it up).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/interview_engine/test_replay_a13ec188.py
git commit -m "test(engine): replay scaffold for session a13ec188 clarify_kind routing

Adds parametrized replay test for the four load-bearing turns
(T2 broad_rephrase, T5 use_case_anchor, T8/T12 concept_explanation).
Marked under prompt_quality so it runs by hand, not in the default
suite. Tests skip pending one-time JudgeService harness wiring
(pattern in tests/interview_engine/judge/test_service.py).

Once wired, this test is the integration safety net for any future
Judge prompt change touching the clarify dispatch.

Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md §10.2"
```

---

## Task 12: Manual real-session mock (no commit; user-driven validation)

**Context:** Per spec §10.3, after the 11 code/prompt commits land, the user runs one candidate-mock session and we grep the audit envelope.

- [ ] **Step 1: Bring the engine up**

```bash
cd backend/nexus && docker compose up --build
```

Wait for `[INFO] nexus startup complete`.

- [ ] **Step 2: User runs a mock session via the candidate-session frontend**

User starts the frontend (port 3002, `cd frontend/session && npm run dev`), opens an interview, and talks through the session as a candidate. Aim to reproduce at least one of:
- "Help me understand why X matters" → expect `clarify_kind=concept_explanation` and a failure-scenario reply.
- "Can you give me an example?" (bare) → expect `clarify_kind=use_case_anchor` and a business-setting reply.
- Asking "what's the use case?" → expect `clarify_kind=use_case_anchor`.

- [ ] **Step 3: Grep the audit envelope**

After session close:

```bash
SESSION=$(ls -t backend/nexus/engine-events/*.json | head -1)
echo "Inspecting: $SESSION"

# Verify clarify_kind values present and varied.
python3 -c "
import json, sys
with open('$SESSION') as f: d = json.load(f)
kinds = []
for e in d['events']:
    if e.get('kind') == 'judge.call':
        out = e['payload'].get('output') or {}
        if out.get('next_action') == 'clarify':
            p = out.get('next_action_payload', {})
            kinds.append(p.get('clarify_kind'))
print('clarify_kinds emitted:', kinds)
"

# repeated_opener flag count should be ≤ 1.
bash scripts/grep_naturalness.sh "$(basename "$SESSION" .json)"

# solution_leak_suspected should be 0.
python3 -c "
import json
with open('$SESSION') as f: d = json.load(f)
leaks = [
    e for e in d['events']
    if e.get('kind') == 'speaker.output'
    and e['payload'].get('naturalness_flags', {}).get('solution_leak_suspected')
]
print('solution_leak_suspected count:', len(leaks))
"
```

- [ ] **Step 4: Document the session outcome in PR description**

Capture the audit-grep output and any observed Speaker behavior in the user's working notes / PR description. If solution_leak fires anywhere, that's the post-merge follow-up (tighten PATH E exemplars or the verb list). If repeated_opener fires more than once, dig into `available_openers` payload propagation in that turn's `speaker.input` event.

No commit for this task — it's the live validation gate.

---

## Self-review

**Spec coverage:**

- §1 Issue 1 (Judge misclassification) — covered by Tasks 1 + 8 (schema + Judge prompt).
- §1 Issue 2 (Clarify rephrases instead of bringing context) — covered by Task 9 (PATH B rewrite with domain+volume+contrast exemplars).
- §1 Issue 3 (No concept_explanation path) — covered by Task 9 (PATH E with anti-leak boundary).
- §1 Issue 4 (Opener over-use) — covered by Tasks 3 + 6 + 10 (`openers.py` + input_builder propagation + preamble).
- §1 Issue 5 (Sarvam STT) — explicitly out of scope, named in §12.
- §1 secondary: `is_post_cap_advance` propagation — Task 7.
- §1 secondary: caching — out of scope per spec §12.
- §4 schema changes — Tasks 1 + 2.
- §5 Judge prompt — Task 8.
- §6 Speaker prompt — Task 9.
- §7 opener pruning — Tasks 3 + 6 + 10.
- §8 naturalness extensions — Task 5.
- §9 is_post_cap_advance — Task 7.
- §10 testing strategy — unit tests across Tasks 1, 2, 3, 5, 6, 7; replay scaffold in Task 11; real-session mock in Task 12.
- §11 rollout sequence — matches the 11-task ordering above.

**Placeholder scan:**

- One acknowledged exception: Task 11's replay test ships as `pytest.skip` pending one-time JudgeService harness wiring. The skip is documented inline with the pattern source (`test_service.py`). All other tasks have complete code in every step.
- Task 7 references a `state_engine_factory` fixture and notes the implementer should check the actual fixture name in the existing test file. The same task points at `test_inverse_quality_gate.py` as the construction-pattern reference. This is a deliberate adapt-to-existing-codebase instruction, not a placeholder.

**Type consistency:**

- `ClarifyKind` introduced in Task 1 is used identically in Tasks 2, 5, 6, 8, 9, 11. Enum values: `term_definition`, `concept_explanation`, `use_case_anchor`, `broad_rephrase`, `probe_context` — same in all references.
- `ClarifyPayload.clarify_kind` field is required (no default) in Task 1, and `SpeakerInput.clarify_kind` is `Optional` (default None) in Task 2 — consistent with the design (Judge always emits the kind; Speaker carries None when instruction_kind is not clarify).
- `available_openers` is `list[str]` everywhere (Tasks 2, 3, 6).
- `_SOFT_TARGETS` becomes `dict[tuple[str, str | None], int]` in Task 5; the lookup function signature `detect_exceeded_soft_target(output, instruction_kind, clarify_kind=None)` consistent with the dict shape.
- `detect_solution_leak(output, clarify_kind)` — `clarify_kind` accepted as `str | None`; gracefully returns False on non-PATH-E kinds.

No issues found.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-18-speaker-intent-layer.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
