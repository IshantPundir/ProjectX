# AI Screening as a Skills Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI-screening stage a technical-skills test — extract few high-impact JD-accurate signals classified by `purpose` (skill vs eligibility), and rewrite the `ai_screening` bank recipe to test only skills via scenarios within budget.

**Architecture:** Two back-to-back phases. Phase 1 (signals): add a `purpose` field to the JSONB signal (no DB migration), rewrite extraction to a versioned v2 prompt (lean/consolidated/purpose-classified), expose `purpose` in the recruiter Signal Inspector. Phase 2 (bank): the `ai_screening` generation filters to `purpose=skill` signals and the v3 recipe + critic test skills via scenarios (reverse the v3 P1 rule, drop experience_check/education, ≤1 behavioral, lead-level dedup, trim-to-budget).

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, Dramatiq, OpenAI via `instructor`, pytest; Next.js 16 + Zustand + React Query + Vitest.

**Spec:** `docs/superpowers/specs/2026-06-12-ai-screening-skills-test-design.md`

---

## Code-quality mandate (binding)
- `purpose` defaults to `"skill"` so legacy snapshots never regress — a documented backward-compat default, not a silent fallback.
- v1 extraction prompt retained as immutable provenance; v2 is a new file (EEOC audit trail).
- The eligibility filter is applied ONCE in the bank actor, not scattered.
- Every new branch ships with a test in the same task. No dead code from renames.

---

## File Structure

**Phase 1 (signals):**
- `app/ai/schemas.py` — `SignalPurpose` + `purpose` on `SignalItemV2`; `≥1 skill` validator.
- `app/config.py` + `app/ai/config.py` — `jd_signal_extraction_prompt_version` (default `v2`).
- `prompts/v2/jd_signal_extraction.txt` (CREATE).
- `app/modules/jd/actors.py` — versioned loader + stamp configured `prompt_version` in the snapshot.
- `app/modules/jd/schemas.py` (or wherever `save_signals` validates) — ensure the recruiter-save body accepts `purpose`.
- `frontend/app/lib/api/jobs.ts` — `SignalItem.purpose`.
- `frontend/app/components/dashboard/jd-panels/EditableSignalsPanel.tsx` — purpose toggle.
- `frontend/app/stores/job-edit.ts` — `purpose` default in `addChip`.
- `tests/jd/prompt_evals/test_signal_extraction_evals.py` (CREATE).

**Phase 2 (bank):**
- `app/modules/question_bank/actors.py` — filter `purpose=eligibility` out of generation for `ai_screening`.
- `prompts/v3/question_bank_ai_screening.txt` — scenario-primary recipe override + lead-dedup.
- `prompts/v3/question_bank_critic.txt` — checklist: scenario-dominance, no eligibility/claim Q, ≤1 behavioral, lead-dedup, trim-to-budget.
- `app/modules/reporting/scoring/` — neutralize un-questioned eligibility signals + zero-knockout no-op (verify/adjust).
- `tests/question_bank/prompt_evals/test_bank_gen_evals.py` — new evals.

---

# PHASE 1 — SIGNALS

## Task 1: Schema — `purpose` on `SignalItemV2`

**Files:**
- Modify: `app/ai/schemas.py`
- Test: `tests/test_signal_purpose_schema.py` (CREATE)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signal_purpose_schema.py
import pytest
from pydantic import ValidationError
from app.ai.schemas import SignalItemV2, ExtractedSignals


def _sig(value="Workato workflow development", purpose="skill", **kw):
    base = dict(value=value, type="competency", priority="required", weight=3,
                knockout=False, stage="interview", source="ai_extracted",
                inference_basis=None, purpose=purpose)
    base.update(kw)
    return SignalItemV2(**base)


def test_purpose_defaults_to_skill_when_absent():
    s = SignalItemV2(value="x", type="competency", priority="required", weight=2,
                     knockout=False, stage="interview", source="ai_extracted",
                     inference_basis=None)
    assert s.purpose == "skill"


def test_purpose_accepts_eligibility():
    assert _sig(purpose="eligibility").purpose == "eligibility"


def test_purpose_rejects_unknown():
    with pytest.raises(ValidationError):
        _sig(purpose="made_up")


def test_extracted_signals_requires_at_least_one_skill():
    elig = dict(type="experience", priority="required", weight=3, knockout=True,
                stage="screen", source="ai_extracted", inference_basis=None,
                purpose="eligibility")
    sigs = [SignalItemV2(value=f"{i}+ years", **elig) for i in range(5)]
    with pytest.raises(ValidationError):
        ExtractedSignals(signals=sigs, seniority_level="mid", role_summary="a role summary")


def test_extracted_signals_passes_with_a_skill():
    elig = dict(type="experience", priority="required", weight=3, knockout=True,
                stage="screen", source="ai_extracted", inference_basis=None,
                purpose="eligibility")
    sigs = [SignalItemV2(value=f"{i}+ years", **elig) for i in range(4)]
    sigs.append(_sig())  # one skill (competency/interview)
    out = ExtractedSignals(signals=sigs, seniority_level="mid", role_summary="a role summary")
    assert any(s.purpose == "skill" for s in out.signals)
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/test_signal_purpose_schema.py -q`
Expected: FAIL — `purpose` not a field; `≥1 skill` validator missing.

- [ ] **Step 3: Add `SignalPurpose` + `purpose` field**

In `app/ai/schemas.py`, after the other Signal Literals (~line 19):
```python
SignalPurpose = Literal["skill", "eligibility"]
```
In `SignalItemV2`, after the `knockout` field (~line 32):
```python
    # Assessed in the AI skills screen ("skill") vs recruiter pre-screened ("eligibility").
    # Default "skill" so legacy snapshots (no purpose) stay testable — no regression.
    purpose: SignalPurpose = "skill"
```

- [ ] **Step 4: Add the `≥1 skill` validator**

In `ExtractedSignals.check_coverage`, before `return self`:
```python
        if not any(s.purpose == "skill" for s in self.signals):
            raise ValueError("Must include at least one signal with purpose='skill'")
```

- [ ] **Step 5: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_signal_purpose_schema.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/ai/schemas.py backend/nexus/tests/test_signal_purpose_schema.py
git commit -m "feat(jd): signal purpose field (skill vs eligibility) + >=1-skill validator"
```

---

## Task 2: AIConfig — versioned extraction prompt

**Files:**
- Modify: `app/config.py`, `app/ai/config.py`
- Test: `tests/test_jd_extraction_prompt_version.py` (CREATE)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jd_extraction_prompt_version.py
from app.ai.config import AIConfig


def test_jd_signal_extraction_prompt_version_defaults_v2():
    assert AIConfig().jd_signal_extraction_prompt_version == "v2"


def test_jd_signal_extraction_prompt_version_env_override(monkeypatch):
    monkeypatch.setenv("JD_SIGNAL_EXTRACTION_PROMPT_VERSION", "v1")
    assert AIConfig().jd_signal_extraction_prompt_version == "v1"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/test_jd_extraction_prompt_version.py -q`
Expected: FAIL — attribute missing.

- [ ] **Step 3: Add the setting in `app/config.py`**

Near the other prompt-version settings (e.g. `question_bank_prompt_version`):
```python
    jd_signal_extraction_prompt_version: str = "v2"
```

- [ ] **Step 4: Add the AIConfig property in `app/ai/config.py`**

Near the extraction properties:
```python
    @property
    def jd_signal_extraction_prompt_version(self) -> str:
        return self._settings.jd_signal_extraction_prompt_version
```

- [ ] **Step 5: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_jd_extraction_prompt_version.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/tests/test_jd_extraction_prompt_version.py
git commit -m "feat(jd): configurable jd_signal_extraction_prompt_version (default v2)"
```

---

## Task 3: v2 extraction prompt

**Files:**
- Create: `prompts/v2/jd_signal_extraction.txt`

> Prompt quality is validated by the extraction eval (Task 7). This task creates the file + verifies it loads.

- [ ] **Step 1: Create `prompts/v2/jd_signal_extraction.txt`**

```
You are an enterprise hiring-intelligence system that extracts a SMALL, HIGH-IMPACT set of
structured hiring signals from a job description, for a downstream AI-led skills interview.

# Your task

You receive, IN THIS ORDER:
  1. The hiring company's profile (about, industry, hiring_bar) — stable context.
  2. The job description (enriched or raw).
  3. Optionally, a project scope paragraph.

Read the context BEFORE the document. Produce one structured output: a FLAT signal list +
seniority_level + a crisp role_summary.

# The bar: FEW, high-signal-density, JD-faithful

This is the single most important rule. Most JDs warrant roughly 8–10 signals — not 20+.
  - Extract the JD's real MUST-HAVE skills and the few highest-impact responsibilities.
  - Capture genuinely-differentiating Good-to-Haves SPARINGLY, at weight 1 / preferred.
  - Do NOT manufacture a signal from every keyword, tool name, buzzword, or boilerplate
    responsibility. Padding the list with low-value signals is the primary failure here.

# CONSOLIDATE — one signal = one unit of assessment

Combine closely-related sub-skills that you would test TOGETHER into a SINGLE grouped
signal. A signal should map to roughly ONE scenario question + one rubric.
  - e.g. "REST APIs", "SOAP/XML", "JSON data formats" → one signal:
    "API integration & data transformation (REST/SOAP, JSON)".
  - e.g. "RDBMS or NoSQL" stays ONE signal (a one-of choice).
Do NOT over-combine genuinely distinct competencies; do NOT fragment one competency into
many near-duplicates.

# purpose — classify EVERY signal: "skill" or "eligibility"

  - `purpose = "skill"`: a technical or behavioral COMPETENCY the AI screen will ASSESS by
    making the candidate reason through a scenario or describe real work (Workato workflow
    design, API integration, agent-workflow design, DB reasoning, collaboration).
  - `purpose = "eligibility"`: a PRE-SCREENED fact the AI screen will NOT test — tenure /
    years of experience, a degree, a certification. The recruiter verifies these before the
    session; the AI screen is purely a skills test.

Where a JD line bundles a TENURE with a TOOL ("1 year hands-on with Workato"), emit BOTH:
  - an `eligibility` signal for the duration ("At least 1 year with Workato"), AND
  - a distinct `skill` signal for the competency itself ("Workato recipe/workflow development").
The duration is pre-screened; the competency is what we test.

# Signal types (the existing taxonomy — orthogonal to purpose)

  - `competency`: a skill, tool, methodology, or domain to demonstrate.
  - `experience`: a tenure / scope / scale requirement (usually purpose=eligibility).
  - `credential`: a degree, certification, license (purpose=eligibility).
  - `behavioral`: a role-specific work-style / collaboration / leadership expectation.

# Weight & knockout — map to the JD's OWN structure

  - weight 3 (critical): in the title, the "Must-Have" list, or flagged "must/essential".
  - weight 2 (important): a clear single requirement.
  - weight 1 (baseline): nice-to-have / good-to-have / implied.
  - knockout = true ONLY for a genuine non-negotiable gate (a hard "minimum X", a legally
    required license/clearance). Prefer putting knockout on the ELIGIBILITY signal (years,
    license), not on a skill — skills are assessed by depth, not a yes/no gate. Max 5
    knockouts.

# Stage (unchanged — for routing; keep assigning it)

  - stage = "screen": quick yes/no eligibility-style checks.
  - stage = "interview": competencies needing depth ("walk me through how you would…").

# Provenance (unchanged)

  - source = "ai_extracted": stated in the JD. inference_basis MUST be null.
  - source = "ai_inferred": logically implied. inference_basis MUST be a short explanation.
    Infer only from: role title + seniority; technology adjacency; company profile.
  HARD RULES — NEVER infer certifications/years/regulatory knowledge not present, leadership
  beyond the title, or anything that could create a discriminatory criterion.

# Coverage requirements

  - At least 5 signals total; aim for ~8–10.
  - At least 1 signal with purpose = "skill".
  - At least 1 stage="screen" and 1 stage="interview".
  - At least 1 competency.
  - No more than 5 knockout signals.

# Output

  - seniority_level: junior | mid | senior | lead | principal.
  - role_summary: 10–2000 chars — the role's core function and impact.
  - Every signal carries: value, type, purpose, priority, weight, knockout, stage, source,
    inference_basis.
Return only the structured JSON. No preamble, no markdown.
```

- [ ] **Step 2: Smoke-test it loads**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; print(len(PromptLoader(version='v2').get('jd_signal_extraction')))"`
Expected: an integer length, no FileNotFoundError.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/prompts/v2/jd_signal_extraction.txt
git commit -m "feat(jd): v2 signal-extraction prompt — lean, consolidated, purpose-classified"
```

---

## Task 4: Extraction actor — use the versioned loader + stamp the version

**Files:**
- Modify: `app/modules/jd/actors.py`

- [ ] **Step 1: Load the extraction prompt via the versioned loader**

In `app/modules/jd/actors.py`, find `prompt = prompt_loader.get("jd_signal_extraction")` (~line 328) and replace with:
```python
    from app.ai.prompts import PromptLoader
    _extraction_loader = PromptLoader(version=ai_config.jd_signal_extraction_prompt_version)
    prompt = _extraction_loader.get("jd_signal_extraction")
```
(If a module-level versioned loader is cleaner, hoist `_extraction_loader` to module scope next to the existing `prompt_loader` import — match the file's style. The enrichment/reenrichment calls KEEP using the shared `prompt_loader`.)

- [ ] **Step 2: Stamp the configured version on the snapshot**

In `_persist_signal_snapshot` (~line 113-122), change the hardcoded `prompt_version="v1"` on the `JobPostingSignalSnapshot(...)` to:
```python
        prompt_version=ai_config.jd_signal_extraction_prompt_version,
```

- [ ] **Step 3: Stamp the version on the OTel span + metadata for the extraction call**

In the extraction call's `set_llm_span_attributes(...)` (~line 353) change `prompt_version="v1"` to `prompt_version=ai_config.jd_signal_extraction_prompt_version`, and likewise the `"prompt_version": "v1"` in that call's `metadata` dict (~line 375) to `ai_config.jd_signal_extraction_prompt_version`. Leave the enrichment (line 215) and reenrichment (line 763) `prompt_version="v1"` UNCHANGED — those prompts are still v1.

- [ ] **Step 4: Verify the actor imports + a JD unit test still pass**

Run: `docker compose run --rm nexus pytest tests/test_jd_actor.py -q 2>&1 | tail -8`
Expected: PASS (the extraction actor tests should mock the LLM; confirm the versioned loader doesn't break them — if a test asserts `prompt_version="v1"` on the snapshot, update it to expect `v2`, and report that change).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/jd/actors.py backend/nexus/tests/test_jd_actor.py
git commit -m "feat(jd): extraction reads versioned prompt + stamps v2 on the snapshot"
```

---

## Task 5: Recruiter-save accepts `purpose`

**Files:**
- Modify: the schema validating `PATCH /api/jobs/{id}/signals` (find it: `app/modules/jd/schemas.py` or `app/modules/jd/service.py::save_signals`).
- Test: `tests/test_jd_signals.py` (extend)

- [ ] **Step 1: Find the save-signals body schema**

Run: `cd /home/ishant/Projects/ProjectX/backend/nexus && grep -rn "save_signals\|SaveSignals\|def save_signals\|class .*Signal.*Body\|signals/" app/modules/jd/ | head`
Identify the Pydantic model the endpoint binds the incoming signals to (it may reuse `SignalItemV2`, or a separate `RecruiterSignalItem`/`SaveSignalsBody`).

- [ ] **Step 2: Write the failing test**

In `tests/test_jd_signals.py`, add a test that the save path accepts + round-trips a `purpose` field. Model it on the existing save-signals tests in that file (reuse their fixtures/client). The assertion: PATCH a signal with `purpose="eligibility"`, reload the snapshot, confirm the stored signal dict has `purpose == "eligibility"`. (If the existing tests build a signal dict helper, add `purpose` to it.)

- [ ] **Step 3: Run to verify it FAILS (if the schema rejects/strips purpose)**

Run: `docker compose run --rm nexus pytest tests/test_jd_signals.py -k purpose -q`
Expected: FAIL if the save schema doesn't include `purpose` (extra field ignored or rejected). If it already reuses `SignalItemV2` (which now has `purpose`), it may PASS immediately — in that case note it and skip Step 4.

- [ ] **Step 4: Add `purpose` to the save-signals body schema**

If the save body uses a model distinct from `SignalItemV2`, add the field mirroring Task 1:
```python
    purpose: Literal["skill", "eligibility"] = "skill"
```
(Use the same `SignalPurpose` import if convenient.)

- [ ] **Step 5: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_jd_signals.py -k purpose -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/jd/ backend/nexus/tests/test_jd_signals.py
git commit -m "feat(jd): recruiter signal-save accepts + persists purpose"
```

---

## Task 6: Frontend — `purpose` on the signal type + an editable toggle

**Files:**
- Modify: `frontend/app/lib/api/jobs.ts`, `frontend/app/components/dashboard/jd-panels/EditableSignalsPanel.tsx`, `frontend/app/stores/job-edit.ts`

- [ ] **Step 1: Add `purpose` to the `SignalItem` type**

In `frontend/app/lib/api/jobs.ts`, add a type + field on `SignalItem` (after `knockout`):
```typescript
export type SignalPurpose = 'skill' | 'eligibility'
```
```typescript
  purpose: SignalPurpose
```
(Add it to the `SignalItem` type definition at ~line 30-41.)

- [ ] **Step 2: Default `purpose` when the recruiter adds a new chip**

In `frontend/app/stores/job-edit.ts`, find the `addChip` `newItem` object (~line 74) and add:
```typescript
      purpose: 'skill',
```

- [ ] **Step 3: Add a purpose toggle in `EditableChipRow`**

In `frontend/app/components/dashboard/jd-panels/EditableSignalsPanel.tsx`, add a toggle next to the `stage`/`knockout` controls (follow the knockout toggle pattern, ~lines 139-150). A two-state button:
```tsx
<button
  type="button"
  onClick={() => updateSignal(realIndex, {
    purpose: item.purpose === 'eligibility' ? 'skill' : 'eligibility',
  })}
  className="rounded px-1.5 py-0.5 text-[10px] font-semibold"
  style={item.purpose === 'eligibility'
    ? { background: 'var(--px-zinc-100)', color: 'var(--px-zinc-600)' }
    : { background: 'var(--px-accent-tint)', color: 'var(--px-accent)' }}
  title="skill = tested in the AI screen; eligibility = recruiter pre-screened"
>
  {item.purpose === 'eligibility' ? 'ELIGIBILITY' : 'SKILL'}
</button>
```
(Match the exact styling tokens the sibling controls use — read them first; `var(--px-accent-tint)` exists per the theme.)

- [ ] **Step 4: Handle legacy signals missing `purpose`**

A snapshot generated before this change has signals without `purpose`. In `EditableChipRow` and any read that reads `item.purpose`, treat `undefined` as `'skill'`: use `(item.purpose ?? 'skill')` in the toggle's display + comparison so legacy rows render as SKILL. (TypeScript will require the field on the type; since the backend now always emits it and legacy snapshots are JSON without it, guard reads with `?? 'skill'`.)

- [ ] **Step 5: Type-check + build + test**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit && npm run lint && npm run build && npm run test 2>&1 | tail -15
```
Expected: type-check + build pass; tests green (update any test that constructs a `SignalItem` literal to include `purpose`, and report it).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/lib/api/jobs.ts frontend/app/components/dashboard/jd-panels/EditableSignalsPanel.tsx frontend/app/stores/job-edit.ts frontend/app/tests
git commit -m "feat(app): signal purpose (skill/eligibility) type + editable toggle"
```

---

## Task 7: Extraction prompt-quality eval

**Files:**
- Create: `tests/jd/__init__.py`, `tests/jd/prompt_evals/__init__.py`, `tests/jd/prompt_evals/test_signal_extraction_evals.py`

> Real-API, opt-in (`-m prompt_quality`). Write + verify it COLLECTS; do NOT run the real-API suite (the user runs it).

- [ ] **Step 1: Create the eval file**

```python
# tests/jd/prompt_evals/test_signal_extraction_evals.py
"""Signal-extraction prompt-quality eval (v2). Opt-in, real API.
Run: docker compose exec nexus pytest tests/jd/prompt_evals -m prompt_quality
"""
from __future__ import annotations
import dataclasses
import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.schemas import SignalExtractionOutput

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


@dataclasses.dataclass
class JDCase:
    id: str
    company: dict
    jd: str


WORKATO_JD = JDCase(
    id="workato_integration_engineer",
    company={"about": "Enterprise IT automation", "industry": "Technology", "hiring_bar": "high"},
    jd="""Job Title: AI Integration Engineer - Workato Specialist
Total Experience Required: 4+ years
Relevant Experience: At least 1 year hands-on with Workato
Must-Have Skills:
- Minimum 1 year hands-on with Workato
- AI engineering with a focus on agent-based systems
- Designing and implementing AI-driven workflows
- Integration project implementation
- APIs (RESTful, SOAP/XML) and data structures (JSON)
- Automation technologies and middleware
- At least one programming language: Java, Python, or Ruby
- RDBMS or NoSQL databases
Good-to-Have: TIBCO/Dell Boomi/MuleSoft; iPaaS/SaaS; Workday/NetSuite/Salesforce; microservices; BPM/RPA
Key Responsibilities: design AI-driven workflows; lead integration projects; collaborate cross-functionally;
develop APIs/connectors; monitor/troubleshoot/optimize; document; provide technical guidance; stay current.
Education: BTech/BE or higher in CS/AI/ML or related field; certifications a plus.""",
)

CASES = [WORKATO_JD]


async def _extract(case: JDCase) -> SignalExtractionOutput:
    loader = PromptLoader(version=ai_config.jd_signal_extraction_prompt_version)
    system = loader.get("jd_signal_extraction")
    user = (f"## Company Profile\n- About: {case.company['about']}\n"
            f"- Industry: {case.company['industry']}\n- Hiring bar: {case.company['hiring_bar']}\n\n"
            f"## Job Description\n\n{case.jd}\n")
    client = get_openai_client()
    kw = dict(model=ai_config.extraction_model, response_model=SignalExtractionOutput,
              messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
              max_retries=1)
    if ai_config.extraction_effort:
        kw["reasoning_effort"] = ai_config.extraction_effort
    return await client.chat.completions.create(**kw)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_extraction_is_lean(case):
    out = await _extract(case)
    n = len(out.signals.signals)
    assert n <= 13, f"[{case.id}] too many signals ({n}); should be lean (~8-10)"


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_eligibility_facts_classified_eligibility(case):
    out = await _extract(case)
    elig_vals = [s.value.lower() for s in out.signals.signals if s.purpose == "eligibility"]
    # years/degree/cert should be eligibility, not skill
    skill_vals = [s.value.lower() for s in out.signals.signals if s.purpose == "skill"]
    leak = [v for v in skill_vals if "year" in v or "btech" in v or "degree" in v or "certification" in v]
    assert not leak, f"[{case.id}] eligibility facts mis-classified as skill: {leak}"
    assert any("year" in v for v in elig_vals), f"[{case.id}] tenure not captured as eligibility"


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_has_a_skill_and_core_musthaves_weighted(case):
    out = await _extract(case)
    skills = [s for s in out.signals.signals if s.purpose == "skill"]
    assert skills, f"[{case.id}] no skill signals"
    # the core Workato/API/AI-workflow skills should be high weight
    core = [s for s in skills if any(k in s.value.lower() for k in ("workato", "api", "ai-driven", "agent", "integration"))]
    assert any(s.weight >= 2 for s in core), f"[{case.id}] core skills under-weighted"
```

- [ ] **Step 2: Verify it collects + fixtures import (no real API)**

Run: `docker compose run --rm nexus pytest tests/jd/prompt_evals --collect-only -q 2>&1 | tail -6`
Expected: tests collect, 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/tests/jd/
git commit -m "test(jd): signal-extraction prompt-quality eval (lean, purpose, must-weights)"
```

---

# PHASE 2 — BANK

## Task 8: Bank actor — filter eligibility signals out of `ai_screening` generation

**Files:**
- Modify: `app/modules/question_bank/actors.py`
- Test: `tests/test_question_banks_actors.py` (extend) or `tests/question_bank/test_eligibility_filter.py` (CREATE)

- [ ] **Step 1: Write the failing test (a pure filter helper)**

Add a small pure helper so the filter is unit-testable. Create `tests/question_bank/test_eligibility_filter.py`:
```python
from app.modules.question_bank.actors import _signals_for_generation


def _s(value, purpose="skill", type_="competency"):
    return {"value": value, "type": type_, "purpose": purpose, "weight": 3,
            "priority": "required", "knockout": False, "stage": "interview"}


def test_ai_screening_drops_eligibility():
    sigs = [_s("Workato"), _s("4+ years", purpose="eligibility", type_="experience"),
            _s("BTech", purpose="eligibility", type_="credential")]
    out = _signals_for_generation(sigs, stage_type="ai_screening")
    vals = [s["value"] for s in out]
    assert vals == ["Workato"]


def test_legacy_signals_without_purpose_default_skill():
    sigs = [{"value": "Workato", "type": "competency", "weight": 3,
             "priority": "required", "knockout": False, "stage": "interview"}]
    out = _signals_for_generation(sigs, stage_type="ai_screening")
    assert [s["value"] for s in out] == ["Workato"]


def test_phone_screen_keeps_eligibility():
    sigs = [_s("Workato"), _s("4+ years", purpose="eligibility", type_="experience")]
    out = _signals_for_generation(sigs, stage_type="phone_screen")
    assert len(out) == 2
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_eligibility_filter.py -q`
Expected: FAIL — `_signals_for_generation` undefined.

- [ ] **Step 3: Add the helper + wire it into `_generate_one_bank`**

In `app/modules/question_bank/actors.py`, add the pure helper (near `_build_user_message`):
```python
def _signals_for_generation(snapshot_signals: list[dict], *, stage_type: str) -> list[dict]:
    """The signals the bank generator sees. For an AI skills screen, eligibility signals
    (years/degree/cert — recruiter pre-screened) are excluded; the screen tests SKILLS.
    Legacy signals without a `purpose` default to skill (no regression)."""
    if stage_type != "ai_screening":
        return list(snapshot_signals)
    return [s for s in snapshot_signals if s.get("purpose", "skill") != "eligibility"]
```
In `_generate_one_bank` Phase A, capture `stage_type = stage.stage_type` (confirm it's captured — it's used for `prompt_name`). Then change the Phase B call from passing the full set to the filtered set:
```python
        await _stream_bank_questions(
            ...
            eligible_signals=_signals_for_generation(snapshot_signals, stage_type=stage_type),
            ...
        )
```
(`snapshot_signals` is already captured in Phase A. `stage_type` must be captured there too — add `stage_type = stage.stage_type` if not already.)

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_eligibility_filter.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the actor suite (no regression; remember the critic patch)**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -m "not prompt_quality" -q 2>&1 | tail -6`
Expected: PASS (the existing tests patch the critic + stream; the filter is a no-op for their non-ai_screening or skill-only fixtures — if a fixture used eligibility signals for an ai_screening bank and asserts on them, update it and report).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/actors.py backend/nexus/tests/question_bank/test_eligibility_filter.py
git commit -m "feat(question_bank): AI screening generates over skill signals only (drops eligibility)"
```

---

## Task 9: ai_screening recipe — scenario-primary, skills-only, lead-dedup

**Files:**
- Modify: `prompts/v3/question_bank_ai_screening.txt`

> Validated by the bank evals (Task 12). This task rewrites the recipe.

- [ ] **Step 1: Rewrite the authoring recipe to be scenario-primary + skills-only**

In `prompts/v3/question_bank_ai_screening.txt`, REPLACE the "Authoring recipe" section with a recipe that overrides the general guidance for THIS stage. The new recipe text:
```
# This stage is a TECHNICAL SKILLS TEST — author it that way

The recruiter has ALREADY pre-screened eligibility (years, degree, certifications). Your
only job here is to TEST whether the candidate can actually do the technical work. The
signals you are given are the SKILLS to assess (eligibility signals have been filtered out).

OVERRIDE the general seniority-format guidance for this stage: do NOT down-weight
situational/scenario questions — they are the PRIMARY instrument here. Make the candidate
REASON, design, debug, and trade things off aloud.

Authoring recipe — in order:

1. TECHNICAL SCENARIOS (the bulk). For each high-weight skill signal, author a
   `technical_scenario` that forces the candidate to USE the skill — design a workflow,
   debug a failing integration, transform a payload, reason about a data model under load,
   choose between approaches. NOT "have you used X" — make them DO X out loud. One self-
   contained spoken scenario per lead; depth ladders into the escalating follow-ups.
2. ONE PROJECT DEEP-DIVE. Exactly one `project_deepdive` over the candidate's most relevant
   real project — decisions they drove, what they chose it over, what broke, what they'd
   change. This naturally surfaces ownership and collaboration, so you usually need no
   separate behavioral question.
3. AT MOST ONE BEHAVIORAL. Only if a soft-skill signal (type=behavioral) is genuinely
   high-weight AND not already covered by the project deep-dive, author a single true STAR
   `behavioral`. Never more than one.

FORBIDDEN in this stage:
  - `experience_check` ("have you done X", "can you work with X") — replace every one with a
    scenario that makes them demonstrate it.
  - `compliance_binary` / education / tenure questions — eligibility is pre-screened and its
    signals are not even in your input.

Budget: fit the stage duration. A 20-minute screen is ~5–6 scenario questions at 3–4 min.
Fewer, deeper, skill-revealing questions beat a long shallow list. STOP when the high-weight
skill signals are covered by scenarios — do not pad.
```

- [ ] **Step 2: Strengthen distinctness to the LEAD level**

In the "Within-bank distinctness" section of the same file, add:
```
Distinctness applies to LEAD QUESTIONS, not just follow-up dimensions. No two leads may
probe the same underlying thing — in particular the project_deepdive and any behavioral or
scenario must not be two versions of "tell me about an integration you built end to end".
Each lead opens a DISTINCT skill or angle.
```

- [ ] **Step 3: Smoke-test it loads**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; print(len(PromptLoader(version='v3').load_pair('question_bank_common','question_bank_ai_screening')))"`
Expected: an integer, no error.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/prompts/v3/question_bank_ai_screening.txt
git commit -m "feat(question_bank): ai_screening recipe — scenario-primary skills test, lead-dedup"
```

---

## Task 10: Critic — enforce the skills-test shape + trim to budget

**Files:**
- Modify: `prompts/v3/question_bank_critic.txt`

- [ ] **Step 1: Update the critic checklist**

In `prompts/v3/question_bank_critic.txt`, REPLACE checklist items 1–3 and 8 area with skills-test-aware rules, and ADD a budget-trim rule. Specifically, change the checklist so it reads (keep the distinctness/anchor/tripwire items, renumber as needed):
```
1. SKILLS-ONLY, SCENARIO-PRIMARY (ai_screening). The bank must TEST skills: it is dominated
   by `technical_scenario` (make the candidate reason/design/debug) plus exactly ONE
   `project_deepdive`. There must be ZERO `experience_check`, `compliance_binary`, education,
   or tenure questions — rewrite any such question into a scenario that makes the candidate
   demonstrate the skill. At most ONE `behavioral`, only for a high-weight soft-skill signal.
2. COVERAGE over SKILL signals. Every high-weight skill signal is probed by a scenario. If a
   high-weight skill is uncovered, ADD a scenario for it (never an experience_check).
3. PROJECT DEEP-DIVE. Exactly one, with an escalating ladder (decision → alternative → what
   broke → what they'd change). Add or repair it.
4. LEAD-LEVEL DISTINCTNESS. No two LEAD questions probe the same underlying thing (not just
   follow-up dimensions). Merge or replace near-duplicate leads (e.g. a behavioral and the
   project_deepdive both about "an integration you built end to end").
5. ESCALATION + SPECIFICS — every seed_probe demands a falsifiable specific; ≥1 orthogonal rung.
6. ANCHOR SHARPNESS — every rubric band names observable spoken behavior.
7. BLUFFER TRIPWIRES — red_flags are content tells, not delivery cues.
8. FITS BUDGET — the sum of estimated_minutes must not exceed the stage duration. If it does,
   DROP the lowest-priority question(s) (lowest-weight skill, or a behavioral over a scenario)
   until it fits. Fewer, deeper questions are correct.
9. SPOKEN HYGIENE — each lead is ONE self-contained spoken ask, ≤240 chars.
```
(The critic's user message already includes the stage duration via the role/stage block — confirm `stage_duration` is passed to `run_bank_critic` (it is, from Task wiring); the trim rule needs it.)

- [ ] **Step 2: Smoke-test it loads**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; print(len(PromptLoader(version='v3').get('question_bank_critic')))"`
Expected: an integer.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/prompts/v3/question_bank_critic.txt
git commit -m "feat(question_bank): critic enforces skills-only scenario-primary + trim-to-budget"
```

---

## Task 11: Report — neutralize un-questioned eligibility signals + zero-knockout no-op

**Files:**
- Investigate: `app/modules/reporting/scoring/rollup.py`, `aggregate.py`, `constants.py`
- Modify (only if needed) + Test

- [ ] **Step 1: Trace how the report scores per-signal and handles uncovered signals**

Read `app/modules/reporting/scoring/rollup.py` + `aggregate.py`. Determine: (a) does the per-signal rollup score EVERY role signal, or only signals that had a `QuestionRecord`/evidence? (b) does an uncovered signal lower the score or get flagged as a gap? (c) does the knockout path assume ≥1 knockout question?

- [ ] **Step 2: If uncovered eligibility signals are penalized/flagged — neutralize them**

If (and only if) the rollup penalizes or gap-flags a signal that had no question: add a guard so `purpose == "eligibility"` signals with no evidence are SKIPPED (not scored, not flagged as a gap) — they're pre-screened, outside the AI screen's remit. Write a unit test in `tests/reporting/` first (failing), then add the guard, then green. If the rollup already only scores asked signals, this is a NO-OP — write a small test asserting an un-questioned eligibility signal doesn't appear as a gap, confirm it passes, and report "no change needed".

- [ ] **Step 3: Confirm the knockout path no-ops on zero knockouts**

Verify `brain/policy.gate_knockout` (engine) and the report's knockout-gating tolerate a bank with zero knockout/mandatory questions (the new common case for ai_screening). Add/confirm a test: a `SessionEvidence` with no `KnockoutOutcome` produces a normal verdict (not an error, not an auto-fail). If a test already covers this, reference it; otherwise add one.

- [ ] **Step 4: Run the reporting suite**

Run: `docker compose run --rm nexus pytest tests/reporting -m "not prompt_quality" -q 2>&1 | tail -8`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/reporting/ backend/nexus/tests/reporting/
git commit -m "test(reporting): eligibility signals + zero-knockout banks handled gracefully"
```

---

## Task 12: Bank prompt-quality evals — skills-test shape

**Files:**
- Modify: `tests/question_bank/prompt_evals/test_bank_gen_evals.py`

> Real-API, opt-in. Write + verify collection; do NOT run the real-API suite.

- [ ] **Step 1: Add a skills-test eval over a purpose-classified signal set**

Append to `test_bank_gen_evals.py` a case + tests. Add a `BankGenCase` whose signals carry `purpose` (skills + a couple eligibility), feed them through the existing `_generate` path with the ai_screening prompt, and assert the skills-test shape:
```python
_SKILLS_CASE = BankGenCase(
    id="skills_test_workato",
    role_title="AI Integration Engineer (Workato)",
    seniority="mid",
    company_profile={"about": "Enterprise automation", "industry": "Technology", "hiring_bar": "high"},
    signals=[
        _mk_signal("Workato recipe/workflow development", weight=3),
        _mk_signal("API integration & data transformation (REST/SOAP, JSON)", weight=3),
        _mk_signal("AI-driven / agent-based workflow design", weight=3),
        _mk_signal("RDBMS or NoSQL data reasoning", weight=2),
        _mk_signal("Integration project ownership", sig_type="experience", weight=2),
    ],
    stage_duration=20,
    stage_difficulty="hard",
)


async def test_ai_screen_is_scenario_dominant_and_skills_only():
    qs = await _generate(_SKILLS_CASE)
    kinds = [q.question_kind for q in qs]
    assert "experience_check" not in kinds, f"claim-check leaked: {kinds}"
    assert "compliance_binary" not in kinds, f"compliance leaked: {kinds}"
    scenario_like = sum(1 for k in kinds if k in ("technical_scenario", "project_deepdive"))
    assert scenario_like / len(kinds) >= 0.7, f"not scenario-dominant: {kinds}"
    assert kinds.count("behavioral") <= 1, f"too many behavioral: {kinds}"
    assert kinds.count("project_deepdive") == 1, f"need exactly one deepdive: {kinds}"


async def test_ai_screen_fits_budget():
    qs = await _generate(_SKILLS_CASE)
    total = sum(float(q.estimated_minutes) for q in qs)
    assert total <= _SKILLS_CASE.stage_duration, f"over budget: {total} > {_SKILLS_CASE.stage_duration}"
```
(Confirm `_mk_signal`/`BankGenCase`/`_generate` signatures match the current file; adapt. `_mk_signal` may need a `purpose` kwarg added to its helper — if so, add `purpose="skill"` default to `_mk_signal` and pass `purpose="eligibility"` where needed. The eval's `_build_user_message` must include `purpose` per signal and the generation path must apply the eligibility filter — if `_generate` bypasses the actor filter, filter the case's signals to skill-only before building, matching production.)

- [ ] **Step 2: Verify collection (no real API)**

Run: `docker compose run --rm nexus pytest tests/question_bank/prompt_evals --collect-only -q 2>&1 | tail -6`
Expected: collects, 0 errors. Construct the new case's fixtures via a quick bootstrap check (`import app.main` first) to confirm validity.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/tests/question_bank/prompt_evals/test_bank_gen_evals.py
git commit -m "test(question_bank): ai_screening skills-test evals (scenario-dominant, fits budget)"
```

---

## Task 13: Full-suite verification + live smoke

**Files:** none (verification).

- [ ] **Step 1: Backend default gate green**

Run: `docker compose run --rm nexus pytest tests/jd tests/test_jd_actor.py tests/test_jd_signals.py tests/question_bank tests/test_question_banks_actors.py tests/test_question_banks_integration.py tests/reporting -m "not prompt_quality" -q 2>&1 | tail -8`
Expected: 0 failed.

- [ ] **Step 2: Frontend gate**

Run: `cd /home/ishant/Projects/ProjectX/frontend/app && npx tsc --noEmit && npm run build 2>&1 | tail -5`
Expected: pass.

- [ ] **Step 3: Restart worker (no hot-reload)**

Run: `docker compose up -d --force-recreate nexus-worker`

- [ ] **Step 4: Live smoke (user-run)**

Re-extract signals for the Workato JD (`/jobs/ce6dad9a…?tab=jd`): confirm ~8–10 signals, years/degree/cert tagged `eligibility`, core skills `purpose=skill` at weight ≥2, consolidated API signal. Then regenerate the bank: confirm it is scenario-dominant, ≤20 min, has no education/tenure/claim-check questions, no duplicate leads, ≤1 behavioral.

---

## Self-Review (plan vs spec)

- **Spec §2 (purpose field, no migration, ≥1 skill validator, default skill)** → Task 1. ✓
- **Spec §3 (extraction v2: lean, consolidate, purpose, accurate Must, split tenure/skill, versioned + provenance)** → Tasks 2–4, 7. ✓
- **Spec §4 (ai_screening recipe: skill-only, scenario-primary, drop experience_check/compliance, ≤1 behavioral, lead-dedup, trim-to-budget; critic)** → Tasks 8–10, 12. ✓
- **Spec §5 (report tolerance, knockout no-op, engine tolerance, recruiter UI, starter_pack unchanged)** → Tasks 5, 6, 11. ✓
- **Spec §6 (two plans back-to-back)** → this single doc, Phase 1 then Phase 2. ✓
- **Spec §7 (testing)** → Tasks 1,2,5,8 unit; 7,12 prompt-quality; 11 reporting; 13 live smoke. ✓

**Type consistency:** `purpose: Literal["skill","eligibility"]` default `"skill"` is identical in schema (T1), save body (T5), bank filter (`s.get("purpose","skill")`, T8), frontend (`SignalPurpose`, T6). `_signals_for_generation(snapshot_signals, *, stage_type)` defined T8, called T8. `jd_signal_extraction_prompt_version` consistent across config (T2), actor (T4), evals (T7). No dangling references.
