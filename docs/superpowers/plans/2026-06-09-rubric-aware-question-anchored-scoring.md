# Rubric-Aware, Question-Anchored Report Scoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the post-session report grade each interview question against its *own* full bank card (rubric + Listen-for + Red-flags + difficulty + probe-dependence), roll those question-grades up to signals, and stamp template provenance — so 100s of candidates on one JD are graded by the same standardized template.

**Architecture:** Replace the per-*signal* LLM re-check (`scoring/recheck.py`) with a per-*question* grader (`scoring/question_grade.py`) plus a question→signal roll-up (`scoring/rollup.py`). The signal stays the unit of the verdict; the question becomes the unit of grading. Downstream aggregation (dimensions, ceilings, verdict, holistic, communication in `scoring/aggregate.py`) is unchanged. A small Step-0 change populates `ScoringManifest` provenance fields.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, OpenAI Responses API (`responses.parse` with `text_format`), pytest. Frontend: Next.js / React / Vitest (`frontend/app`).

**Spec:** `docs/superpowers/specs/2026-06-09-rubric-aware-question-anchored-scoring-design.md`

**Run backend tests with:** `docker compose exec nexus python -m pytest <path> -q` (or `docker compose run --rm nexus pytest <path> -q` if no container is up). All paths below are relative to `backend/nexus/`.

---

## Task 1: Step 0 — Manifest provenance (bank_id / signal_snapshot_id / scorer_code_version)

**Files:**
- Modify: `app/modules/reporting/service.py` (`build_report` signature + `ScoringManifest` construction; add `SCORER_CODE_VERSION`)
- Modify: `app/modules/reporting/actors.py:253-258` (pass the two ids)
- Test: `tests/reporting/test_build_report.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/reporting/test_build_report.py` (reuse whatever evidence/questions/signal_metadata builder the existing tests in this file use — match their fixture style):

```python
async def test_manifest_carries_template_provenance(make_evidence, make_questions, make_signal_metadata):
    report = await build_report(
        evidence=make_evidence(),
        questions=make_questions(),
        signal_metadata=make_signal_metadata(),
        correlation_id="cid-prov",
        bank_id="bank-123",
        signal_snapshot_id="snap-456",
    )
    m = report.scoring_manifest
    assert m.bank_id == "bank-123"
    assert m.signal_snapshot_id == "snap-456"
    assert m.scorer_code_version is not None and m.scorer_code_version != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/test_build_report.py::test_manifest_carries_template_provenance -q`
Expected: FAIL — `build_report() got an unexpected keyword argument 'bank_id'`.

- [ ] **Step 3: Add the module constant**

Near the top of `app/modules/reporting/service.py`, after `_COMM_POINTS`:

```python
# Bump when the scoring algorithm changes in a way that affects scores, so a
# report's manifest records which scorer produced it (cross-candidate audit).
SCORER_CODE_VERSION = "qa-1"  # question-anchored, gen-1
```

- [ ] **Step 4: Thread the ids through `build_report`**

Change the signature:

```python
async def build_report(*, evidence, questions, signal_metadata, correlation_id,
                       bank_id=None, signal_snapshot_id=None, n_samples=None):
```

In the `ScoringManifest(...)` construction at the end of `build_report`, add the three fields:

```python
        scoring_manifest=ScoringManifest(
            scorer_model=ai_config.report_scorer_model,
            prompt_version=ai_config.report_scorer_prompt_version,
            scorer_code_version=SCORER_CODE_VERSION,
            bank_id=bank_id,
            signal_snapshot_id=signal_snapshot_id,
            generated_at=datetime.now(UTC).isoformat(), correlation_id=correlation_id,
            evidence_grounding_summary={
                # ...unchanged...
            }),
```

- [ ] **Step 5: Pass the ids from the actor**

In `app/modules/reporting/actors.py`, the `build_report(...)` call (around line 253):

```python
            report = await build_report(
                evidence=evidence,
                questions=questions,
                signal_metadata=signal_metadata,
                correlation_id=correlation_id,
                bank_id=str(bank.id),
                signal_snapshot_id=str(snapshot.id),
            )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose exec nexus python -m pytest tests/reporting/test_build_report.py::test_manifest_carries_template_provenance -q`
Expected: PASS.

- [ ] **Step 7: Run the full reporting suite (no regressions)**

Run: `docker compose exec nexus python -m pytest tests/reporting -q`
Expected: PASS (existing tests unaffected; the new kwargs default to None).

- [ ] **Step 8: Commit**

```bash
git add app/modules/reporting/service.py app/modules/reporting/actors.py tests/reporting/test_build_report.py
git commit -m "feat(reporting): stamp bank_id/snapshot_id/scorer_code_version on report manifest"
```

---

## Task 2: Schemas — add `QuestionGradeOut` + new card fields

**Files:**
- Modify: `app/modules/reporting/schemas.py`
- Test: `tests/reporting/test_schemas.py`

Note: `SignalRecheckOut` is NOT removed here (recheck.py still imports it) — it is removed in Task 7 together with `recheck.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/reporting/test_schemas.py`:

```python
from app.modules.reporting.schemas import QuestionGradeOut, QuestionOut, SignalAssessmentOut


def test_question_grade_out_defaults():
    g = QuestionGradeOut(level="solid")
    assert g.listen_for_hits == [] and g.red_flags_tripped == []
    assert g.evidence_quotes == [] and g.needs_verification is False
    assert g.overridden is False and g.override_reason is None


def test_question_out_new_card_fields_default():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="…", candidate_quote="…")
    assert q.level == "not_reached" and q.difficulty is None
    assert q.listen_for_hits == [] and q.red_flags_tripped == []
    assert q.probes_used == 0 and q.probes_available == 0


def test_signal_assessment_cross_credit_fields_default():
    s = SignalAssessmentOut(signal="s", type="competency", weight=2, knockout=False,
                            priority="preferred", provenance="asked_directly", level="solid")
    assert s.cross_credit_applied is False and s.level_basis == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/test_schemas.py -k "question_grade_out or new_card_fields or cross_credit" -q`
Expected: FAIL — `ImportError: cannot import name 'QuestionGradeOut'`.

- [ ] **Step 3: Add `QuestionGradeOut`**

In `app/modules/reporting/schemas.py`, after `SignalRecheckOut`:

```python
class QuestionGradeOut(BaseModel):
    """Structured output from the per-QUESTION post-interview grade (Layer 2).
    The question is graded against its OWN full bank card (rubric + listen-for
    + red-flags + evaluation_hint), difficulty-calibrated and probe-aware."""
    level: Literal["strong", "solid", "thin", "absent"]
    listen_for_hits: list[str] = Field(default_factory=list)
    red_flags_tripped: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    needs_verification: bool = False
    verification_note: str | None = None
    overridden: bool = False
    override_reason: str | None = None
```

- [ ] **Step 4: Extend `QuestionOut`**

Add fields to `QuestionOut` (after `our_read`):

```python
    level: str = "not_reached"            # per-question grade: strong|solid|thin|absent|not_reached
    difficulty: str | None = None         # easy|medium|hard (bank)
    listen_for_hits: list[str] = Field(default_factory=list)
    red_flags_tripped: list[str] = Field(default_factory=list)
    probes_used: int = 0
    probes_available: int = 0
```

- [ ] **Step 5: Extend `SignalAssessmentOut`**

Add fields to `SignalAssessmentOut` (after `override_reason`):

```python
    cross_credit_applied: bool = False
    level_basis: str = ""                 # e.g. "dedicated: thin; +1 cross-credit → solid"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose exec nexus python -m pytest tests/reporting/test_schemas.py -k "question_grade_out or new_card_fields or cross_credit" -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting/schemas.py tests/reporting/test_schemas.py
git commit -m "feat(reporting): QuestionGradeOut + per-question card + cross-credit basis schema fields"
```

---

## Task 3: Deterministic per-question base level (`question_base_level`)

**Files:**
- Create: `app/modules/reporting/scoring/question_grade.py`
- Test: `tests/reporting/scoring/test_question_grade.py`

This is the pure, auditable base the LLM later refines. It rolls a single question's *own* elicited notes into a base level — no provenance, no closure (those live at the signal level).

- [ ] **Step 1: Write the failing test**

Create `tests/reporting/scoring/test_question_grade.py`:

```python
from app.modules.interview_runtime.evidence import (
    EvidenceNote, EvidenceStance, EvidenceTexture, TimeSpan,
)
from app.modules.reporting.scoring.question_grade import question_base_level


def _note(seq, stance, texture, retracts=None):
    return EvidenceNote(
        seq=seq, turn_ref=f"t{seq}", signal="s", stance=stance, texture=texture,
        quote="x", span=TimeSpan(start_ms=0, end_ms=1),
        from_question_id="q1", via_probe=False, retracts_seq=retracts,
    )


def test_base_level_best_supporting_texture():
    notes = [_note(1, EvidenceStance.supports, EvidenceTexture.thin),
             _note(2, EvidenceStance.supports, EvidenceTexture.strong)]
    assert question_base_level(notes) == "strong"


def test_base_level_concrete_maps_solid():
    notes = [_note(1, EvidenceStance.supports, EvidenceTexture.concrete)]
    assert question_base_level(notes) == "solid"


def test_base_level_unretracted_contradiction_is_absent():
    notes = [_note(1, EvidenceStance.contradicts, EvidenceTexture.concrete)]
    assert question_base_level(notes) == "absent"


def test_base_level_no_notes_is_not_reached():
    assert question_base_level([]) == "not_reached"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_question_grade.py -q`
Expected: FAIL — module `question_grade` does not exist.

- [ ] **Step 3: Implement `question_base_level`**

Create `app/modules/reporting/scoring/question_grade.py`:

```python
"""Layer 2 — per-QUESTION grade. A deterministic base level over the question's
own elicited notes, refined by an LLM graded against the question's full bank
card (rubric + listen-for + red-flags + evaluation_hint), difficulty-calibrated
and probe-aware. Replaces the per-signal recheck."""
from __future__ import annotations

import hashlib
import json

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.interview_runtime.evidence import (
    EvidenceNote, EvidenceStance, EvidenceTexture,
)
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.schemas import QuestionGradeOut
from app.modules.reporting.scoring.types import DemonstrationLevel

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

_TEXTURE_RANK = {EvidenceTexture.thin: 0, EvidenceTexture.concrete: 1, EvidenceTexture.strong: 2}
_RANK_LEVEL = {2: "strong", 1: "solid", 0: "thin"}


def question_base_level(notes: list[EvidenceNote]) -> DemonstrationLevel:
    """Deterministic base for ONE question from the notes IT elicited.
    Supporting notes → best texture (strong>concrete>thin). No supports:
    an un-retracted contradiction → absent; else not_reached."""
    supports = [n for n in notes if n.stance == EvidenceStance.supports]
    if supports:
        best = max(_TEXTURE_RANK[n.texture] for n in supports)
        return _RANK_LEVEL[best]  # type: ignore[return-value]
    if any(n.stance == EvidenceStance.contradicts and n.retracts_seq is None for n in notes):
        return "absent"
    return "not_reached"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_question_grade.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/question_grade.py tests/reporting/scoring/test_question_grade.py
git commit -m "feat(reporting): deterministic per-question base level"
```

---

## Task 4: Per-question LLM grader + prompt

**Files:**
- Create: `prompts/v4/report_scorer/question_grade.txt`
- Modify: `app/modules/reporting/scoring/question_grade.py` (add `grade_question`)
- Test: `tests/reporting/scoring/test_question_grade.py` (refusal path, mocked); `tests/reporting/prompt_evals/test_question_grade_evals.py` (live, `@prompt_quality`)

- [ ] **Step 1: Write the prompt file**

Create `prompts/v4/report_scorer/question_grade.txt` (adapted from the retired `signal_recheck.txt`, now question-anchored and consuming listen-for/red-flags/difficulty/probes):

```
You are grading ONE question from a structured screening interview, so that
hundreds of candidates for the same role are judged by the SAME standard.

You are given:
- the question text and its question_kind (experience_check, compliance_binary,
  technical_scenario, behavioral),
- its difficulty (easy/medium/hard) — the rubric is already calibrated to it,
- its RUBRIC with three anchors: excellent, meets_bar, below_bar,
- its LISTEN-FOR points (what a strong answer contains),
- its RED FLAGS (what a weak/▸absent answer sounds like),
- how many follow-up probes were available and how many were actually fired,
- the engine's append-only NOTES for THIS question — each a verbatim candidate
  quote tagged stance (supports/contradicts) and texture (thin/concrete/strong),
- the engine's deterministic base level.

Grade the answer against the rubric anchors and map to a level:
- strong  — reaches `excellent`: a real, owned example with the depth the anchor asks for.
- solid   — reaches `meets_bar`: a real, specific thing they did, with the required facts.
- thin    — only `below_bar`: generic, buzzwords, a hypothetical "I would…", or fewer facts
            than `meets_bar` requires.
- absent  — the notes show a disclaim or no genuine evidence.

Rules:
- THE RUBRIC IS THE BAR and you grade the FULL range. Do NOT anchor on the engine base;
  it is only a hint. Use `strong` whenever the answer truly hits `excellent`. Set
  overridden=true with a one-line override_reason whenever your level differs from the base.
- LISTEN-FOR → listen_for_hits: list only the points the candidate GENUINELY covered, each
  grounded in their words. RED FLAGS → red_flags_tripped: list only the ones that genuinely
  fired. Do not invent either.
- PROBE-DEPENDENCE: if the answer only reached concrete/strong AFTER most or all available
  probes were fired (probes_used near probes_available), cap the level ONE step down
  (concrete-after-all-probes → solid, not strong). Volunteered depth beats extracted depth.
  This is a cap, never an extra penalty.
- FACTUAL GATES (experience_check, compliance_binary) are answered by a brief precise FACT,
  not an essay — never penalise brevity. If every required fact is present → solid (strong if
  `excellent` asks for more and it is present). If SOME required facts are missing → grade the
  level it actually reaches (usually thin) AND set needs_verification=true with a one-line
  verification_note naming the missing facts. You never reject — an unmet must-have is held for
  human review downstream.
- A confident tone is not depth. A thin-but-confident answer stays thin.
- Honour an honest correction: a later contradiction walking back an earlier claim is not a strength.
- Quote ONLY the candidate's actual words in evidence_quotes; never invent a quote. Reveal
  nothing about scoring or the rubric in any field a candidate could see.
```

- [ ] **Step 2: Write the failing (refusal-path) unit test**

Add to `tests/reporting/scoring/test_question_grade.py`:

```python
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.asyncio
async def test_grade_question_refusal_keeps_base_level():
    from app.modules.reporting.scoring.question_grade import grade_question
    q = {"id": "q1", "text": "Tell me about an Intune change.",
         "rubric": {"excellent": "…", "meets_bar": "…", "below_bar": "…"},
         "positive_evidence": ["names the artifact"], "red_flags": ["only 'we'"],
         "evaluation_hint": "h", "question_kind": "behavioral", "difficulty": "medium"}
    fake = AsyncMock()
    fake.responses.parse = AsyncMock(return_value=type("R", (), {"output_parsed": None})())
    with patch("app.modules.reporting.scoring.question_grade.get_raw_openai_client",
               return_value=fake):
        out = await grade_question(question=q, notes=[], probes_used=0, probes_available=3,
                                   base_level="thin", correlation_id="cid")
    assert out.level == "thin"          # falls back to the engine base on refusal
    assert out.overridden is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_question_grade.py::test_grade_question_refusal_keeps_base_level -q`
Expected: FAIL — `grade_question` not defined.

- [ ] **Step 4: Implement `grade_question`**

Append to `app/modules/reporting/scoring/question_grade.py`:

```python
def _render_notes(notes: list[EvidenceNote]) -> str:
    lines = [
        f"[note {n.seq} · {n.stance.value}/{n.texture.value}"
        f"{' · via probe' if n.via_probe else ''}] {n.quote}"
        for n in notes
    ]
    return "\n".join(lines) if lines else "(no notes for this question)"


async def grade_question(
    *, question: dict, notes: list[EvidenceNote], probes_used: int,
    probes_available: int, base_level: DemonstrationLevel, correlation_id: str,
) -> QuestionGradeOut:
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/question_grade"
    )
    card = {
        "text": question.get("text", ""),
        "rubric": question.get("rubric", {}),
        "positive_evidence": question.get("positive_evidence", []),
        "red_flags": question.get("red_flags", []),
        "evaluation_hint": question.get("evaluation_hint", ""),
    }
    prefix = (
        f"{system_prompt}\n\n"
        f"<question_kind>\n{question.get('question_kind') or 'unknown'}\n</question_kind>\n\n"
        f"<difficulty>\n{question.get('difficulty') or 'unknown'}\n</difficulty>\n\n"
        f"<probes>\nused={probes_used} of available={probes_available}\n</probes>\n\n"
        f"<card>\n{json.dumps(card, ensure_ascii=False)}\n</card>\n\n"
        f"<engine_base>\nlevel={base_level}\n</engine_base>"
    )
    notes_block = _render_notes(notes)
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<notes>\n{notes_block}\n</notes>"},
    ]
    qid_hash = hashlib.sha256(str(question.get("id", "")).encode("utf-8")).hexdigest()[:12]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": QuestionGradeOut,
        "prompt_cache_key": (
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:qg1:"
            f"{ai_config.report_scorer_prompt_version}:{qid_hash}:{ai_config.report_scorer_model}"
        ),
    }
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(prompt_name="report_question_grade",
                                prompt_version=ai_config.report_scorer_prompt_version,
                                correlation_id=correlation_id)
        response = await get_raw_openai_client().responses.parse(**kwargs)

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.question_grade.refusal", question_id=question.get("id"),
                    correlation_id=correlation_id)
        return QuestionGradeOut(level=base_level if base_level != "not_reached" else "thin")

    grounded, _ = ground_quotes(parsed.evidence_quotes, notes_block)
    return parsed.model_copy(update={"evidence_quotes": grounded})
```

Note: if `ai_config.report_scorer_prompt_cache_key_prefix` does not exist, reuse the exact attribute name `recheck.py` used for its cache key prefix — grep `recheck.py` to confirm and mirror it.

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_question_grade.py::test_grade_question_refusal_keeps_base_level -q`
Expected: PASS.

- [ ] **Step 6: Write the live prompt eval (opt-in)**

Create `tests/reporting/prompt_evals/__init__.py` (empty) and `tests/reporting/prompt_evals/test_question_grade_evals.py`:

```python
import pytest
from app.modules.interview_runtime.evidence import (
    EvidenceNote, EvidenceStance, EvidenceTexture, TimeSpan,
)
from app.modules.reporting.scoring.question_grade import grade_question

pytestmark = pytest.mark.prompt_quality


def _n(seq, texture, quote):
    return EvidenceNote(seq=seq, turn_ref=f"t{seq}", signal="s",
                        stance=EvidenceStance.supports, texture=texture, quote=quote,
                        span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1",
                        via_probe=False, retracts_seq=None)


@pytest.mark.asyncio
async def test_thin_dedicated_answer_grades_thin_and_trips_red_flag():
    """An iOS Wi-Fi troubleshooting answer that names no tools should grade thin
    and trip the 'does not know Wi-Fi/cert handling' red flag."""
    q = {"id": "q1", "text": "An iOS update breaks Wi-Fi on managed iPhones. Diagnose with Intune.",
         "rubric": {"excellent": "Names APNs/cert profile checks, isolates by supervision, targeted rollback.",
                    "meets_bar": "Basic profile checks and a reasonable rollback plan.",
                    "below_bar": "Vague blame, suggests wipe, no Intune/iOS specifics."},
         "positive_evidence": ["Checks Wi-Fi config and certificate profiles (SCEP/PKCS)"],
         "red_flags": ["Does not know Wi-Fi or certificate profile handling on iOS/Intune"],
         "evaluation_hint": "Listen for cert/profile specifics.",
         "question_kind": "technical_scenario", "difficulty": "medium"}
    notes = [_n(1, EvidenceTexture.thin, "We can check the Wi Fi policy."),
             _n(2, EvidenceTexture.thin, "And look into it.")]
    out = await grade_question(question=q, notes=notes, probes_used=3, probes_available=3,
                               base_level="thin", correlation_id="cid-eval")
    assert out.level in ("thin", "absent")
    assert any("Wi" in r or "cert" in r.lower() for r in out.red_flags_tripped)


@pytest.mark.asyncio
async def test_probe_dependence_caps_one_tier():
    """An answer that only becomes concrete AFTER all probes are fired should be
    capped one tier below what the same content would earn if volunteered."""
    q = {"id": "q1", "text": "Tell me about an Intune change you executed.",
         "rubric": {"excellent": "Names the exact object changed, personal build/test/target/validate steps, change control, documentation, rollback/outcome.",
                    "meets_bar": "A concrete Intune change with some personal involvement and basic documentation.",
                    "below_bar": "Team-only narrative, no Intune specifics, no documentation."},
         "positive_evidence": ["Names a specific Intune artifact changed",
                               "Describes personal build/test/target/validate steps",
                               "Documentation produced (KB/runbook/change record)"],
         "red_flags": ["Speaks only in 'we' with no personal actions"],
         "evaluation_hint": "Listen for a crisp STAR story with personal ownership.",
         "question_kind": "behavioral", "difficulty": "medium"}
    # Concrete detail, but every detail came only after the 3 probes fired (via_probe=True).
    notes = [
        EvidenceNote(seq=1, turn_ref="t1", signal="s", stance=EvidenceStance.supports,
                     texture=EvidenceTexture.strong,
                     quote="I changed the iOS compliance policy, tested the rollback first, "
                           "documented it in BMC Remedy with validation screenshots.",
                     span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1",
                     via_probe=True, retracts_seq=None),
    ]
    out = await grade_question(question=q, notes=notes, probes_used=3, probes_available=3,
                               base_level="strong", correlation_id="cid-probe")
    assert out.level == "solid"   # capped one tier from strong because depth was fully probe-extracted
```

- [ ] **Step 7: Run the live eval (requires OPENAI_API_KEY)**

Run: `docker compose exec nexus python -m pytest tests/reporting/prompt_evals/test_question_grade_evals.py -m prompt_quality -q`
Expected: PASS (real OpenAI call). If it flakes on wording, tune `question_grade.txt`, not the test's intent.

- [ ] **Step 8: Commit**

```bash
git add prompts/v4/report_scorer/question_grade.txt app/modules/reporting/scoring/question_grade.py tests/reporting/scoring/test_question_grade.py tests/reporting/prompt_evals/
git commit -m "feat(reporting): per-question LLM grader consuming the full bank card"
```

---

## Task 5: Question→signal roll-up (`rollup.py`) — the crux

**Files:**
- Create: `app/modules/reporting/scoring/rollup.py`
- Test: `tests/reporting/scoring/test_rollup.py`

Interface:

```python
@dataclass(frozen=True)
class SignalRollup:
    level: DemonstrationLevel      # strong|solid|thin|absent|not_reached
    cross_credit_applied: bool
    level_basis: str

def roll_up_signal(*, signal, dedicated_level, dedicated_outcome, cross_credit_level) -> SignalRollup
```

- `dedicated_level`: the graded level of the signal's chosen dedicated question (after Task-4 grade), or `None` if no dedicated question was asked.
- `dedicated_outcome`: `"asked"` | `"not_reached"` | `None` (no dedicated question exists).
- `cross_credit_level`: best level reached by this signal's notes elicited by OTHER questions (`strong|solid|thin|absent|None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/reporting/scoring/test_rollup.py`:

```python
from app.modules.reporting.scoring.rollup import roll_up_signal, pick_dedicated_question


def test_dedicated_thin_plus_strong_cross_credit_lifts_one_to_solid():
    r = roll_up_signal(signal="s", dedicated_level="thin", dedicated_outcome="asked",
                       cross_credit_level="strong")
    assert r.level == "solid"          # +1 only, never thin→strong
    assert r.cross_credit_applied is True
    assert "thin" in r.level_basis and "solid" in r.level_basis


def test_dedicated_thin_no_cross_credit_stays_thin():
    r = roll_up_signal(signal="s", dedicated_level="thin", dedicated_outcome="asked",
                       cross_credit_level=None)
    assert r.level == "thin" and r.cross_credit_applied is False


def test_dedicated_absent_disclaim_not_lifted():
    r = roll_up_signal(signal="s", dedicated_level="absent", dedicated_outcome="asked",
                       cross_credit_level="strong")
    assert r.level == "absent" and r.cross_credit_applied is False


def test_dedicated_not_reached_cross_credit_authoritative():
    r = roll_up_signal(signal="s", dedicated_level=None, dedicated_outcome="not_reached",
                       cross_credit_level="solid")
    assert r.level == "solid" and r.cross_credit_applied is True


def test_no_dedicated_and_no_cross_credit_is_not_reached():
    r = roll_up_signal(signal="s", dedicated_level=None, dedicated_outcome=None,
                       cross_credit_level=None)
    assert r.level == "not_reached"


def test_solid_dedicated_plus_strong_cross_credit_lifts_to_strong():
    r = roll_up_signal(signal="s", dedicated_level="solid", dedicated_outcome="asked",
                       cross_credit_level="strong")
    assert r.level == "strong"


def test_pick_dedicated_prefers_asked_then_lowest_position():
    questions = [
        {"id": "qA", "primary_signal": "s", "position": 7},
        {"id": "qB", "primary_signal": "s", "position": 0},
    ]
    outcomes = {"qA": "not_reached", "qB": "asked"}
    assert pick_dedicated_question("s", questions, outcomes)["id"] == "qB"


def test_pick_dedicated_two_asked_lowest_position_wins():
    questions = [
        {"id": "qA", "primary_signal": "s", "position": 3},
        {"id": "qB", "primary_signal": "s", "position": 1},
    ]
    outcomes = {"qA": "asked", "qB": "asked"}
    assert pick_dedicated_question("s", questions, outcomes)["id"] == "qB"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_rollup.py -q`
Expected: FAIL — module `rollup` does not exist.

- [ ] **Step 3: Implement `rollup.py`**

Create `app/modules/reporting/scoring/rollup.py`:

```python
"""Question→signal roll-up. The dedicated question (primary_signal match) anchors
a signal's level; cross-credit from other questions can lift it by at most ONE
tier; when the dedicated question was never reached, cross-credit is authoritative.
Pure — no IO/LLM. The signal stays the unit of the verdict (downstream aggregate)."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.scoring.types import DemonstrationLevel

# Level ladder (low→high). not_reached is OFF-ladder (no data) and handled separately.
_LADDER: list[str] = ["absent", "thin", "solid", "strong"]


def _rank(level: str | None) -> int | None:
    return _LADDER.index(level) if level in _LADDER else None


@dataclass(frozen=True)
class SignalRollup:
    level: DemonstrationLevel
    cross_credit_applied: bool
    level_basis: str


def pick_dedicated_question(signal, questions, outcomes):
    """The signal's dedicated question: primary_signal == signal, preferring
    asked over not_reached, then lowest position. Returns the question dict or None."""
    owned = [q for q in questions if q.get("primary_signal") == signal]
    if not owned:
        return None
    def key(q):
        asked = outcomes.get(q["id"]) == "asked"
        return (0 if asked else 1, q.get("position", 1_000_000))
    return sorted(owned, key=key)[0]


def roll_up_signal(
    *, signal: str, dedicated_level: DemonstrationLevel | None,
    dedicated_outcome: str | None, cross_credit_level: str | None,
) -> SignalRollup:
    # No dedicated question asked → cross-credit is authoritative (charitable).
    if dedicated_outcome != "asked" or dedicated_level is None:
        if cross_credit_level and _rank(cross_credit_level) is not None:
            return SignalRollup(level=cross_credit_level,  # type: ignore[arg-type]
                                cross_credit_applied=True,
                                level_basis=f"no dedicated question asked; cross-credit → {cross_credit_level}")
        return SignalRollup(level="not_reached", cross_credit_applied=False,
                            level_basis="never asked; no cross-credit")

    base = dedicated_level
    base_rank = _rank(base)
    # A genuine disclaim (absent) is never lifted by an incidental mention elsewhere.
    if base == "absent":
        return SignalRollup(level="absent", cross_credit_applied=False,
                            level_basis="dedicated: absent (disclaim) — not lifted")

    cc_rank = _rank(cross_credit_level)
    if cc_rank is not None and base_rank is not None and cc_rank > base_rank:
        lifted = _LADDER[base_rank + 1]
        return SignalRollup(level=lifted,  # type: ignore[arg-type]
                            cross_credit_applied=True,
                            level_basis=f"dedicated: {base}; +1 cross-credit → {lifted}")
    return SignalRollup(level=base, cross_credit_applied=False,
                        level_basis=f"dedicated: {base}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_rollup.py -q`
Expected: PASS (all 8).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/rollup.py tests/reporting/scoring/test_rollup.py
git commit -m "feat(reporting): question→signal roll-up (anchor + ≤1-tier cross-credit + tie-break)"
```

---

## Task 6: EvidenceView — notes grouped by question

**Files:**
- Modify: `app/modules/reporting/scoring/evidence_adapter.py`
- Test: `tests/reporting/scoring/test_evidence_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/reporting/scoring/test_evidence_adapter.py` (reuse the file's existing `SessionEvidence` builder; if none, build a minimal one inline as the other tests do):

```python
def test_notes_by_question_groups_on_from_question_id(make_evidence_with_notes):
    # two notes from q1, one from q2 (helper builds EvidenceNotes accordingly)
    view = make_evidence_with_notes()
    by_q = view.notes_by_question
    assert set(by_q.keys()) == {"q1", "q2"}
    assert len(by_q["q1"]) == 2 and len(by_q["q2"]) == 1
```

If `make_evidence_with_notes` doesn't exist, write the EvidenceView + notes inline (mirror `test_evidence_adapter.py`'s existing construction of `SessionEvidence`).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_evidence_adapter.py -k notes_by_question -q`
Expected: FAIL — `EvidenceView` has no attribute `notes_by_question`.

- [ ] **Step 3: Implement the property**

Add to `EvidenceView` in `app/modules/reporting/scoring/evidence_adapter.py`:

```python
    @property
    def notes_by_question(self) -> dict[str, list[EvidenceNote]]:
        """Notes grouped by the question that elicited them (from_question_id)."""
        out: dict[str, list[EvidenceNote]] = {}
        for n in self._ev.notes:
            out.setdefault(n.from_question_id, []).append(n)
        return out

    @property
    def outcome_by_question(self) -> dict[str, str]:
        """question_id → outcome ('asked' | 'not_reached')."""
        return {
            q.question_id: (q.outcome.value if hasattr(q.outcome, "value") else q.outcome)
            for q in self._ev.questions
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec nexus python -m pytest tests/reporting/scoring/test_evidence_adapter.py -k notes_by_question -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/evidence_adapter.py tests/reporting/scoring/test_evidence_adapter.py
git commit -m "feat(reporting): EvidenceView.notes_by_question + outcome_by_question"
```

---

## Task 7: Rewire `build_report` — question-grade → roll-up; retire recheck

**Files:**
- Modify: `app/modules/reporting/service.py` (`build_report` body)
- Remove: `app/modules/reporting/scoring/recheck.py`, `prompts/v4/report_scorer/signal_recheck.txt`, `tests/reporting/scoring/test_recheck.py`
- Remove: `SignalRecheckOut` from `schemas.py`; `level_for_signal` + `tests/reporting/scoring/test_level_for_signal.py` (superseded by `question_base_level`)
- Test: `tests/reporting/test_build_report.py`

This is the integration task — the per-signal recheck is replaced by: grade each asked question (Task 4) → roll up to signals (Task 5) → build the per-question cards with the new fields.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/reporting/test_build_report.py` (use the file's evidence/questions builders; ensure the builder has at least one asked question with notes whose `from_question_id` matches a question `id`, and a signal shared across two questions):

```python
async def test_per_question_cards_carry_listen_for_and_red_flags(make_evidence, make_questions, make_signal_metadata):
    report = await build_report(
        evidence=make_evidence(), questions=make_questions(),
        signal_metadata=make_signal_metadata(), correlation_id="cid-q")
    asked = [q for q in report.questions if q.status_badge != "not_attempted"]
    assert asked, "expected at least one asked question card"
    # new card fields are present and typed
    for q in asked:
        assert isinstance(q.listen_for_hits, list)
        assert isinstance(q.red_flags_tripped, list)
        assert q.level in ("strong", "solid", "thin", "absent", "not_reached")
        assert q.probes_available >= 0
    # signal cards explain their level
    assert all(isinstance(s.level_basis, str) for s in report.signal_assessments)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec nexus python -m pytest tests/reporting/test_build_report.py::test_per_question_cards_carry_listen_for_and_red_flags -q`
Expected: FAIL (cards lack the new fields / they're empty defaults because nothing populates them yet).

- [ ] **Step 3: Rewire the grading section of `build_report`**

In `app/modules/reporting/service.py`, replace the imports of `recheck_signal` and `level_for_signal` and the Layer-1/Layer-2 blocks (the `base_level` loop, `recheck_targets`, `_one`, `recheck_results`, `final_level`) with question-anchored grading. Key changes:

1. Imports: drop `from app.modules.reporting.scoring.recheck import recheck_signal` and `level_for_signal` from the aggregate import; add:
```python
from app.modules.reporting.scoring.question_grade import grade_question, question_base_level
from app.modules.reporting.scoring.rollup import pick_dedicated_question, roll_up_signal
```

2. Replace the base-level + recheck section with:
```python
    notes_by_question = view.notes_by_question
    outcome_by_question = view.outcome_by_question
    probes_by_q = {q.question_id: (len(q.probes_used), q.probes_available) for q in evidence.questions}

    # --- Layer 2: grade every ASKED question against its OWN full card ---------
    async def _grade(q: dict):
        qid = q["id"]
        qnotes = notes_by_question.get(qid, [])
        used, avail = probes_by_q.get(qid, (0, 0))
        base = question_base_level(qnotes)
        return qid, await grade_question(
            question=q, notes=qnotes, probes_used=used, probes_available=avail,
            base_level=base, correlation_id=correlation_id)
    asked_qids = [q["id"] for q in questions if outcome_by_question.get(q["id"]) == "asked"]
    grades = dict(await asyncio.gather(*[_grade(q) for q in questions if q["id"] in asked_qids])) \
        if asked_qids else {}

    # --- Roll question grades up to each PRIMARY signal -----------------------
    def _cross_credit_level(sig: str) -> str | None:
        """Best level from notes for `sig` elicited by a question that is NOT its dedicated one."""
        ded = pick_dedicated_question(sig, questions, outcome_by_question)
        ded_id = ded["id"] if ded else None
        other = [n for n in notes_by_signal.get(sig, [])
                 if n.from_question_id != ded_id and n.stance == EvidenceStance.supports]
        if not other:
            return None
        return question_base_level(other)  # texture roll-up over the cross-credit notes

    rollups: dict[str, object] = {}
    final_level: dict[str, str] = {}
    for sig in primary_set:
        ded = pick_dedicated_question(sig, questions, outcome_by_question)
        ded_id = ded["id"] if ded else None
        ded_outcome = outcome_by_question.get(ded_id) if ded_id else None
        ded_level = grades[ded_id].level if (ded_id in grades) else None
        r = roll_up_signal(signal=sig, dedicated_level=ded_level, dedicated_outcome=ded_outcome,
                           cross_credit_level=_cross_credit_level(sig))
        rollups[sig] = r
        final_level[sig] = r.level
```

3. The `scored` list, dimensions, overall, ceilings, verdict, holistic, communication stay exactly as they are — they read `final_level[sig]`.

4. `signal_assessments`: replace the `overridden`/`override_reason` derivation (which referenced `recheck_results`) with the rollup + per-dedicated-question grade. For each scored signal `s`:
```python
    def _ded_grade(sig):
        ded = pick_dedicated_question(sig, questions, outcome_by_question)
        return grades.get(ded["id"]) if ded else None

    signal_assessments = [SignalAssessmentOut(
        signal=s.value, type=s.type, weight=s.weight, knockout=s.knockout, priority=s.priority,
        provenance=_provenance_str(s.value), level=s.level, score=s.score,
        evidence=evidence_by_sig[s.value],
        overridden=bool(_ded_grade(s.value) and _ded_grade(s.value).overridden),
        override_reason=(_ded_grade(s.value).override_reason if _ded_grade(s.value) else None),
        cross_credit_applied=rollups[s.value].cross_credit_applied,
        level_basis=rollups[s.value].level_basis,
    ) for s in scored]
```

5. `evidence_by_sig` / `_scorecard_evidence`: change it to prefer the dedicated question grade's `evidence_quotes`, else engine supporting notes. Update `_scorecard_evidence(sig, grades_by_dedicated, notes_by_signal)` accordingly (replace the old `recheck_results` arg with a `{sig: _ded_grade(sig)}` map, or inline).

6. `human_verify`: replace the `recheck_results` loop with the dedicated grades:
```python
    human_verify = [
        {"signal": sig, "note": g.verification_note}
        for sig in primary_set
        if (g := _ded_grade(sig)) and g.needs_verification and g.verification_note
    ]
```

7. **Per-question cards** (`q_out` loop): populate the new fields from `grades`. For each `qr` in `evidence.questions`, with `g = grades.get(qr.question_id)` and `qdict = q_text_by_id.get(qr.question_id, {})`:
```python
        q_out.append(QuestionOut(
            seq=i + 1, question_id=qr.question_id, title=text[:60],
            status_badge=badge, status_tone=tone, question_text=text,
            candidate_quote=quote, asked_at_ms=None,
            level=(g.level if g else "not_reached"),
            difficulty=qdict.get("difficulty"),
            listen_for_hits=(g.listen_for_hits if g else []),
            red_flags_tripped=(g.red_flags_tripped if g else []),
            probes_used=len(qr.probes_used), probes_available=qr.probes_available))
```

8. `evidence_grounding_summary` in the manifest: replace `n_signals_rechecked`/`n_overrides` (which read `recheck_results`) with grade-based equivalents:
```python
                "n_questions_graded": len(grades),
                "n_overrides": sum(1 for g in grades.values() if g.overridden),
                "level_map": {s.value: s.level for s in scored},
                "cross_credit_signals": [s.value for s in scored if rollups[s.value].cross_credit_applied],
```

- [ ] **Step 4: Delete the retired files**

```bash
git rm app/modules/reporting/scoring/recheck.py prompts/v4/report_scorer/signal_recheck.txt tests/reporting/scoring/test_recheck.py tests/reporting/scoring/test_level_for_signal.py
```

Remove `SignalRecheckOut` from `app/modules/reporting/schemas.py`. Remove `level_for_signal` from `app/modules/reporting/scoring/aggregate.py` (and its now-unused `_TEXTURE_RANK`/`_RANK_LEVEL` if nothing else uses them — grep first). Grep the codebase for any remaining import of `recheck_signal`, `SignalRecheckOut`, or `level_for_signal` and remove/redirect:
```bash
grep -rn "recheck_signal\|SignalRecheckOut\|level_for_signal" app/ tests/
```

- [ ] **Step 5: Run the integration test + full reporting suite**

Run: `docker compose exec nexus python -m pytest tests/reporting -q`
Expected: PASS. Fix any test that imported the retired symbols (update to the new ones). The golden-report test (`test_golden_report.py`) may need its expected level map refreshed — inspect the diff and update the snapshot only if the new value is correct per the spec's roll-up rules (document the change in the commit message).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(reporting): question-anchored grading + roll-up; retire per-signal recheck"
```

---

## Task 8: Regression harness on the real session

**Files:**
- Create: `tests/reporting/fixtures/session_f2fd4b03_evidence.json`, `…_questions.json`, `…_signal_metadata.json`
- Test: `tests/reporting/test_regression_real_session.py`

This locks the spec's worked example: the real EMM-Engineer session stays `borderline`, iOS reads its honest basis, the iOS red-flag is recorded, and the manifest carries provenance.

- [ ] **Step 1: Capture the three real inputs as fixtures**

From the host (DB is the local Supabase container), export the live evidence, the bank questions (the dict shape `build_report` consumes), and the projected signal_metadata for session `f2fd4b03-9eb5-4384-acec-e6143552b4e4`:

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -tAc \
  "SELECT session_evidence_json FROM sessions WHERE id='f2fd4b03-9eb5-4384-acec-e6143552b4e4';" \
  > backend/nexus/tests/reporting/fixtures/session_f2fd4b03_evidence.json
```

For `…_questions.json`, dump the bank rows in the exact dict shape `actors.py` builds (id/position/text/signal_values/estimated_minutes/is_mandatory/follow_ups/positive_evidence/red_flags/rubric/evaluation_hint/question_kind/difficulty/primary_signal) — reuse the SQL `json_agg` query from the forensic step. For `…_signal_metadata.json`, dump `project_signal_metadata(snapshot.signals)` as a list of dicts (write a tiny one-off script invoking `project_signal_metadata`, or hand-build from the snapshot). Commit all three fixtures.

- [ ] **Step 2: Write the regression test**

Create `tests/reporting/test_regression_real_session.py`:

```python
import json
import pathlib
import pytest

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report

_FX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((_FX / name).read_text())


@pytest.mark.asyncio
async def test_real_emm_session_stays_borderline_with_honest_ios_basis():
    evidence = SessionEvidence.model_validate(_load("session_f2fd4b03_evidence.json"))
    questions = _load("session_f2fd4b03_questions.json")
    signal_metadata = _load("session_f2fd4b03_signal_metadata.json")

    report = await build_report(
        evidence=evidence, questions=questions, signal_metadata=signal_metadata,
        correlation_id="cid-regression", bank_id="bank-f2fd", signal_snapshot_id="snap-f2fd")

    assert report.verdict == "borderline"
    assert report.scoring_manifest.bank_id == "bank-f2fd"

    ios = next(s for s in report.signal_assessments if s.signal.startswith("iOS device management"))
    assert "thin" in ios.level_basis.lower()        # dedicated Wi-Fi answer was thin
    assert ios.cross_credit_applied is True          # lifted by the change-mgmt story

    ios_q = next(q for q in report.questions if "Wi" in q.question_text or "Wi‑Fi" in q.question_text)
    assert ios_q.red_flags_tripped, "the iOS Wi-Fi red flag should be recorded"
```

This is a real-API test (the grader calls OpenAI). Mark it `@pytest.mark.prompt_quality` if your CI excludes live calls by default; keep it runnable locally.

- [ ] **Step 3: Run it**

Run: `docker compose exec nexus python -m pytest tests/reporting/test_regression_real_session.py -q`
Expected: PASS. If the verdict or basis differs, do NOT loosen the test — investigate whether the roll-up or prompt is wrong vs. the spec, fix the code, and only then re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/reporting/fixtures/ tests/reporting/test_regression_real_session.py
git commit -m "test(reporting): regression harness locking the real EMM session worked example"
```

---

## Task 9: Frontend — surface per-question hits/flags + cross-credit basis

**Files:**
- Modify: `frontend/app/lib/api/reports.ts` (types)
- Modify: `frontend/app/components/dashboard/reports/QuestionByQuestion.tsx`
- Modify: `frontend/app/components/dashboard/reports/SignalAuditTable.tsx`
- Modify: `frontend/app/tests/components/reports/_fixture.ts`
- Test: `frontend/app/tests/components/reports/ReportView.test.tsx` (or the closest existing report component test)

All commands below run from `frontend/app/`.

- [ ] **Step 1: Extend the API types**

In `frontend/app/lib/api/reports.ts`, find the `QuestionOut`/question card type and the signal assessment type and add the new optional fields (match the file's existing naming/casing — the backend sends snake_case JSON):

```typescript
// question card
level?: string;
difficulty?: string | null;
listen_for_hits?: string[];
red_flags_tripped?: string[];
probes_used?: number;
probes_available?: number;
// signal assessment
cross_credit_applied?: boolean;
level_basis?: string;
```

- [ ] **Step 2: Write the failing component test**

In the report component test (e.g. `tests/components/reports/ReportView.test.tsx`), add a case that renders a question card carrying `listen_for_hits` + `red_flags_tripped` + `difficulty` and asserts they appear:

```typescript
it("renders listen-for hits, red-flag warnings, and difficulty on a question card", () => {
  const report = makeReport({
    questions: [{
      ...baseQuestion,
      difficulty: "hard",
      listen_for_hits: ["Checks certificate profiles"],
      red_flags_tripped: ["Does not know Wi-Fi/cert handling"],
      probes_used: 3, probes_available: 3,
    }],
  });
  render(<QuestionByQuestion questions={report.questions} />); // match the real prop shape
  expect(screen.getByText(/Checks certificate profiles/)).toBeInTheDocument();
  expect(screen.getByText(/Does not know Wi-Fi/)).toBeInTheDocument();
  expect(screen.getByText(/hard/i)).toBeInTheDocument();
});
```

Update `tests/components/reports/_fixture.ts` so `makeReport`/`baseQuestion` accept and default the new fields.

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test -- ReportView`
Expected: FAIL — the new text isn't rendered.

- [ ] **Step 4: Render the new fields**

In `QuestionByQuestion.tsx`, for each question card add (follow the existing card's styling/classes — do not invent a new design system):
- a difficulty chip (when `difficulty`),
- a "✓ Listen-for" list from `listen_for_hits`,
- a "⚠ Red flags" list from `red_flags_tripped` (use a caution tone),
- a probe-dependence hint `{probes_used}/{probes_available} probes` when `probes_available > 0`.

In `SignalAuditTable.tsx`, render `level_basis` next to each signal's level (a muted sub-label), and a small "cross-credited" tag when `cross_credit_applied`.

- [ ] **Step 5: Run test + lint + typecheck**

Run: `npm run test -- ReportView && npm run lint && npm run type-check`
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/api/reports.ts frontend/app/components/dashboard/reports/QuestionByQuestion.tsx frontend/app/components/dashboard/reports/SignalAuditTable.tsx frontend/app/tests/components/reports/
git commit -m "feat(report-ui): surface per-question listen-for hits, red-flags, difficulty, cross-credit basis"
```

---

## Task 10: End-to-end re-score of the live session + sign-off

**Files:** none (verification task)

- [ ] **Step 1: Re-score the real session through the actor (force)**

With the stack up, trigger a forced re-score so the live report regenerates under the new scorer. Use the existing report re-trigger path (the `/api/reports/.../regenerate` endpoint if present, or enqueue `score_session_report(session_id, tenant_id, correlation_id, force=True)` from a one-off shell in the worker container). Confirm:
```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c \
  "SELECT verdict, overall_score, scoring_manifest->>'bank_id' AS bank, scoring_manifest->>'scorer_code_version' AS ver FROM session_reports WHERE session_id='f2fd4b03-9eb5-4384-acec-e6143552b4e4';"
```
Expected: `verdict=borderline`, `bank` non-null, `ver=qa-1`.

- [ ] **Step 2: Eyeball the rendered report**

Open `http://localhost:3000/reports/session/f2fd4b03-9eb5-4384-acec-e6143552b4e4?...` and confirm the iOS question card shows the tripped Wi-Fi red flag, difficulty chips render, and the signal audit table shows the cross-credit basis.

- [ ] **Step 3: Human sign-off (CLAUDE.md HITL gate)**

This change alters candidate scoring. Get explicit sign-off before merge (the user is the gate). Record the before/after level map in the PR/commit description.

- [ ] **Step 4: Update memory**

Update `project_report_rubric_awareness.md` to mark the rework implemented + note the deferred engine-timing dependency (§7 of the spec).

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** Step-0 manifest = Task 1. Per-question grader (full card) = Tasks 3–4. Roll-up (anchor + ≤1 cross-credit + tie-break + disclaim guard) = Task 5. notes_by_question = Task 6. Rewire + retire recheck = Task 7. Regression worked-example = Task 8. Frontend surfacing = Task 9. Deferred timing (§7) is intentionally NOT implemented — do not add it.
- **Type consistency:** `QuestionGradeOut.level` is `strong|solid|thin|absent` (no `not_reached` — that's engine-only and applied by the roll-up). `roll_up_signal` returns `SignalRollup`. `pick_dedicated_question` takes `(signal, questions, outcomes)` and returns a question dict or `None`. The grader fn is `grade_question(*, question, notes, probes_used, probes_available, base_level, correlation_id)`.
- **Watch:** confirm the exact `ai_config` attribute name for the prompt cache-key prefix by grepping `recheck.py` before deleting it (Task 4 Step 4 reuses it; Task 7 deletes recheck.py).
