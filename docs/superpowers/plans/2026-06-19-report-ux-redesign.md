# Report UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the post-session report into an at-a-glance instrument — 0–10 scores, a competency radar, 0–5 star-rated questions, and an immersive header with Reel/Full-session CTAs — on the web first, then mirrored in the PDF.

**Architecture:** The scoring engine and DB are unchanged (scores persist 0–100; verdict logic untouched). The 0–10 scale is a **display unit** applied once at the read-model boundary. Two additive scoring outputs: a per-question LLM-emitted 0–10 (already-existing per-signal 0–100 is reused for the radar). A new server-sourced `header` block carries identity/job/session/skills. The web report is rebuilt with new hand-rolled SVG components (radar, rings, stars) in the `px`/in-house style; the PDF print template mirrors them.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy async (backend) · Next.js 16 / React 19 / TypeScript strict / Tailwind v4 + in-house `px/` primitives (frontend) · Jinja2 + headless Chromium (PDF). Tests: pytest (backend), Vitest + Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-06-19-report-ux-redesign-design.md`

## Global Constraints

- **Score scale:** recruiter-facing scores are **0–10, one decimal**. Internal scoring math + DB stay **0–100**. Convert ONLY at the read-model boundary via `to_ten`. Per-question scores are natively **0–10** (no conversion). Per-question stars render `score/2` (0–5, half-step).
- **Verdict logic is frozen.** No change to thresholds, ceilings, holistic, `resolve_verdict`, or the Borderline-always-human invariant. A regression test must prove verdicts are identical.
- **No new LLM call for signal/radar scores** — reuse `LEVEL_POINTS`. Only the per-question grade gains a `score` field.
- **Frontend:** in-house `px/` primitives only — NO shadcn, NO Radix, NO `@base-ui-components/react`. Tailwind v4 tokens (`var(--px-*)`), no raw hex in component logic (SVG chart fills may use tokens or inline values consistent with existing `report.css`). Icons = `lucide-react`. TypeScript strict, no `any`.
- **PII:** `candidate_email` may appear in the authenticated report payload + recruiter-curated PDF, never in logs (`candidate_id` only).
- **TDD:** write the failing test first, watch it fail, implement, watch it pass, commit. Frequent commits.
- **Backend module boundaries:** cross-module imports via public API (`from app.modules.<m> import X`), never deep paths.
- **Worker note:** the PDF render runs in `nexus-pdf-worker` (no hot-reload) — restart it after editing `reporting/pdf/`.

---

## Phase A — Backend data & scoring

### Task A1: 0–10 scale conversion at the read boundary

**Files:**
- Create: `backend/nexus/app/modules/reporting/scoring/scale.py`
- Modify: `backend/nexus/app/modules/reporting/schemas.py` (ScoreOut/ReportRead/ReportIndexItem/SignalAssessmentOut score types → float)
- Modify: `backend/nexus/app/modules/reporting/serialization.py` (`report_read_from_row` converts)
- Modify: `backend/nexus/app/modules/reporting/router.py` (hub index `overall_score` conversion)
- Test: `backend/nexus/tests/reporting/test_scale.py`, `backend/nexus/tests/reporting/test_serialization_scale.py`

**Interfaces:**
- Produces: `to_ten(score_100: int | float | None) -> float | None` — 0–100 → 0–10 one-decimal; None passthrough.
- Produces: `ReportRead`/`ScoreOut`/`SignalAssessmentOut`/`ReportIndexItem` whose `score`/`overall_score`/`session_score`/`holistic_delta` are 0–10 floats.

- [ ] **Step 1: Write the failing test for `to_ten`**

```python
# tests/reporting/test_scale.py
from app.modules.reporting.scoring.scale import to_ten

def test_to_ten_rounds_to_one_decimal():
    assert to_ten(81) == 8.1
    assert to_ten(65) == 6.5
    assert to_ten(100) == 10.0
    assert to_ten(0) == 0.0
    assert to_ten(35) == 3.5

def test_to_ten_passthrough_none():
    assert to_ten(None) is None

def test_to_ten_accepts_float():
    assert to_ten(72.0) == 7.2
```

- [ ] **Step 2: Run it, verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_scale.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.reporting.scoring.scale`

- [ ] **Step 3: Implement `scale.py`**

```python
# app/modules/reporting/scoring/scale.py
"""Display-unit conversion: internal 0–100 scores → recruiter-facing 0–10.

The scoring engine + DB keep 0–100 (calibrated verdict thresholds). This is the
single place the recruiter-facing 0–10 scale is produced — applied at the
read-model boundary only.
"""
from __future__ import annotations


def to_ten(score_100: int | float | None) -> float | None:
    """0–100 score → 0–10, one decimal. None passes through."""
    return None if score_100 is None else round(score_100 / 10, 1)
```

- [ ] **Step 4: Run it, verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_scale.py -v`
Expected: PASS

- [ ] **Step 5: Widen schema score types to float**

In `schemas.py`, change these annotations (values are now 0–10):
- `ScoreOut.score: int | None` → `float | None`
- `ScoreOut.session_score: int | None` → `float | None`
- `ScoreOut.holistic_delta: int | None` → `float | None`
- `SignalAssessmentOut.score: int | None` → `float | None`
- `ReportRead.overall_score: int | None` → `float | None`
- `ReportIndexItem.overall_score: int | None` → `float | None`

(`QuestionOut` is handled in A2 — leave it for now. `tier_label`/`tone`/`confidence` unchanged.)

- [ ] **Step 6: Write the failing test for `report_read_from_row` conversion**

```python
# tests/reporting/test_serialization_scale.py
from types import SimpleNamespace
from app.modules.reporting.serialization import report_read_from_row

def _row(**kw):
    base = dict(
        id=None, session_id=None, status="ready", engine_version="v3", version=1,
        verdict="advance", verdict_reason="ok", overall_score=81, overall_coverage=0.8,
        overall_confidence="high",
        dimension_scores={
            "overall": {"score": 81, "tier_label": "Strong", "tone": "ok",
                        "confidence": "high", "coverage": 0.8,
                        "session_score": 80, "holistic_delta": 5},
            "technical": {"score": 83, "tier_label": "Strong", "tone": "ok",
                          "confidence": "high", "coverage": 0.8},
        },
        signal_scorecards=[{"signal": "Intune", "type": "competency", "weight": 3,
                            "knockout": False, "priority": "required",
                            "provenance": "asked_directly", "level": "strong",
                            "score": 100, "evidence": []}],
        question_scorecards=[], summary={}, scoring_manifest=None, human_decision=None,
        generated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)

def test_dimension_and_signal_scores_are_ten_scale():
    read = report_read_from_row(_row())
    assert read.overall_score == 8.1
    assert read.scores["overall"].score == 8.1
    assert read.scores["overall"].session_score == 8.0
    assert read.scores["overall"].holistic_delta == 0.5
    assert read.scores["technical"].score == 8.3
    assert read.signal_assessments[0].score == 10.0
```

- [ ] **Step 7: Run it, verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_serialization_scale.py -v`
Expected: FAIL — scores still 81 / 100.

- [ ] **Step 8: Apply conversion in `report_read_from_row`**

In `serialization.py`, import `to_ten` and convert before building `ReportRead`. Replace the inline `"scores": row.dimension_scores or {}` and `"overall_score": row.overall_score` and `"signal_assessments": row.signal_scorecards or []` with converted copies:

```python
from app.modules.reporting.scoring.scale import to_ten

def _convert_score_dict(d: dict) -> dict:
    out = dict(d)
    for k in ("score", "session_score", "holistic_delta"):
        if k in out:
            out[k] = to_ten(out[k])
    return out

# inside report_read_from_row, before model_validate:
raw_scores = row.dimension_scores or {}
scores_ten = {k: _convert_score_dict(v) for k, v in raw_scores.items()}
sig_cards = [dict(s, score=to_ten(s.get("score"))) for s in (row.signal_scorecards or [])]
# then in the dict passed to ReportRead.model_validate:
#   "overall_score": to_ten(row.overall_score),
#   "scores": scores_ten,
#   "signal_assessments": sig_cards,
```

- [ ] **Step 9: Run it, verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_serialization_scale.py -v`
Expected: PASS

- [ ] **Step 10: Convert the hub index score**

In `router.py::list_report_index`, the row `overall_score` (0–100) must become 0–10. Import `to_ten` and wrap: `overall_score=to_ten(r["overall_score"])`. (Add `from app.modules.reporting.scoring.scale import to_ten` at top.)

- [ ] **Step 11: Verdict-unchanged regression check**

Run the existing reporting scoring tests to confirm the verdict pipeline is untouched (we changed only display types/serialization, not `aggregate.py`/`constants.py`):

Run: `docker compose run --rm nexus pytest tests/reporting -k "verdict or aggregate or scoring" -v`
Expected: PASS (no verdict changes).

- [ ] **Step 12: Commit**

```bash
git add app/modules/reporting/scoring/scale.py app/modules/reporting/schemas.py \
        app/modules/reporting/serialization.py app/modules/reporting/router.py \
        tests/reporting/test_scale.py tests/reporting/test_serialization_scale.py
git commit -m "feat(reporting): expose recruiter-facing scores on a 0-10 scale"
```

---

### Task A2: Per-question 0–10 score (stars)

**Files:**
- Modify: `backend/nexus/app/modules/reporting/schemas.py` (`QuestionGradeOut.score`, `QuestionOut.score`)
- Modify: `backend/nexus/app/modules/reporting/scoring/question_grade.py` (fallback derivation)
- Modify: `backend/nexus/app/modules/reporting/service.py` (wire `QuestionOut.score`)
- Modify: `backend/nexus/prompts/v4/report_scorer/question_grade.txt` (require + anchor the 0–10)
- Modify: `backend/nexus/app/ai/config.py` (bump `report_scorer_prompt_version`)
- Test: `backend/nexus/tests/reporting/test_question_score.py`

**Interfaces:**
- Consumes: `QuestionGradeOut` (existing) + `LEVEL_POINTS` from `constants.py`.
- Produces: `QuestionGradeOut.score: int` (0–10), `QuestionOut.score: int | None` (0–10), and a `score_from_level(level) -> int` fallback.

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_question_score.py
from app.modules.reporting.schemas import QuestionGradeOut, QuestionOut
from app.modules.reporting.scoring.question_grade import score_from_level

def test_questiongradeout_has_score_field():
    g = QuestionGradeOut(level="strong", score=10)
    assert g.score == 10

def test_score_from_level_maps_levels_to_ten_scale():
    assert score_from_level("strong") == 10
    assert score_from_level("solid") == 8
    assert score_from_level("thin") == 4
    assert score_from_level("absent") == 1
    assert score_from_level("not_reached") == 1

def test_questionout_carries_score():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="full text", candidate_quote="",
                    score=8)
    assert q.score == 8
```

- [ ] **Step 2: Run it, verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_question_score.py -v`
Expected: FAIL — `score` not a field / `score_from_level` undefined.

- [ ] **Step 3: Add `score` to `QuestionGradeOut` and `QuestionOut`**

In `schemas.py`:
- `QuestionGradeOut`: add `score: int = 0` (0–10, rubric-anchored; default 0 covers refusal before fallback overwrites).
- `QuestionOut`: add `score: int | None = None` (0–10; None = not assessed / no stars).

- [ ] **Step 4: Add `score_from_level` to `question_grade.py`**

```python
# question_grade.py — add near _RANK_LEVEL
from app.modules.reporting.scoring.constants import level_score

def score_from_level(level: str) -> int:
    """Fallback 0–10 question score from an engine level (LEVEL_POINTS ÷ 10)."""
    return round(level_score(level) / 10)
```

- [ ] **Step 5: Run it, verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_question_score.py -v`
Expected: PASS

- [ ] **Step 6: Make the grader populate `score` (LLM + fallback)**

In `grade_question`, the refusal branch already returns a `QuestionGradeOut`. Set its score from the base level; and when parsed, trust the LLM `score` but clamp 0–10. Replace the refusal return + final return:

```python
    if parsed is None:
        lvl = base_level if base_level != "not_reached" else "thin"
        return QuestionGradeOut(level=lvl, score=score_from_level(lvl))

    grounded, _ = ground_quotes(parsed.evidence_quotes, notes_block)
    clamped = max(0, min(10, parsed.score))
    return parsed.model_copy(update={"evidence_quotes": grounded, "score": clamped})
```

- [ ] **Step 7: Update the prompt to require + anchor the 0–10 score**

In `prompts/v4/report_scorer/question_grade.txt`, add to the output contract a required integer `score` (0–10) with anchors:

```
- score (integer 0–10): how fully the answer met THIS question's rubric.
    10 = fully meets the rubric; all key listen-fors hit, no red flags.
    7–8 = solid; minor gaps.
    4–6 = partial / thin; some substance, key elements missing.
    2–3 = weak attempt.
    1 = attempted but failed, or a red flag tripped.
    0 = not demonstrated / disclaimed.
  Calibrate to the stated <difficulty>: an easy question fully answered is not
  automatically a 10 if it left required facts unstated.
```

- [ ] **Step 8: Bump the prompt version**

In `ai/config.py`, increment `report_scorer_prompt_version` (e.g. `"v4"` → `"v4.1"`; match the existing format used in that field). This is the cache-key + manifest version.

- [ ] **Step 9: Wire `QuestionOut.score` in `build_report`**

In `service.py`, where `QuestionOut(...)` is constructed (the `q_out.append(...)` block), add `score=(g.score if g else None)` so the per-question card carries the 0–10 (None when the question was never graded / not asked).

- [ ] **Step 10: Write + run a build-wiring test**

Add to `test_question_score.py` a focused test that a graded question's `score` flows into the card. If the repo has an existing `build_report` fixture/test (`tests/reporting/test_service*.py`), extend it; otherwise assert at the `QuestionOut` level (Step 3 covers schema). Then run:

Run: `docker compose run --rm nexus pytest tests/reporting/test_question_score.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add app/modules/reporting/schemas.py app/modules/reporting/scoring/question_grade.py \
        app/modules/reporting/service.py app/ai/config.py \
        prompts/v4/report_scorer/question_grade.txt tests/reporting/test_question_score.py
git commit -m "feat(reporting): per-question 0-10 score for star rating"
```

---

### Task A3: Report header block (identity + job + session + skills)

**Files:**
- Modify: `backend/nexus/app/modules/reporting/schemas.py` (new `ReportHeader`, `ReportRead.header`)
- Modify: `backend/nexus/app/modules/reporting/assets.py` (new `attach_report_header`)
- Modify: `backend/nexus/app/modules/reporting/router.py` (call it in both GET handlers)
- Modify: `backend/nexus/app/modules/reporting/public_share.py` (attach on the public envelope)
- Test: `backend/nexus/tests/reporting/test_report_header.py`

**Interfaces:**
- Produces: `ReportHeader` Pydantic model; `ReportRead.header: ReportHeader | None`.
- Produces: `async def attach_report_header(*, db, report: ReportRead, session_id, tenant_id) -> None` — mutates `report.header` in place (mirrors `attach_reference_photo`'s shape in `assets.py`).
- Produces: `skills_from_assessments(assessments, *, cap=6) -> list[str]` — demonstrated signals (level ∈ {solid, strong}) sorted by weight desc.

- [ ] **Step 1: Write the failing test for skill derivation + schema**

```python
# tests/reporting/test_report_header.py
from app.modules.reporting.schemas import ReportHeader, SignalAssessmentOut
from app.modules.reporting.assets import skills_from_assessments

def _sa(signal, level, weight):
    return SignalAssessmentOut(signal=signal, type="competency", weight=weight,
                               knockout=False, priority="required",
                               provenance="asked_directly", level=level, score=None)

def test_skills_are_demonstrated_signals_by_weight():
    aa = [_sa("Intune", "strong", 3), _sa("Comms", "thin", 2),
          _sa("CondAccess", "solid", 1), _sa("Identity", "absent", 2)]
    assert skills_from_assessments(aa) == ["Intune", "CondAccess"]

def test_skills_cap():
    aa = [_sa(f"S{i}", "strong", 10 - i) for i in range(10)]
    assert len(skills_from_assessments(aa, cap=4)) == 4

def test_report_header_schema_defaults():
    h = ReportHeader(candidate_name="Punar", job_title="EMM Engineer", stage_label="AI Screening")
    assert h.skills == [] and h.candidate_email is None
```

- [ ] **Step 2: Run it, verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_report_header.py -v`
Expected: FAIL — `ReportHeader` / `skills_from_assessments` undefined.

- [ ] **Step 3: Add the `ReportHeader` schema**

In `schemas.py`:

```python
class ReportHeader(BaseModel):
    candidate_name: str
    candidate_email: str | None = None
    job_title: str = ""
    stage_label: str = ""
    session_started_at: str | None = None   # ISO 8601
    duration_seconds: int | None = None
    skills: list[str] = Field(default_factory=list)
    reference_photo_url: str | None = None
```

And add to `ReportRead`: `header: ReportHeader | None = None`.

- [ ] **Step 4: Add `skills_from_assessments` + `attach_report_header` to `assets.py`**

```python
# assets.py
from app.modules.reporting.schemas import ReportHeader, ReportRead, SignalAssessmentOut

_DEMONSTRATED = {"solid", "strong"}

def skills_from_assessments(assessments: list[SignalAssessmentOut], *, cap: int = 6) -> list[str]:
    demonstrated = [a for a in assessments if a.level in _DEMONSTRATED]
    demonstrated.sort(key=lambda a: a.weight, reverse=True)
    return [a.signal for a in demonstrated[:cap]]

async def attach_report_header(*, db, report: ReportRead, session_id, tenant_id) -> None:
    """Populate report.header from session → candidate/job/stage joins.
    Uses an explicit tenant filter (works under RLS + in tests)."""
    from sqlalchemy import text
    row = (await db.execute(text("""
        SELECT c.name AS candidate_name, c.email AS candidate_email,
               j.title AS job_title, st.name AS stage_name,
               s.agent_started_at, s.recording_duration_seconds
          FROM sessions s
          LEFT JOIN candidate_job_assignments a ON a.id = s.assignment_id
          LEFT JOIN candidates c ON c.id = a.candidate_id
          LEFT JOIN job_postings j ON j.id = a.job_posting_id
          LEFT JOIN job_pipeline_stages st ON st.id = s.stage_id
         WHERE s.id = :sid AND s.tenant_id = :tid
    """), {"sid": str(session_id), "tid": str(tenant_id)})).mappings().first()
    if row is None:
        return
    report.header = ReportHeader(
        candidate_name=row["candidate_name"] or "Candidate",
        candidate_email=row["candidate_email"],
        job_title=row["job_title"] or "",
        stage_label=row["stage_name"] or "",
        session_started_at=row["agent_started_at"].isoformat() if row["agent_started_at"] else None,
        duration_seconds=row["recording_duration_seconds"],
        skills=skills_from_assessments(report.signal_assessments),
        reference_photo_url=report.reference_photo_url,
    )
```

> Implementer note: confirm `candidates.email`, `sessions.agent_started_at`, and `sessions.recording_duration_seconds` column names against the models before running (they appear in migrations 0024/0050). Adjust the SELECT if a name differs.

- [ ] **Step 5: Run it, verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_report_header.py -v`
Expected: PASS (schema + skills tests; the DB join is covered by the router test below).

- [ ] **Step 6: Call `attach_report_header` in both report GET handlers**

In `router.py`, in `get_report_by_session` and `get_report_by_id`, after `attach_reference_photo(...)`, add:

```python
from app.modules.reporting.assets import attach_report_header  # top of file
await attach_report_header(db=db, report=read, session_id=<sid>, tenant_id=tenant_id)
```

(`<sid>` = `session_id` in the by-session handler, `row.session_id` in the by-id handler — matching the existing `attach_reference_photo` calls.)

- [ ] **Step 7: Attach the header on the public envelope**

In `public_share.py`, after the `ReportRead` is assembled and reference photo attached, call `attach_report_header` with the resolved session_id + tenant_id so `/recordings/<token>` carries the same header. (Find the existing reference-photo attach in that file and mirror it.)

- [ ] **Step 8: Write + run a router composition test**

Add a test that drives `get_report_by_session` against a seeded session/candidate/job/stage (follow the pattern in the existing `tests/reporting/test_router*.py` or `tests/test_reports_*`), asserting the response body has `header.candidate_name`, `header.job_title`, and `header.skills`. Run:

Run: `docker compose run --rm nexus pytest tests/reporting/test_report_header.py tests/reporting -k header -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/modules/reporting/schemas.py app/modules/reporting/assets.py \
        app/modules/reporting/router.py app/modules/reporting/public_share.py \
        tests/reporting/test_report_header.py
git commit -m "feat(reporting): server-sourced report header (identity, job, session, skills)"
```

---

## Phase B — Frontend web report (`frontend/app`)

> Run all frontend commands from `frontend/app/`. Tests: `npm run test -- <path>`. Type-check: `npm run type-check`.

### Task B1: Update report API types

**Files:**
- Modify: `frontend/app/lib/api/reports.ts`
- Test: `frontend/app/tests/api/reports-types.test.ts` (type-level + a tiny runtime assertion)

**Interfaces:**
- Produces: `ReportHeader` TS interface; `ReportRead.header`; `QuestionOut.score`; numeric (0–10) `score` fields already typed `number | null`.

- [ ] **Step 1: Add the `ReportHeader` interface + fields**

In `reports.ts`:

```typescript
export interface ReportHeader {
  candidate_name: string
  candidate_email: string | null
  job_title: string
  stage_label: string
  session_started_at: string | null
  duration_seconds: number | null
  skills: string[]
  reference_photo_url: string | null
}
```

Add to `QuestionOut`: `/** Rubric-anchored 0–10 score; null when not assessed. */ score?: number | null`.
Add to `ReportRead`: `header: ReportHeader | null`.
(The `score`/`overall_score` fields are already `number | null` — values are now 0–10; no type change, but update the JSDoc comments to say "0–10".)

- [ ] **Step 2: Type-check**

Run: `npm run type-check`
Expected: PASS (no consumers broken yet).

- [ ] **Step 3: Commit**

```bash
git add lib/api/reports.ts
git commit -m "feat(reports): header + per-question score in API types"
```

---

### Task B2: `StarRating` component (0–5 from 0–10, half-step)

**Files:**
- Create: `frontend/app/components/dashboard/reports/StarRating.tsx`
- Test: `frontend/app/tests/components/reports/StarRating.test.tsx`

**Interfaces:**
- Produces: `<StarRating valueTen={number} />` — renders 5 stars at half precision from a 0–10 input, plus an accessible label.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/reports/StarRating.test.tsx
import { render, screen } from '@testing-library/react'
import { StarRating } from '@/components/dashboard/reports/StarRating'

test('renders an accessible 0-5 label from a 0-10 value', () => {
  render(<StarRating valueTen={9} />)
  expect(screen.getByLabelText('4.5 out of 5')).toBeInTheDocument()
})

test('clamps and labels a full score', () => {
  render(<StarRating valueTen={10} />)
  expect(screen.getByLabelText('5 out of 5')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/StarRating.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `StarRating`**

Port the gold-star SVG from the approved mockup. Render 5 stars; for each, full / half (clip) / empty based on `stars = valueTen / 2`. Label = `${(Math.round(valueTen) / 2)} out of 5` formatted to drop trailing `.0`.

```tsx
'use client'
const STAR_PATH = 'M12 2l3 6.5 7 .8-5.2 4.8 1.4 6.9L12 17.6 5.8 21l1.4-6.9L2 9.3l7-.8z'

export function StarRating({ valueTen, size = 18 }: { valueTen: number; size?: number }) {
  const stars = Math.max(0, Math.min(5, valueTen / 2))
  const label = `${String(Math.round(stars * 2) / 2).replace(/\.0$/, '')} out of 5`
  return (
    <span role="img" aria-label={label} className="inline-flex gap-[3px]">
      {Array.from({ length: 5 }, (_, i) => {
        const fill = Math.max(0, Math.min(1, stars - i)) // 0, .5, or 1
        return (
          <svg key={i} width={size} height={size} viewBox="0 0 24 24" aria-hidden>
            <defs>
              <linearGradient id={`g${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ffd24d" /><stop offset="100%" stopColor="#f5a623" />
              </linearGradient>
              <clipPath id={`c${i}`}><rect x="0" y="0" width={24 * fill} height="24" /></clipPath>
            </defs>
            <path d={STAR_PATH} fill="none" stroke="#d9d9e3" strokeWidth="1.4" />
            {fill > 0 && <path d={STAR_PATH} fill={`url(#g${i})`} clipPath={`url(#c${i})`} />}
          </svg>
        )
      })}
    </span>
  )
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/StarRating.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/StarRating.tsx tests/components/reports/StarRating.test.tsx
git commit -m "feat(reports): StarRating component (0-5 half-step from 0-10)"
```

---

### Task B3: `ScoreRing` component (0–10)

**Files:**
- Create: `frontend/app/components/dashboard/reports/ScoreRing.tsx`
- Test: `frontend/app/tests/components/reports/ScoreRing.test.tsx`

**Interfaces:**
- Produces: `<ScoreRing valueTen={number | null} label={string} tone?={string} size?={number} />` — a circular gauge showing a 0–10 value (one decimal), ring fill = `valueTen/10`.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/reports/ScoreRing.test.tsx
import { render, screen } from '@testing-library/react'
import { ScoreRing } from '@/components/dashboard/reports/ScoreRing'

test('shows the 0-10 value and label', () => {
  render(<ScoreRing valueTen={8.1} label="Overall" />)
  expect(screen.getByText('8.1')).toBeInTheDocument()
  expect(screen.getByText('Overall')).toBeInTheDocument()
})

test('renders an em-dash when not assessed', () => {
  render(<ScoreRing valueTen={null} label="Behavioral" />)
  expect(screen.getByText('—')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/ScoreRing.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `ScoreRing`** (port the ring SVG from the mockup; tone color via `tierTone`/`TONE_INK` from `report-format.ts`; fill offset = `circumference * (1 - valueTen/10)`; display `valueTen.toFixed(1)` or `—` for null). Keep the markup parallel to the existing `ScoreGauge.tsx` so it can replace it.

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/ScoreRing.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/ScoreRing.tsx tests/components/reports/ScoreRing.test.tsx
git commit -m "feat(reports): ScoreRing gauge on the 0-10 scale"
```

---

### Task B4: `CompetencyRadar` component

**Files:**
- Create: `frontend/app/components/dashboard/reports/CompetencyRadar.tsx`
- Test: `frontend/app/tests/components/reports/CompetencyRadar.test.tsx`

**Interfaces:**
- Consumes: `SignalAssessmentOut[]` (from `reports.ts`).
- Produces: `<CompetencyRadar assessments={SignalAssessmentOut[]} />` — renders an SVG radar over **assessed** primary signals (`provenance !== 'not_reached'`), 0–10 axes, cap 8 by weight; if fewer than 3 assessed signals, renders a ranked bar list fallback instead.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/reports/CompetencyRadar.test.tsx
import { render, screen } from '@testing-library/react'
import { CompetencyRadar } from '@/components/dashboard/reports/CompetencyRadar'
import type { SignalAssessmentOut } from '@/lib/api/reports'

const sa = (signal: string, score: number, provenance = 'asked_directly'): SignalAssessmentOut => ({
  signal, type: 'competency', weight: 2, knockout: false, priority: 'required',
  provenance: provenance as SignalAssessmentOut['provenance'], level: 'solid', score,
  evidence: [], overridden: false, override_reason: null,
})

test('plots assessed signals as radar axes', () => {
  render(<CompetencyRadar assessments={[sa('Intune', 9), sa('Comms', 7), sa('Identity', 6)]} />)
  expect(screen.getByText('Intune')).toBeInTheDocument()
  expect(screen.getByText('Identity')).toBeInTheDocument()
})

test('falls back to bars under 3 assessed signals', () => {
  render(<CompetencyRadar assessments={[sa('Intune', 9), sa('Comms', 7)]} />)
  // bar fallback uses role="img" with a known label
  expect(screen.getByLabelText(/competency scores/i)).toBeInTheDocument()
})

test('excludes not_reached signals', () => {
  render(<CompetencyRadar assessments={[sa('A', 9), sa('B', 8), sa('C', 7), sa('D', 1, 'not_reached')]} />)
  expect(screen.queryByText('D')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/CompetencyRadar.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `CompetencyRadar`**

Port the radar SVG from the approved `glance-roomy` mockup. Logic:
- `assessed = assessments.filter(a => a.provenance !== 'not_reached' && a.score != null)`, sort by `weight` desc then `signal`, take first 8.
- If `assessed.length < 3` → render the bar fallback: a list of `signal` + a `<div>` bar width `score*10%`, wrapped in `<div role="img" aria-label="Competency scores">`.
- Else compute axis points: for axis `i` of `n`, angle `= -90 + i*360/n` (deg), radius factor `= score/10`; draw grid polygon (value 1), mid polygon (0.5), axis lines, data polygon, and a `<text>` label per axis (this is what the tests assert on).
- Use the existing accent token for the data polygon stroke/fill.

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/CompetencyRadar.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/CompetencyRadar.tsx tests/components/reports/CompetencyRadar.test.tsx
git commit -m "feat(reports): CompetencyRadar (assessed signals, 0-10, bar fallback)"
```

---

### Task B5: `ImmersiveHeader` component

**Files:**
- Create: `frontend/app/components/dashboard/reports/ImmersiveHeader.tsx`
- Test: `frontend/app/tests/components/reports/ImmersiveHeader.test.tsx`

**Interfaces:**
- Consumes: `ReportHeader`, `Verdict`, reel availability flag.
- Produces: `<ImmersiveHeader header={ReportHeader} verdict={Verdict} hasReel={boolean} onOpenReel={()=>void} onOpenSession={()=>void} />` — immersive hero with photo/identity/job/session/skill-pills/stamp + two CTAs; Reel button hidden when `!hasReel || verdict === 'reject'`.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/reports/ImmersiveHeader.test.tsx
import { render, screen } from '@testing-library/react'
import { ImmersiveHeader } from '@/components/dashboard/reports/ImmersiveHeader'
import type { ReportHeader } from '@/lib/api/reports'

const header: ReportHeader = {
  candidate_name: 'Punar Sharma', candidate_email: 'punar@example.com',
  job_title: 'EMM Engineer', stage_label: 'AI Screening',
  session_started_at: '2026-06-18T14:14:00Z', duration_seconds: 840,
  skills: ['Intune', 'Troubleshooting'], reference_photo_url: null,
}

test('shows identity, email, job and skills', () => {
  render(<ImmersiveHeader header={header} verdict="advance" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.getByText('Punar Sharma')).toBeInTheDocument()
  expect(screen.getByText('punar@example.com')).toBeInTheDocument()
  expect(screen.getByText('Intune')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /candidate reel/i })).toBeInTheDocument()
})

test('hides the reel button on reject', () => {
  render(<ImmersiveHeader header={header} verdict="reject" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.queryByRole('button', { name: /candidate reel/i })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/ImmersiveHeader.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `ImmersiveHeader`**

Port the chosen `reel-glow` immersive header from the mockup: deep gradient + radial-glow + dot-grid background; 164px photo (or monogram from initials when `reference_photo_url` is null); name + email; an inline row with job · stage, formatted date (from `session_started_at`), and duration (mm:ss from `duration_seconds`); the two CTAs (`ReelButton` glowing gradient — only when `hasReel && verdict !== 'reject'`; `FullSessionButton` glass); skill pills row; verdict stamp via `verdictMeta`. Use `lucide-react` Play icon. CTAs are real `<button>`s wired to `onOpenReel`/`onOpenSession`.

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/ImmersiveHeader.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/ImmersiveHeader.tsx tests/components/reports/ImmersiveHeader.test.tsx
git commit -m "feat(reports): immersive report header with Reel/Full-session CTAs"
```

---

### Task B6: `AtAGlanceBand` component

**Files:**
- Create: `frontend/app/components/dashboard/reports/AtAGlanceBand.tsx`
- Test: `frontend/app/tests/components/reports/AtAGlanceBand.test.tsx`

**Interfaces:**
- Consumes: `ReportRead` (for `scores`, `signal_assessments`, `quick_summary`).
- Produces: `<AtAGlanceBand report={ReportRead} />` — radar (left) + 3 evenly-spaced equal `ScoreRing`s (Overall/Technical/Comms) + one-line lede + a full-width Top-strengths / Watch-outs pills strip. NO verdict shown here.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/reports/AtAGlanceBand.test.tsx — assert the 3 rings render,
// the strengths/watch-out pills derive from signal levels, and NO verdict label
// (e.g. "Recommended") appears in the band.
```

Write concrete assertions: render with a `ReportRead` fixture having `scores.overall/technical/communication` + a few `signal_assessments`; assert `getByText('Overall')`, strengths pill text present, and `queryByText('Recommended')` is null.

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/AtAGlanceBand.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `AtAGlanceBand`**

Compose `CompetencyRadar` + three `ScoreRing`s (`scores.overall`, `scores.technical`, `scores.communication` — each `valueTen = scores[k]?.score`). Rings in a `flex justify-between` (equal size, evenly spaced — satisfies the "evenly scale/space the gauges" note). Lede = first sentence of `quick_summary` (or a short derived line). Pills strip: Top strengths = `skills_from_assessments` logic client-side (level ∈ {solid,strong}, by weight, cap 3) in green pills; Watch-outs = required/knockout signals at level ∈ {thin, absent, not_reached} in amber pills. No verdict.

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/AtAGlanceBand.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/AtAGlanceBand.tsx tests/components/reports/AtAGlanceBand.test.tsx
git commit -m "feat(reports): at-a-glance band (radar + rings + strengths/watch pills)"
```

---

### Task B7: Question-by-question hero-star restyle

**Files:**
- Modify: `frontend/app/components/dashboard/reports/QuestionByQuestion.tsx`
- Test: `frontend/app/tests/components/reports/QuestionByQuestion.test.tsx` (create or extend)

**Interfaces:**
- Consumes: `QuestionOut[]` incl. new `score`; `StarRating`.
- Produces: hero-star card per question — full `question_text` (no truncation/clamp), `StarRating valueTen={q.score}` + "X.X / 5" when `score != null` (else "Not assessed" chip), difficulty + status chips, candidate quote, our_read, listen-for hits (green) / red-flags (red).

- [ ] **Step 1: Write the failing test**

Assert: full question text renders (use a >60-char question and assert the whole string is present — guards the un-truncation); when `score=8`, `getByLabelText('4 out of 5')` is present; when `score` null, "Not assessed" shows and no star label.

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/QuestionByQuestion.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the restyle**

Port the approved `herostars` card: seq dot, full `question_text` (render `q.question_text`, never `q.title`), `StarRating` + "X.X / 5" top-right (or "Not assessed" chip when `score == null`), difficulty/status chips, `candidate_quote` blockquote, `our_read`, and `listen_for_hits`/`red_flags_tripped` as green/red pills. Keep any existing seek-into-recording affordance (`asked_at_ms`) if present.

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/QuestionByQuestion.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/QuestionByQuestion.tsx tests/components/reports/QuestionByQuestion.test.tsx
git commit -m "feat(reports): hero-star question cards with full question text"
```

---

### Task B8: Recompose `ReportView` (Layout II) + drop `scoreToTen`

**Files:**
- Modify: `frontend/app/components/dashboard/reports/ReportView.tsx`
- Modify: `frontend/app/components/dashboard/reports/ScoresCard.tsx` (remove `scoreToTen` ÷10; values already 0–10)
- Modify: `frontend/app/components/dashboard/reports/ScoreGauge.tsx` (render 0–10 input) OR swap usages to `ScoreRing`
- Modify: `frontend/app/components/dashboard/reports/report-format.ts` (drop/replace `scoreToTen`; rescale `scoreBandTone` thresholds to 0–10)
- Modify: `frontend/app/app/(dashboard)/reports/session/[sessionId]/page.tsx` (header from `report.header`)
- Test: `frontend/app/tests/components/reports/ReportView.test.tsx` (extend)

**Interfaces:**
- Consumes: all Phase B components + `report.header`.
- Produces: the new report layout — `ImmersiveHeader` → `AtAGlanceBand` → two-column body (left: summary, why-verdict, strengths/concerns, questions, signal audit; right sticky rail: `ScoresCard`, `ProctoringIntegrityPanel`, `HumanDecisionPanel`, `PlaybackPanel`).

- [ ] **Step 1: Rescale `report-format.ts`**

`scoreBandTone(score)` currently keys on 65/40 (0–100). Change to 0–10: `>= 6.5 → ok`, `>= 4.0 → caution`, else danger. Replace `scoreToTen` (which divides by 10) with a `formatTen(score: number | null): string | null` that just formats one decimal (value is already 0–10). Update all callers.

- [ ] **Step 2: Update `ScoreGauge`/`ScoresCard` for 0–10**

In `ScoresCard.tsx`: remove the `scoreToTen` import; the "Session score … holistic" line now uses the already-0–10 `session_score`/`holistic_delta` (drop the `/10`). Either (a) keep `ScoreGauge` and make it accept a 0–10 `score` (fill = `score/10`, display `score.toFixed(1)`), or (b) replace `ScoreGauge` usages with the new `ScoreRing`. Pick one and apply consistently.

- [ ] **Step 3: Recompose `ReportView` to Layout II**

Replace the current header/grid with: `<ImmersiveHeader header={report.header!} verdict={report.verdict} hasReel={...} onOpenReel onOpenSession />`, then `<AtAGlanceBand report={report} />`, then a `xl:grid-cols-[1.7fr_1fr]` grid: left column = QuickSummary, WhyContrast, StrengthsConcerns, QuestionByQuestion, SignalAuditTable; right column = a `sticky top-4` wrapper around ScoresCard, ProctoringIntegrityPanel, HumanDecisionPanel, PlaybackPanel. Wire `onOpenReel`/`onOpenSession` to the existing theater open handlers (`openTheater`). `hasReel` from existing reel availability (the page already knows via `useReel`/ReelCard — pass it down, default false).

- [ ] **Step 4: Feed `header` from the page**

In `page.tsx`, the identity currently comes from query params; prefer `report.header`. Pass the report through to `ReportView` (already does) — `ReportView` reads `report.header`. Keep query-param `candidateName`/`title` as a fallback only if `report.header` is null (legacy reports).

- [ ] **Step 5: Run the report view + existing report tests**

Run: `npm run test -- tests/components/reports/`
Expected: PASS (update any existing assertions that referenced the old layout/score scale).

- [ ] **Step 6: Type-check + lint**

Run: `npm run type-check && npm run lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add components/dashboard/reports/ app/(dashboard)/reports/session/
git commit -m "feat(reports): recompose report view (immersive header, glance band, sticky rail, 0-10)"
```

---

### Task B9: Signal audit table restyle (0–10 + mini bar)

**Files:**
- Modify: `frontend/app/components/dashboard/reports/SignalAuditTable.tsx`
- Test: `frontend/app/tests/components/reports/SignalAuditTable.test.tsx` (create or extend)

- [ ] **Step 1: Write the failing test** — assert a signal row shows its 0–10 score (e.g. `8.0`) and a level label.

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- tests/components/reports/SignalAuditTable.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement** — render `assessment.score` as `score.toFixed(1)` with a slim horizontal bar (`width: score*10%`) tinted by `scoreBandTone(score)`; keep weight/knockout/provenance/level_basis columns.

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- tests/components/reports/SignalAuditTable.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/SignalAuditTable.tsx tests/components/reports/SignalAuditTable.test.tsx
git commit -m "feat(reports): signal audit on the 0-10 scale with mini bars"
```

---

## Phase C — PDF mirror (`reporting/pdf/`)

> The PDF render runs in `nexus-pdf-worker` (no hot-reload). After editing, restart it: `docker compose up -d --force-recreate nexus-pdf-worker`. Verify the rendered PDF by triggering a share or a local render harness.

### Task C1: PDF context — header, radar, stars, 0–10 colors

**Files:**
- Modify: `backend/nexus/app/modules/reporting/pdf/context.py`
- Test: `backend/nexus/tests/reporting/test_pdf_context.py` (create or extend)

**Interfaces:**
- Consumes: `ReportRead` with `header`, `SignalAssessmentOut.score` (0–10), `QuestionOut.score` (0–10).
- Produces: a context dict with `header` fields, `radar` axis points (assessed signals, 0–10), per-question `score`/stars, and 0–10-scaled bar colors.

- [ ] **Step 1: Write the failing test**

Assert `build_pdf_context(report, ...)` returns: `ctx["header"]["candidate_email"]`, a `radar` list of assessed signals only (excludes `not_reached`), each question carrying `score` and the FULL `question_text` (not a 60-char title), and `_bar_color(8.0)` → green band on the 0–10 scale.

- [ ] **Step 2: Run it, verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_pdf_context.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

- Rescale `_bar_color`/`assessed_dimensions` thresholds to 0–10 (`>= 8 green`, `>= 6 amber`, else red).
- Add `header` to the context from `report.header` (fallback to the existing `candidate_name`/`job_title` params when null).
- Add a `radar` list: assessed primary signals (`provenance != "not_reached"`, `score is not None`), sorted by weight, cap 8, each `{name, score}` (0–10).
- Pass `questions` with `score` + full `question_text` (the template already has `q.question_text`; ensure it's in the dumped dict — it is, via `model_dump`).
- Compute star fill fractions per question if simpler to do in Python (`stars=[fill0..fill4]`) or leave to a Jinja macro (C2).

- [ ] **Step 4: Run it, verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_pdf_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/pdf/context.py tests/reporting/test_pdf_context.py
git commit -m "feat(reporting/pdf): context for header, radar, stars, 0-10 scale"
```

---

### Task C2: PDF template — immersive header, radar, rings, full question text + stars

**Files:**
- Modify: `backend/nexus/app/modules/reporting/pdf/templates/report.html.j2`

- [ ] **Step 1: Header** — enlarge the hero; add candidate **email** under the name, role · stage · **date · duration** row, and a **skill-pills** row (`header.skills`). Keep the verdict stamp.

- [ ] **Step 2: At-a-glance** — add an SVG **radar** (loop `radar` axis points; same polygon math as the web component) beside the gauges; show gauges as **0–10**; add a Top-strengths / Watch-outs pills strip.

- [ ] **Step 3: Questions** — render the FULL `{{ q.question_text }}` (replace the truncated `{{ q.title }}` in `.qtitle`) and add an SVG **star row** (gold stars, half fill from `q.score/2`) + "X.X / 5". Keep `break-inside:avoid` on `.qcard`.

- [ ] **Step 4: Render-verify**

Restart the worker and produce a PDF (trigger a share on a `ready` report, or run the local render path). Confirm: header shows email + skills, question text is no longer cut off, stars + radar render, all numbers are 0–10.

```bash
docker compose up -d --force-recreate nexus-pdf-worker
# then trigger POST /api/reports/session/{id}/share and inspect the emailed/uploaded PDF
```

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/pdf/templates/report.html.j2
git commit -m "feat(reporting/pdf): immersive header, radar, full question text + stars"
```

---

## Self-Review (completed during authoring)

- **Spec coverage:** §3.1 scale→A1; §3.2 per-signal radar→A1 (conversion) + B4 (consumption); §3.3 per-question 0–10→A2 + B2/B7; §3.4 header→A3 + B1/B5; §4 web→B2–B9; §5 PDF→C1–C2; §6 testing→test step in every task. All spec sections map to tasks.
- **Type consistency:** `to_ten` (A1) used in serialization + hub. `score_from_level` (A2) reused by fallback. `ReportHeader`/`skills_from_assessments` (A3) ↔ TS `ReportHeader` (B1) ↔ `ImmersiveHeader` props (B5). `StarRating valueTen` (B2) consumed by B7. `ScoreRing valueTen` (B3) consumed by B6/B8. `CompetencyRadar assessments` (B4) consumed by B6. Names consistent across tasks.
- **Placeholder scan:** no TBD/TODO; each code step shows real code; restyle steps (B7/B9/C2) reference the approved mockups + name exact ports rather than "add styling".
- **Open verification flagged inline:** A3 Step 4 notes the implementer must confirm `candidates.email` / `sessions.agent_started_at` / `recording_duration_seconds` column names before running.
