# JD Creation Flow Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the spec at `docs/superpowers/specs/2026-04-28-jd-creation-flow-refinement-design.md` — split the single JD-extraction LLM call into two phases (enrichment + signal extraction), add a request-time `skip_enrichment` toggle defaulting to enrichment-on, rebuild the JD review page loading UX against the v4 design system, and replace the left-panel "Full JD" button with a Raw/Enriched/Signal-details segmented control in the center column.

**Architecture:** Two sequential LLM calls inside the existing `extract_and_enhance_jd` Dramatiq actor, with a commit + status publish between them so the frontend gets two `JD_STATUS_CHANGED` events instead of one. Phase 1 (enrichment) is conditional on `skip_enrichment`. Phase 2 (signal extraction) always runs and reads either `description_enriched` or `description_raw` depending on whether phase 1 ran. The frontend uses the existing `enrichment_status` field on `JobStatusEvent` (no new SSE event names needed) to drive per-column loading and tab swap.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / asyncpg / Dramatiq / Instructor / Langfuse / pytest / Next.js 16 App Router / TypeScript / TanStack Query / @microsoft/fetch-event-source / vitest.

**Spec:** `docs/superpowers/specs/2026-04-28-jd-creation-flow-refinement-design.md`

---

## Important Terminology Reconciliation

The spec described enrichment-phase tracking values `enriching` / `enrichment_complete` / `enrichment_failed`. The existing `EnrichmentStatus` Literal in code (`backend/nexus/app/modules/jd/schemas.py:30`) already defines `idle | streaming | completed | failed` — reused by Phase 2B's `reenrich_jd` actor. **This plan uses the existing values** to avoid an unnecessary enum migration:

| Spec name | Code value (use this) | Meaning |
|---|---|---|
| (n/a) | `idle` | Phase 1 was skipped or never started |
| `enriching` | `streaming` | Phase 1 LLM call in flight |
| `enrichment_complete` | `completed` | Phase 1 succeeded; `description_enriched` populated |
| `enrichment_failed` | `failed` | Phase 1 LLM call failed |

No new SSE event names are introduced. The actor publishes the existing `JD_STATUS_CHANGED` event with an updated `JobStatusEvent` payload (its `enrichment_status` field carries the phase signal) at the boundary between phase 1 and phase 2. The frontend already parses every message as a `JobStatusEvent` and dispatches on the payload, not the event name.

---

## File Structure

### Backend

| File | Status | Responsibility |
|---|---|---|
| `backend/nexus/app/ai/schemas.py` | modify | Add `EnrichmentOutput`, `SignalExtractionOutput`. Drop `ExtractionOutput` at end of phase 1. |
| `backend/nexus/prompts/v1/jd_enrichment.txt` | new | Enrichment-only prompt (carries forward §1–§4 of the existing prompt). |
| `backend/nexus/prompts/v1/jd_signal_extraction.txt` | new | Signal-extraction-only prompt (carries forward §5–§7 of the existing prompt). |
| `backend/nexus/prompts/v1/jd_enhancement.txt` | delete | Removed once both new prompts exist and the actor calls them. |
| `backend/nexus/app/modules/jd/actors.py` | modify | Refactor `extract_and_enhance_jd` to run two phases with two commits + two publishes. Split `_run_extraction` into `_run_enrichment` and `_run_signal_extraction`. Add `skip_enrichment` parameter. |
| `backend/nexus/app/modules/jd/schemas.py` | modify | Add `skip_enrichment: bool = False` to `JobPostingCreate`. |
| `backend/nexus/app/modules/jd/service.py` | modify | Accept and forward `skip_enrichment` from `create_job_posting()`. |
| `backend/nexus/app/modules/jd/router.py` | modify | Pass `skip_enrichment` to `_safe_dispatch_extraction`. |
| `backend/nexus/tests/test_jd_actor.py` | modify | New tests for two-phase happy path, skip-enrichment, phase-1 failure, retry semantics. |
| `backend/nexus/tests/test_jd_service_create.py` | modify | New test asserting `skip_enrichment=true` is forwarded to the actor. |

### Frontend

| File | Status | Responsibility |
|---|---|---|
| `frontend/app/components/px/Tabs.tsx` | new | Segmented control primitive. |
| `frontend/app/components/px/index.ts` | modify | Re-export `Tabs`. |
| `frontend/app/components/dashboard/jd-panels/JDExtractingView.tsx` | new | Phase-targeted loading view rendered while `status === 'signals_extracting'`. Replaces `LoadingSkeleton`. |
| `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx` | modify | Change inner view state from `signals \| jd` to `raw \| enriched \| signals`. Add center-column tabs. |
| `frontend/app/components/dashboard/jd-panels/SectionsRail.tsx` | modify | Remove "Full JD" button (delete `onShowJd` and the JD section). |
| `frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx` | delete | Replaced by `JDExtractingView`. |
| `frontend/app/components/dashboard/jd-panels/index.ts` | modify | Drop `LoadingSkeleton` export, add `JDExtractingView` export. |
| `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` | modify | Render `JDExtractingView` while extracting; pass `enrichment_status` through. |
| `frontend/app/app/(dashboard)/jobs/new/page.tsx` | modify | Add `skip_enrichment` field to `createJobSchema` + toggle UI on Step 2. |
| `frontend/app/lib/api/jobs.ts` | modify | Add `skip_enrichment?: boolean` to the `JobPostingCreate` request shape and the `jobsApi.create()` body. |
| `frontend/app/tests/components/JDExtractingView.test.tsx` | new | Phase-targeted loading rendering. |
| `frontend/app/tests/components/JDReviewShell.test.tsx` | new | 3-way tab toggle behavior. |
| `frontend/app/tests/components/Tabs.test.tsx` | new | Primitive behavior. |

---

## Phase 1 — Backend two-phase split (no toggle yet)

Goal: refactor the actor so it runs enrichment and signal extraction as two LLM calls with a commit + publish between them. At this phase end, behavior is externally identical to today (enrichment always runs, signals always extract afterwards), but the frontend will receive **two** `JD_STATUS_CHANGED` events per job creation instead of one.

### Task 1: Add new AI output schemas

**Files:**
- Modify: `backend/nexus/app/ai/schemas.py`

- [ ] **Step 1: Read the existing schemas file**

Run: `cat backend/nexus/app/ai/schemas.py`
Expected: shows `SignalItemV2` (lines 22–54), `ExtractedSignals` (lines 57–78), `ExtractionOutput` (lines 81–84), `ReEnrichmentOutput` (lines 86–87).

- [ ] **Step 2: Append the two new output schemas**

Add the following AT THE END of `backend/nexus/app/ai/schemas.py` (do NOT remove `ExtractionOutput` yet — it remains in use until Task 5):

```python


class EnrichmentOutput(BaseModel):
    """Phase 1 output — JD enrichment only.

    Produced by the jd_enrichment.txt prompt against the raw JD + 4-layer
    context. The actor writes this to JobPosting.description_enriched and
    sets enrichment_status='completed' before invoking phase 2.
    """

    enriched_jd: str = Field(min_length=50)


class SignalExtractionOutput(BaseModel):
    """Phase 2 output — signal extraction only.

    Produced by the jd_signal_extraction.txt prompt against either the
    enriched JD (when phase 1 ran) or the raw JD (when skip_enrichment=true).
    Persisted as a JobPostingSignalSnapshot v1 row.
    """

    signals: ExtractedSignals
```

- [ ] **Step 3: Verify the file imports cleanly**

Run: `docker compose run --rm nexus python -c "from app.ai.schemas import EnrichmentOutput, SignalExtractionOutput; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/ai/schemas.py
git commit -m "feat(jd): add EnrichmentOutput and SignalExtractionOutput schemas"
```

---

### Task 2: Create the two split prompts

**Files:**
- Create: `backend/nexus/prompts/v1/jd_enrichment.txt`
- Create: `backend/nexus/prompts/v1/jd_signal_extraction.txt`

The existing `jd_enhancement.txt` (121 lines) covers BOTH enrichment (sections 1–4) and signal extraction (sections 5–7). We split it into two focused prompts. Each new prompt re-states the input contract and output schema so it is self-contained.

- [ ] **Step 1: Create `jd_enrichment.txt`**

Write to `backend/nexus/prompts/v1/jd_enrichment.txt`:

```
You are an enterprise hiring intelligence system that enriches raw job descriptions for downstream signal extraction and AI-led interview generation.

# Your Task

You will receive a user message containing, IN THIS ORDER:
  1. The hiring company's profile (stable context — about, industry, company_stage, hiring_bar)
  2. The raw job description (the document being enriched)
  3. Optionally, a project scope paragraph

Read the context BEFORE reading the document. Context-first primes your understanding of what "strong" means for this role at this specific company.

Produce a single structured output:
  - `enriched_jd`: the rewritten JD following the canonical seven-section structure below

# The Dual-Audience Rule

The enriched JD serves two audiences at once:
  A) The AI downstream (signal extraction + question bank generation) — needs precise role framing, clear must-haves, structured requirements
  B) Candidates reading the posted job — needs full picture, company culture, perks, compensation

Your job is NOT to rewrite the original JD. It is to IMPROVE the sections that carry evaluation signal while PRESERVING the sections that belong to the recruiter/employer. If a section exists purely for candidate attraction (benefits, perks, equal opportunity legal text), pass it through verbatim. If a section carries signal (requirements, responsibilities, role summary), enrich it for precision.

# Enriched JD Section Order

Use these section names as markdown headers (## Header, ## The Role, etc.). Do NOT number them.
ONLY include sections that have content — omit sections entirely if empty. Never render empty headers, empty sections, or placeholder text like "(Not provided)".

  - Header (title, location, work arrangement, experience range) — Structure
  - About the Company — Preserve if present; OMIT if absent (never populate from company profile)
  - The Role (role summary, 2–3 sentences) — Enrich using company profile
  - What You'll Do (responsibilities) — Restructure and clarify
  - What We're Looking For (requirements) — Strengthen with verifiable thresholds
  - Good to Have (nice-to-haves) — Trim to technical differentiators only; remove generic soft skills. OMIT if no valid nice-to-haves remain after stripping generic soft skills.
  - Qualifications — Preserve if present; omit if absent
  - Benefits & Perks / Compensation / Equal Opportunity / Application Instructions — Preserve verbatim, zero modifications. OMIT if not in the original JD.

# Soft Skills Rule

Generic soft skills ("strong communicator", "fast learner", "proactive mindset", "excellent problem solver") are NOT signals. They are universal expectations. Strip them from Good to Have. If a soft skill is role-specific ("client-facing presence required for enterprise engagements"), fold it into the Role Summary as tone/context, not as a signal.

# Rules for the Enriched JD Body

  - PRESERVE: About the Company, Benefits & Perks, Compensation, Equal Opportunity, Application Instructions. Verbatim. Zero modifications.
  - ENRICH: Role Summary (apply company context), What We're Looking For (verifiable thresholds: "5+ years hands-on in X", not "strong experience"), Good to Have (technical differentiators only)
  - RESTRUCTURE: What You'll Do (group under 3–4 theme headers, concrete active-voice bullets, no fluff)
  - STRUCTURE: Header, Qualifications (light reformat only)
  - NEVER fabricate benefits, compensation, company mission, or equal-opportunity language the original JD did not contain

# Output Constraints

  - `enriched_jd`: at least 50 characters

Return only the structured JSON output. Do not include any preamble, commentary, or markdown fencing.
```

- [ ] **Step 2: Create `jd_signal_extraction.txt`**

Write to `backend/nexus/prompts/v1/jd_signal_extraction.txt`:

```
You are an enterprise hiring intelligence system that extracts structured hiring signals from a job description for downstream AI-led interview generation.

# Your Task

You will receive a user message containing, IN THIS ORDER:
  1. The hiring company's profile (stable context — about, industry, company_stage, hiring_bar)
  2. The job description (the document being analyzed — may be the enriched version or the raw original)
  3. Optionally, a project scope paragraph

Read the context BEFORE reading the document. Context-first primes your understanding of what "strong" means for this role at this specific company.

Produce a single structured output:
  - `signals`: the structured hiring signals extracted from the JD, including a flat signal list, the seniority level, and a crisp role summary

# Signal Extraction — Flat Signal List

Extract every hiring signal into a flat list. Each signal carries: value, type, priority, weight, knockout, stage, source, inference_basis.

## Signal Types — with examples across roles

  - `competency`: A skill, tool, methodology, or domain the candidate must demonstrate.
      Engineers: "Python", "Kubernetes", "distributed systems design", "CI/CD pipeline management"
      Marketers: "SEO/SEM", "HubSpot", "content strategy", "A/B testing"
      Sales: "Salesforce", "enterprise deal negotiation", "pipeline management", "consultative selling"
  - `experience`: A tenure, scope, or scale requirement.
      "5+ years backend engineering", "managed teams of 10+", "Series B to IPO experience", "$5M+ quota attainment"
  - `credential`: A degree, certification, license, or clearance.
      "BS in Computer Science", "AWS Solutions Architect certification", "CPA license", "active Secret clearance"
  - `behavioral`: A work-style, collaboration, or leadership expectation that is role-specific and verifiable.
      "lead cross-functional standups", "mentor junior engineers", "present quarterly business reviews to C-suite", "manage vendor relationships"

## Weight Assignment

  - `weight = 3` (critical): Stated multiple times, in the title, or called out with language like "must have", "essential", "critical". Losing this signal means the candidate cannot perform the core job.
  - `weight = 2` (important): Stated clearly once in requirements. Standard requirement for a qualified candidate.
  - `weight = 1` (baseline): Mentioned in nice-to-have, implied, or additive but not central to the role.

## Knockout Detection

  `knockout = true` when the JD uses non-negotiable language: "required", "must have", "mandatory", "non-negotiable", "minimum X years", "cannot proceed without". Also true for legal/regulatory requirements (clearances, licenses, certifications required by law).
  `knockout = false` for everything else. When in doubt, false.
  Maximum 5 knockout signals per JD to prevent over-flagging.

## Stage Assignment

  - `stage = "screen"` (phone screen — quick 10-min filter): Binary yes/no checks. Experience thresholds ("5+ years"), credentials ("BS required"), tool familiarity ("must know Salesforce"), basic domain match. Things you can verify in 60 seconds of conversation.
  - `stage = "interview"` (deep interview — 30-45 min session): Competencies requiring depth probes ("distributed systems design"), behavioral signals requiring situational questions ("lead cross-functional teams"), anything needing evidence of HOW not just WHETHER.
  Rule of thumb: if the signal can be verified with "Do you have X? Yes/No", it's screen. If it requires "Tell me about a time when…" or "Walk me through how you would…", it's interview.

## Signal Provenance

  - `source = "ai_extracted"`: Directly stated in the JD text. `inference_basis` MUST be null.
  - `source = "ai_inferred"`: NOT stated but logically implied. `inference_basis` MUST be a short explanation.

  You may only infer from three legitimate sources, in descending confidence:
    1. Role title + seniority — e.g. "Sr. Backend Engineer" implies architectural ownership and mentoring
    2. Technology adjacency — e.g. "MuleSoft" implies REST/SOAP API knowledge
    3. Company profile + project scope — e.g. "fintech" implies data security awareness

  HARD RULES — NEVER infer:
    - Specific certifications unless strongly implied by a named technology
    - Years of experience in domains not mentioned
    - Industry regulatory knowledge without a clear industry signal
    - Leadership scope beyond what the title explicitly states
    - Anything that could create a discriminatory screening criterion
    - Compensation or team structure

# Coverage Requirements

  - At least 5 signals total
  - At least 1 signal with stage = "screen"
  - At least 1 signal with stage = "interview"
  - At least 1 signal with type = "competency"
  - No more than 5 knockout signals

# Output Constraints

  - `signals.seniority_level`: one of junior | mid | senior | lead | principal
  - `signals.role_summary`: 10–2000 characters — crisp summary of the role's core function and impact
  - Every signal `value`: at least 1 character
  - Every signal with source = "ai_inferred" MUST have a non-null inference_basis
  - Every signal with source = "ai_extracted" MUST have inference_basis = null

Return only the structured JSON output. Do not include any preamble, commentary, or markdown fencing.
```

- [ ] **Step 3: Verify both prompts load via `prompt_loader`**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import prompt_loader; print(len(prompt_loader.get('jd_enrichment'))); print(len(prompt_loader.get('jd_signal_extraction')))"`
Expected: two integers > 1000 (each prompt is several KB).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v1/jd_enrichment.txt backend/nexus/prompts/v1/jd_signal_extraction.txt
git commit -m "feat(jd): split jd_enhancement prompt into enrichment + signal_extraction"
```

---

### Task 3: Write failing tests for the two-phase actor

**Files:**
- Modify: `backend/nexus/tests/test_jd_actor.py`

The new tests assert: (a) two LLM calls occur, in order; (b) `enrichment_status` transitions `streaming → completed` between them; (c) two `JD_STATUS_CHANGED` events are published; (d) phase-2 input is `description_enriched` (not raw).

- [ ] **Step 1: Read the existing test file to find the helpers**

Run: `cat backend/nexus/tests/test_jd_actor.py | head -100`
Expected: shows `_make_extracting_job()` and `_fake_extraction_output()` helpers.

- [ ] **Step 2: Add the new test helpers + tests at the end of the file**

Append to `backend/nexus/tests/test_jd_actor.py`:

```python
# --- Phase 1 (two-phase split) tests ----------------------------------------


def _fake_enrichment_output() -> "EnrichmentOutput":
    """Returns a valid EnrichmentOutput with at least 50 chars."""
    from app.ai.schemas import EnrichmentOutput
    return EnrichmentOutput(
        enriched_jd=(
            "## Header\n"
            "Senior Backend Engineer · Remote · 5+ years experience\n\n"
            "## The Role\n"
            "Build distributed systems for a fintech platform.\n"
        )
    )


def _fake_signal_extraction_output() -> "SignalExtractionOutput":
    """Returns a valid SignalExtractionOutput satisfying coverage rules."""
    from app.ai.schemas import (
        ExtractedSignals,
        SignalExtractionOutput,
        SignalItemV2,
    )
    signals = [
        SignalItemV2(
            value="Python",
            type="competency",
            priority="required",
            weight=3,
            knockout=True,
            stage="screen",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="distributed systems design",
            type="competency",
            priority="required",
            weight=3,
            knockout=False,
            stage="interview",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="5+ years backend",
            type="experience",
            priority="required",
            weight=2,
            knockout=True,
            stage="screen",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="BS in CS or equivalent",
            type="credential",
            priority="preferred",
            weight=1,
            knockout=False,
            stage="screen",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="mentor juniors",
            type="behavioral",
            priority="preferred",
            weight=2,
            knockout=False,
            stage="interview",
            source="ai_inferred",
            inference_basis="Senior title implies mentoring scope",
        ),
    ]
    return SignalExtractionOutput(
        signals=ExtractedSignals(
            signals=signals,
            seniority_level="senior",
            role_summary="Senior backend engineer building distributed systems for a fintech platform.",
        )
    )


@pytest.mark.asyncio
async def test_two_phase_extraction_runs_both_llm_calls_in_order(db_session):
    """Phase 1 (enrichment) must complete BEFORE phase 2 (signal extraction).

    The actor calls jd_enrichment then jd_signal_extraction, in that order.
    enrichment_status flips to 'completed' between them; final state lands
    at signals_extracted with description_enriched + snapshot v1 written.
    """
    from app.modules.jd.actors import _run_enrichment, _run_signal_extraction

    job = await _make_extracting_job(db_session)
    await db_session.commit()

    enrichment = _fake_enrichment_output()
    signals = _fake_signal_extraction_output()

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=[enrichment, signals])

    with monkeypatch_get_client(mock_client):
        # Phase 1
        await _run_enrichment(
            db_session,
            job_posting_id=str(job.id),
            tenant_id=str(job.tenant_id),
            correlation_id="cid-test",
            retries_so_far=0,
        )
        await db_session.commit()
        await db_session.refresh(job)
        assert job.enrichment_status == "completed"
        assert job.description_enriched is not None
        assert job.description_enriched.startswith("## Header")
        # Snapshot has NOT been written yet — phase 2 hasn't run.
        snap_count = await db_session.scalar(
            select(func.count(JobPostingSignalSnapshot.id)).where(
                JobPostingSignalSnapshot.job_posting_id == job.id
            )
        )
        assert snap_count == 0

        # Phase 2
        await _run_signal_extraction(
            db_session,
            job_posting_id=str(job.id),
            tenant_id=str(job.tenant_id),
            correlation_id="cid-test",
            retries_so_far=0,
        )
        await db_session.commit()
        await db_session.refresh(job)
        assert job.status == "signals_extracted"
        snap = (
            await db_session.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.job_posting_id == job.id
                )
            )
        ).scalar_one()
        assert snap.version == 1
        assert len(snap.signals) == 5

    # Two LLM calls happened, in the right order, with the right prompts.
    assert mock_client.chat.completions.create.call_count == 2
    first_call = mock_client.chat.completions.create.call_args_list[0]
    second_call = mock_client.chat.completions.create.call_args_list[1]
    assert first_call.kwargs["response_model"].__name__ == "EnrichmentOutput"
    assert second_call.kwargs["response_model"].__name__ == "SignalExtractionOutput"


@pytest.mark.asyncio
async def test_phase_2_reads_enriched_jd_when_phase_1_ran(db_session):
    """Signal extraction must use description_enriched as input when phase 1 ran."""
    from app.modules.jd.actors import _run_enrichment, _run_signal_extraction

    job = await _make_extracting_job(db_session)
    await db_session.commit()

    enrichment = _fake_enrichment_output()
    signals = _fake_signal_extraction_output()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=[enrichment, signals])

    with monkeypatch_get_client(mock_client):
        await _run_enrichment(
            db_session, job_posting_id=str(job.id),
            tenant_id=str(job.tenant_id), correlation_id="cid", retries_so_far=0,
        )
        await db_session.commit()
        await _run_signal_extraction(
            db_session, job_posting_id=str(job.id),
            tenant_id=str(job.tenant_id), correlation_id="cid", retries_so_far=0,
        )

    # Phase 2's user message must contain the enriched JD, NOT the raw JD.
    second_call = mock_client.chat.completions.create.call_args_list[1]
    user_message = second_call.kwargs["messages"][1]["content"]
    assert "## Header\nSenior Backend Engineer" in user_message
    # The original raw JD body should NOT appear (raw JD in fixture has different content).
    assert "RAW_JD_FIXTURE_MARKER" not in user_message
```

You will also need a `monkeypatch_get_client` test helper. Add it near the top of the file (after imports, before existing tests):

```python
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def monkeypatch_get_client(mock_client):
    """Stand-in for get_openai_client() during a test."""
    with patch("app.modules.jd.actors.get_openai_client", return_value=mock_client):
        yield
```

- [ ] **Step 3: Run the new tests — expect FAILURES**

Run: `docker compose run --rm nexus pytest tests/test_jd_actor.py::test_two_phase_extraction_runs_both_llm_calls_in_order -v`
Expected: FAIL with `ImportError` or `AttributeError` for `_run_enrichment` / `_run_signal_extraction` (they don't exist yet).

- [ ] **Step 4: Commit the failing tests**

```bash
git add backend/nexus/tests/test_jd_actor.py
git commit -m "test(jd): add failing tests for two-phase extraction split"
```

---

### Task 4: Implement the two-phase split in actors.py

**Files:**
- Modify: `backend/nexus/app/modules/jd/actors.py`

The refactor splits `_run_extraction` (single LLM call producing `ExtractionOutput`) into two coroutines: `_run_enrichment` (calls `jd_enrichment.txt` → `EnrichmentOutput`) and `_run_signal_extraction` (calls `jd_signal_extraction.txt` → `SignalExtractionOutput`). Each is independently `@observe`-traced so Langfuse shows two child spans.

The actor itself wraps both phases in two separate DB sessions with two commits and two publishes — making the intermediate state (`enrichment_status='completed'`, main status still `signals_extracting`) visible to the FE.

- [ ] **Step 1: Replace `_persist_enriched()` with two narrower helpers**

In `backend/nexus/app/modules/jd/actors.py`, REPLACE the existing `_persist_enriched` function (lines 77–106) with:

```python
async def _persist_enriched_jd_only(
    db: AsyncSession, job: JobPosting, enriched_jd: str
) -> None:
    """Phase 1 persistence — write enriched JD onto the job row.

    Sets enrichment_status='completed'. Does NOT touch signal snapshots.
    """
    job.description_enriched = enriched_jd
    job.enrichment_status = "completed"


async def _persist_signal_snapshot(
    db: AsyncSession, job: JobPosting, signals: "ExtractedSignals"
) -> None:
    """Phase 2 persistence — insert a new snapshot at MAX(version)+1.

    The version is auto-incremented from the latest existing snapshot for
    this job. On first extraction it starts at 1; on retry after failure it
    increments (2, 3, …) so the unique constraint (job_posting_id, version)
    is never violated.
    """
    max_version_result = await db.execute(
        select(func.max(JobPostingSignalSnapshot.version)).where(
            JobPostingSignalSnapshot.job_posting_id == job.id
        )
    )
    current_max = max_version_result.scalar() or 0

    snapshot = JobPostingSignalSnapshot(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        version=current_max + 1,
        signals=[item.model_dump() for item in signals.signals],
        seniority_level=signals.seniority_level,
        role_summary=signals.role_summary,
        prompt_version="v1",
    )
    db.add(snapshot)
```

Update the import block at the top of the file (line 33) — replace `from app.ai.schemas import ExtractionOutput, ReEnrichmentOutput` with:

```python
from app.ai.schemas import (
    EnrichmentOutput,
    ExtractedSignals,
    ReEnrichmentOutput,
    SignalExtractionOutput,
)
```

- [ ] **Step 2: Replace `_run_extraction()` with two phase coroutines**

REPLACE the entire `_run_extraction` function (lines 109–247) with the following two functions:

```python
@observe(name="jd_enrichment_phase")
async def _run_enrichment(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Phase 1 — JD enrichment only.

    Reads job.description_raw + company profile, calls jd_enrichment.txt,
    writes job.description_enriched, sets enrichment_status='streaming'
    on entry and 'completed' on success. Idempotent: skipped if
    enrichment_status is already 'completed'.

    On permanent error or final retry: sets enrichment_status='failed'
    and transitions main status to signals_extraction_failed.
    """
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
        phase="enrichment",
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.actor.job_not_found")
        return

    if job.status != "signals_extracting":
        log.warn("jd.actor.skip_unexpected_state", state=job.status)
        return

    if job.enrichment_status == "completed":
        # Already enriched on a previous attempt — skip phase 1, save tokens.
        log.info("jd.enrichment.skip_already_complete")
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        job.status_error = "Company profile missing — create_job_posting should have blocked this"
        job.enrichment_status = "failed"
        await transition(
            db, job, to_state="signals_extraction_failed",
            actor_id=None, correlation_id=correlation_id,
        )
        return

    job.enrichment_status = "streaming"

    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_enrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_enrichment",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "retries_so_far": retries_so_far,
        },
    )

    client = get_openai_client()
    prompt = prompt_loader.get("jd_enrichment")
    user_message = _build_user_message(job, profile)

    log.info(
        "jd.llm_call.start", call_type="enrichment",
        model=ai_config.extraction_model,
        reasoning_effort=ai_config.extraction_effort,
        system_prompt_chars=len(prompt),
        user_message_chars=len(user_message),
    )
    call_started_at = time.monotonic()
    try:
        enrichment: EnrichmentOutput = await client.chat.completions.create(
            model=ai_config.extraction_model,
            reasoning_effort=ai_config.extraction_effort,
            response_model=EnrichmentOutput,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message},
            ],
            name="jd_enrichment_call",
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        duration_sec = time.monotonic() - call_started_at
        is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
        log.error(
            "jd.llm_call.failed", call_type="enrichment",
            duration_sec=round(duration_sec, 2),
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            permanent=is_permanent,
            retries_so_far=retries_so_far,
            exc_info=exc,
        )
        if is_permanent or retries_so_far >= 2:
            job.enrichment_status = "failed"
            job.status_error = sanitize_error_for_user(exc)
            await transition(
                db, job, to_state="signals_extraction_failed",
                actor_id=None, correlation_id=correlation_id,
            )
            if is_permanent:
                return
        raise

    duration_sec = time.monotonic() - call_started_at
    log.info(
        "jd.llm_call.complete", call_type="enrichment",
        duration_sec=round(duration_sec, 2),
        enriched_jd_chars=len(enrichment.enriched_jd),
    )
    await _persist_enriched_jd_only(db, job, enrichment.enriched_jd)
    log.info("jd.enrichment.completed")


@observe(name="jd_signal_extraction_phase")
async def _run_signal_extraction(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Phase 2 — signal extraction only.

    Reads either job.description_enriched (if phase 1 ran) or
    job.description_raw (if skip_enrichment), calls jd_signal_extraction.txt,
    writes a new JobPostingSignalSnapshot v1 row, transitions main state
    signals_extracting → signals_extracted on success.

    Idempotent: skipped if main status is no longer 'signals_extracting'.
    """
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
        phase="signal_extraction",
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.actor.job_not_found")
        return

    if job.status != "signals_extracting":
        log.warn("jd.actor.skip_unexpected_state", state=job.status)
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        job.status_error = "Company profile missing — create_job_posting should have blocked this"
        await transition(
            db, job, to_state="signals_extraction_failed",
            actor_id=None, correlation_id=correlation_id,
        )
        return

    # Use enriched JD if phase 1 ran; otherwise use raw JD.
    source_is_enriched = (
        job.enrichment_status == "completed" and job.description_enriched is not None
    )
    source_jd = job.description_enriched if source_is_enriched else job.description_raw

    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=[
            "jd_signal_extraction",
            f"retry:{retries_so_far}",
            "source:enriched" if source_is_enriched else "source:raw",
        ],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_signal_extraction",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "source_jd": "enriched" if source_is_enriched else "raw",
            "retries_so_far": retries_so_far,
        },
    )

    client = get_openai_client()
    prompt = prompt_loader.get("jd_signal_extraction")
    # Build the user message with whichever JD applies.
    user_message_parts: list[str] = [
        "## Company Profile\n"
        f"- About: {profile['about']}\n"
        f"- Industry: {profile['industry']}\n"
        f"- Company stage: {profile['company_stage']}\n"
        f"- Hiring bar: {profile['hiring_bar']}\n",
        f"## Job Description\n\n{source_jd}\n",
    ]
    if job.project_scope_raw:
        user_message_parts.append(f"## Project Scope\n\n{job.project_scope_raw}\n")
    user_message = "\n".join(user_message_parts)

    log.info(
        "jd.llm_call.start", call_type="signal_extraction",
        source="enriched" if source_is_enriched else "raw",
        model=ai_config.extraction_model,
        reasoning_effort=ai_config.extraction_effort,
        system_prompt_chars=len(prompt),
        user_message_chars=len(user_message),
    )
    call_started_at = time.monotonic()
    try:
        signal_output: SignalExtractionOutput = await client.chat.completions.create(
            model=ai_config.extraction_model,
            reasoning_effort=ai_config.extraction_effort,
            response_model=SignalExtractionOutput,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message},
            ],
            name="jd_signal_extraction_call",
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        duration_sec = time.monotonic() - call_started_at
        is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
        log.error(
            "jd.llm_call.failed", call_type="signal_extraction",
            duration_sec=round(duration_sec, 2),
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            permanent=is_permanent,
            retries_so_far=retries_so_far,
            exc_info=exc,
        )
        if is_permanent or retries_so_far >= 2:
            job.status_error = sanitize_error_for_user(exc)
            await transition(
                db, job, to_state="signals_extraction_failed",
                actor_id=None, correlation_id=correlation_id,
            )
            if is_permanent:
                return
        raise

    duration_sec = time.monotonic() - call_started_at
    log.info(
        "jd.llm_call.complete", call_type="signal_extraction",
        duration_sec=round(duration_sec, 2),
        signal_count=len(signal_output.signals.signals),
    )
    await _persist_signal_snapshot(db, job, signal_output.signals)
    await transition(
        db, job, to_state="signals_extracted",
        actor_id=None, correlation_id=correlation_id,
    )
    log.info("jd.signal_extraction.completed")
```

- [ ] **Step 3: Refactor the `extract_and_enhance_jd` actor to two-phase pattern**

REPLACE the entire `extract_and_enhance_jd` actor (lines 250–347) with:

```python
async def _publish_status(
    job_posting_id: str, tenant_id: str, correlation_id: str
) -> None:
    """Open a fresh session, read the committed JobStatusEvent, publish it.

    Used between phases so each commit is followed by an SSE event.
    Best-effort — failures are logged but never raised (consistent with
    pubsub.publish() semantics).
    """
    try:
        async with get_bypass_session() as pub_db:
            safe_tenant_id = str(UUID(tenant_id))
            await pub_db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
            )
            status_event = await get_job_status(pub_db, UUID(job_posting_id))
    except Exception as exc:
        logger.warning(
            "actors.extract_and_enhance_jd.publish_read_failed",
            job_posting_id=job_posting_id, error=str(exc),
        )
        return

    if status_event is not None:
        await pubsub.publish(
            pubsub.job_channel(job_posting_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
            correlation_id=correlation_id,
        )


@dramatiq.actor(
    max_retries=2,
    min_backoff=2_000,
    max_backoff=60_000,
    queue_name="jd_extraction",
)
async def extract_and_enhance_jd(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    skip_enrichment: bool = False,
) -> None:
    """Two-phase JD processing.

    Phase 1 (conditional on `skip_enrichment`): enrichment LLM call →
    write description_enriched, commit, publish status event.
    Phase 2 (always): signal extraction LLM call → write snapshot,
    transition to signals_extracted, commit, publish status event.

    Each phase opens its own DB session and commits independently so
    the intermediate state is visible to SSE subscribers. On retry,
    phase 1 is skipped automatically when enrichment_status='completed'.
    """
    current = CurrentMessage.get_current_message()
    retries_so_far = current.options.get("retries", 0) if current else 0

    safe_tenant_id = str(UUID(tenant_id))
    _exc_to_reraise: BaseException | None = None

    # ---- Phase 1: Enrichment (conditional) ----
    phase_1_committed = False
    if not skip_enrichment:
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
            )
            try:
                await _run_enrichment(
                    db, job_posting_id=job_posting_id,
                    tenant_id=tenant_id, correlation_id=correlation_id,
                    retries_so_far=retries_so_far,
                )
                await db.commit()
                phase_1_committed = True
            except Exception as exc:
                if retries_so_far >= 2:
                    await db.commit()
                    phase_1_committed = True
                else:
                    await db.rollback()
                _exc_to_reraise = exc
            finally:
                if langfuse_enabled():
                    await asyncio.to_thread(flush_langfuse)

        if phase_1_committed:
            await _publish_status(job_posting_id, tenant_id, correlation_id)

        if _exc_to_reraise is not None:
            raise _exc_to_reraise

    # ---- Phase 2: Signal extraction (always) ----
    phase_2_committed = False
    async with get_bypass_session() as db:
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )
        try:
            await _run_signal_extraction(
                db, job_posting_id=job_posting_id,
                tenant_id=tenant_id, correlation_id=correlation_id,
                retries_so_far=retries_so_far,
            )
            await db.commit()
            phase_2_committed = True
        except Exception as exc:
            if retries_so_far >= 2:
                await db.commit()
                phase_2_committed = True
            else:
                await db.rollback()
            _exc_to_reraise = exc
        finally:
            if langfuse_enabled():
                await asyncio.to_thread(flush_langfuse)

    if phase_2_committed:
        await _publish_status(job_posting_id, tenant_id, correlation_id)

    if _exc_to_reraise is not None:
        raise _exc_to_reraise
```

- [ ] **Step 4: Run the new tests — expect PASS**

Run: `docker compose run --rm nexus pytest tests/test_jd_actor.py::test_two_phase_extraction_runs_both_llm_calls_in_order tests/test_jd_actor.py::test_phase_2_reads_enriched_jd_when_phase_1_ran -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full actor test suite — expect ALL existing tests still pass**

Run: `docker compose run --rm nexus pytest tests/test_jd_actor.py -v`
Expected: all tests pass. The existing `test_actor_happy_path_persists_snapshot` (and any others that exercised `_run_extraction`) need updating in the next task; for now this command may show 1-2 failures we'll fix in Task 5.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/jd/actors.py
git commit -m "feat(jd): split actor into two-phase enrichment + signal extraction"
```

---

### Task 5: Update existing tests + drop `ExtractionOutput` and `jd_enhancement.txt`

**Files:**
- Modify: `backend/nexus/tests/test_jd_actor.py`
- Modify: `backend/nexus/app/ai/schemas.py`
- Delete: `backend/nexus/prompts/v1/jd_enhancement.txt`

The old `test_actor_happy_path_persists_snapshot` exercised `_run_extraction` directly with a single mocked `ExtractionOutput`. That function no longer exists. We rewrite the tests against the new two-phase functions.

- [ ] **Step 1: Identify the existing tests that reference the removed function**

Run: `docker compose run --rm nexus grep -n "_run_extraction\|ExtractionOutput" tests/test_jd_actor.py`
Expected: shows the lines that need editing.

- [ ] **Step 2: Rewrite each existing test that referenced `_run_extraction` to use the two new functions**

Replace each occurrence of `await _run_extraction(...)` with the two-call pattern shown in Task 3 step 2. For tests that previously mocked one LLM call returning `ExtractionOutput`, update them to mock two LLM calls (one returning `EnrichmentOutput`, one returning `SignalExtractionOutput`).

Run: `docker compose run --rm nexus pytest tests/test_jd_actor.py -v` after each test to confirm green-lit.

- [ ] **Step 3: Drop `ExtractionOutput` from `app/ai/schemas.py`**

In `backend/nexus/app/ai/schemas.py`, REMOVE the `ExtractionOutput` class definition (lines 81–84):

```python
# Remove these 4 lines:
class ExtractionOutput(BaseModel):
    enriched_jd: str = Field(min_length=50)
    signals: ExtractedSignals
```

- [ ] **Step 4: Delete the old prompt**

Run: `git rm backend/nexus/prompts/v1/jd_enhancement.txt`
Expected: file removed.

- [ ] **Step 5: Search for any lingering references**

Run: `docker compose run --rm nexus grep -rn "ExtractionOutput\|jd_enhancement" app/ tests/`
Expected: zero results. If any appear, fix them.

- [ ] **Step 6: Run full backend test suite to confirm nothing else broke**

Run: `docker compose run --rm nexus pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/tests/test_jd_actor.py backend/nexus/app/ai/schemas.py
git rm backend/nexus/prompts/v1/jd_enhancement.txt
git commit -m "refactor(jd): retire ExtractionOutput and jd_enhancement.txt prompt"
```

---

## Phase 2 — Backend skip-enrichment toggle

Goal: add the request-time toggle. After this phase, `POST /api/jobs` accepts `skip_enrichment: bool` and the actor branches accordingly.

### Task 6: Add `skip_enrichment` to `JobPostingCreate` schema

**Files:**
- Modify: `backend/nexus/app/modules/jd/schemas.py`

- [ ] **Step 1: Write a failing test that asserts `skip_enrichment` defaults to `false` and accepts `true`**

Append to `backend/nexus/tests/test_jd_schemas.py` (create the file if it does not exist):

```python
import pytest
from uuid import uuid4
from app.modules.jd.schemas import JobPostingCreate


def test_job_posting_create_skip_enrichment_default_false():
    body = JobPostingCreate(
        org_unit_id=uuid4(),
        title="Test",
        description_raw="x" * 60,
    )
    assert body.skip_enrichment is False


def test_job_posting_create_skip_enrichment_true():
    body = JobPostingCreate(
        org_unit_id=uuid4(),
        title="Test",
        description_raw="x" * 60,
        skip_enrichment=True,
    )
    assert body.skip_enrichment is True
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `docker compose run --rm nexus pytest tests/test_jd_schemas.py -v`
Expected: FAIL with `AttributeError: 'JobPostingCreate' object has no attribute 'skip_enrichment'`.

- [ ] **Step 3: Add the field to `JobPostingCreate`**

In `backend/nexus/app/modules/jd/schemas.py`, modify the `JobPostingCreate` class (lines 142–161) to add the new field at the end:

```python
class JobPostingCreate(BaseModel):
    """POST /api/jobs request body."""

    model_config = ConfigDict(extra="forbid")

    org_unit_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description_raw: str = Field(min_length=50, max_length=50_000)
    project_scope_raw: str | None = Field(default=None, max_length=20_000)
    target_headcount: int | None = Field(default=None, ge=1, le=10_000)
    deadline: date | None = None
    employment_type: EmploymentType | None = None
    work_arrangement: WorkArrangement | None = None
    location: str | None = Field(default=None, max_length=500)
    salary_range_min: int | None = Field(default=None, ge=0)
    salary_range_max: int | None = Field(default=None, ge=0)
    salary_currency: SalaryCurrency | None = None
    travel_required: TravelRequired | None = None
    start_date_pref: StartDatePref | None = None
    skip_enrichment: bool = Field(
        default=False,
        description=(
            "If true, signal extraction runs against the raw JD; "
            "JD enrichment phase is skipped entirely."
        ),
    )
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `docker compose run --rm nexus pytest tests/test_jd_schemas.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/schemas.py backend/nexus/tests/test_jd_schemas.py
git commit -m "feat(jd): add skip_enrichment field to JobPostingCreate"
```

---

### Task 7: Plumb `skip_enrichment` through service + router → actor

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`
- Modify: `backend/nexus/app/modules/jd/router.py`

The `create_job_posting()` service does not need to PERSIST the flag — it just forwards it to the actor dispatch. The router already calls `_safe_dispatch_extraction` which calls `extract_and_enhance_jd.send(...)` — we add the `skip_enrichment` argument there.

- [ ] **Step 1: Write a failing test that asserts the actor receives `skip_enrichment`**

Append to `backend/nexus/tests/test_jd_router.py`:

```python
@pytest.mark.asyncio
async def test_create_job_with_skip_enrichment_forwards_to_actor(
    async_client, recruiter_token, org_unit_with_profile, monkeypatch
):
    """When skip_enrichment=true is in the request body, the actor must
    receive that flag in its kwargs."""
    captured: dict = {}

    def fake_send(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        fake_send,
    )

    resp = await async_client.post(
        "/api/jobs",
        headers={"Authorization": f"Bearer {recruiter_token}"},
        json={
            "org_unit_id": str(org_unit_with_profile.id),
            "title": "Test",
            "description_raw": "x" * 60,
            "skip_enrichment": True,
        },
    )
    assert resp.status_code == 201
    assert captured["kwargs"].get("skip_enrichment") is True
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py::test_create_job_with_skip_enrichment_forwards_to_actor -v`
Expected: FAIL — `captured["kwargs"]` does not contain `skip_enrichment`.

- [ ] **Step 3: Update `_safe_dispatch_extraction` in router.py to accept and forward the flag**

In `backend/nexus/app/modules/jd/router.py`, find `_safe_dispatch_extraction` (around lines 151–195). Update its signature and the `.send()` call:

```python
def _safe_dispatch_extraction(
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    skip_enrichment: bool = False,  # ADD THIS
    db: AsyncSession,
) -> None:
    """Wraps extract_and_enhance_jd.send() with a Redis-down fallback."""
    try:
        extract_and_enhance_jd.send(
            job_posting_id=job_posting_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            skip_enrichment=skip_enrichment,  # ADD THIS
        )
    except Exception:
        # ...existing fallback transition logic unchanged
        ...
```

(Preserve the existing exception-handling body — only the function signature and the `.send()` call change.)

- [ ] **Step 4: Update the `create_job` endpoint to pass `skip_enrichment` through**

In the same file, find the `create_job` handler (lines 296–361) and find the `background_tasks.add_task(_safe_dispatch_extraction, ...)` call. Add `skip_enrichment=body.skip_enrichment` to the kwargs:

```python
background_tasks.add_task(
    _safe_dispatch_extraction,
    job_posting_id=str(job.id),
    tenant_id=str(job.tenant_id),
    correlation_id=correlation_id,
    skip_enrichment=body.skip_enrichment,  # ADD THIS
    db=db,
)
```

- [ ] **Step 5: Add the actor-level test for skip_enrichment behavior**

Append to `backend/nexus/tests/test_jd_actor.py`:

```python
@pytest.mark.asyncio
async def test_run_signal_extraction_uses_raw_when_no_enrichment(db_session):
    """When phase 1 was skipped (enrichment_status='idle'), phase 2 reads raw JD."""
    from app.modules.jd.actors import _run_signal_extraction

    job = await _make_extracting_job(db_session, raw_jd="RAW_JD_FIXTURE_MARKER content")
    await db_session.commit()
    assert job.enrichment_status == "idle"

    signals = _fake_signal_extraction_output()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=signals)

    with monkeypatch_get_client(mock_client):
        await _run_signal_extraction(
            db_session, job_posting_id=str(job.id),
            tenant_id=str(job.tenant_id), correlation_id="cid", retries_so_far=0,
        )

    user_message = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "RAW_JD_FIXTURE_MARKER" in user_message
```

(You may need to update the `_make_extracting_job` helper to accept a `raw_jd` parameter if it doesn't already — pass through to the `JobPosting.description_raw` constructor field.)

- [ ] **Step 6: Run the new tests — expect PASS**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py::test_create_job_with_skip_enrichment_forwards_to_actor tests/test_jd_actor.py::test_run_signal_extraction_uses_raw_when_no_enrichment -v`
Expected: 2 passed.

- [ ] **Step 7: Run full backend suite**

Run: `docker compose run --rm nexus pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_router.py backend/nexus/tests/test_jd_actor.py
git commit -m "feat(jd): plumb skip_enrichment through service + router to actor"
```

---

## Phase 3 — Frontend Tabs primitive + center-column toggle

Goal: introduce a `Tabs` primitive in `components/px/`, refactor `JDReviewShell` to use a 3-way `raw | enriched | signals` view state, and remove the old "Full JD" button from `SectionsRail`.

### Task 8: Add `Tabs` primitive to `components/px/`

**Files:**
- Create: `frontend/app/components/px/Tabs.tsx`
- Modify: `frontend/app/components/px/index.ts`
- Create: `frontend/app/tests/components/Tabs.test.tsx`

- [ ] **Step 1: Write a failing component test**

Write to `frontend/app/tests/components/Tabs.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { renderWithProviders } from '../_utils/render'
import { Tabs } from '@/components/px/Tabs'

describe('Tabs primitive', () => {
  const items = [
    { value: 'a', label: 'A' },
    { value: 'b', label: 'B' },
    { value: 'c', label: 'C' },
  ]

  it('renders all items and marks the selected one as active (aria-selected=true)', () => {
    const { getByRole } = renderWithProviders(
      <Tabs value="b" onChange={() => {}} items={items} ariaLabel="Test tabs" />,
    )
    expect(getByRole('tab', { name: 'A' }).getAttribute('aria-selected')).toBe('false')
    expect(getByRole('tab', { name: 'B' }).getAttribute('aria-selected')).toBe('true')
    expect(getByRole('tab', { name: 'C' }).getAttribute('aria-selected')).toBe('false')
  })

  it('calls onChange when a non-disabled tab is clicked', () => {
    const onChange = vi.fn()
    const { getByRole } = renderWithProviders(
      <Tabs value="a" onChange={onChange} items={items} ariaLabel="Test tabs" />,
    )
    fireEvent.click(getByRole('tab', { name: 'C' }))
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('does not call onChange when a disabled tab is clicked', () => {
    const onChange = vi.fn()
    const itemsWithDisabled = [
      { value: 'a', label: 'A' },
      { value: 'b', label: 'B', disabled: true },
    ]
    const { getByRole } = renderWithProviders(
      <Tabs value="a" onChange={onChange} items={itemsWithDisabled} ariaLabel="Test tabs" />,
    )
    fireEvent.click(getByRole('tab', { name: 'B' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('does not render hidden items', () => {
    const itemsWithHidden = [
      { value: 'a', label: 'A' },
      { value: 'b', label: 'B', hidden: true },
      { value: 'c', label: 'C' },
    ]
    const { queryByRole } = renderWithProviders(
      <Tabs value="a" onChange={() => {}} items={itemsWithHidden} ariaLabel="Test tabs" />,
    )
    expect(queryByRole('tab', { name: 'B' })).toBeNull()
    expect(queryByRole('tab', { name: 'C' })).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run the test — expect FAIL**

Run from `frontend/app`: `npm run test -- tests/components/Tabs.test.tsx`
Expected: FAIL — `Cannot find module '@/components/px/Tabs'`.

- [ ] **Step 3: Implement the Tabs primitive**

Write to `frontend/app/components/px/Tabs.tsx`:

```tsx
'use client'

import { ReactNode } from 'react'

export type TabItem<T extends string> = {
  value: T
  label: ReactNode
  disabled?: boolean
  hidden?: boolean
  /** Optional tooltip text shown on hover when disabled. */
  disabledHint?: string
}

type Props<T extends string> = {
  value: T
  onChange: (next: T) => void
  items: TabItem<T>[]
  ariaLabel: string
  className?: string
}

/**
 * Segmented-control-style tab control. Visually a row of pill buttons; the
 * selected pill is filled with --px-accent. Hidden items are not rendered.
 * Disabled items render but do not respond to clicks.
 *
 * Use this for in-page view switching (e.g., the JD review center column's
 * Raw / Enriched / Signal details toggle), not for navigation.
 */
export function Tabs<T extends string>({
  value,
  onChange,
  items,
  ariaLabel,
  className,
}: Props<T>) {
  const visible = items.filter((it) => !it.hidden)
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`inline-flex items-center gap-0.5 rounded-md border p-0.5 ${className ?? ''}`}
      style={{
        background: 'var(--px-surface-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      {visible.map((item) => {
        const selected = item.value === value
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-disabled={item.disabled || undefined}
            title={item.disabled ? item.disabledHint : undefined}
            disabled={item.disabled}
            onClick={() => {
              if (item.disabled) return
              onChange(item.value)
            }}
            className="rounded px-3 py-1 text-[12.5px] font-medium transition-colors"
            style={{
              background: selected ? 'var(--px-accent)' : 'transparent',
              color: selected
                ? 'var(--px-fg-on-accent)'
                : item.disabled
                  ? 'var(--px-fg-4)'
                  : 'var(--px-fg)',
              cursor: item.disabled ? 'not-allowed' : 'pointer',
              opacity: item.disabled ? 0.5 : 1,
            }}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Re-export from the px barrel**

In `frontend/app/components/px/index.ts`, add:

```typescript
export { Tabs } from './Tabs'
export type { TabItem } from './Tabs'
```

(Place alphabetically among the other re-exports.)

- [ ] **Step 5: Run the test — expect PASS**

Run from `frontend/app`: `npm run test -- tests/components/Tabs.test.tsx`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/px/Tabs.tsx frontend/app/components/px/index.ts frontend/app/tests/components/Tabs.test.tsx
git commit -m "feat(px): add Tabs segmented-control primitive"
```

---

### Task 9: Refactor `JDReviewShell` to 3-way center toggle, remove "Full JD" button

**Files:**
- Modify: `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/SectionsRail.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/RawJdCanvas.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/FullJdCanvas.tsx` (rename to `EnrichedJdCanvas.tsx` — see Step 4)

The current shell has 2-way state `signals | jd` driven by URL `?view=jd`. We change it to 3-way `raw | enriched | signals` driven by URL `?view=raw|enriched|signals` (default `signals`). The center column gains a `Tabs` control that drives this state. The left rail's "Full JD" button is removed.

- [ ] **Step 1: Read the existing SectionsRail to understand the JD button**

Run: `cat frontend/app/components/dashboard/jd-panels/SectionsRail.tsx | grep -n "onShowJd\|Full JD\|jd"`
Expected: lines that reference the JD section / button.

- [ ] **Step 2: Write a failing component test for the 3-way toggle**

Write to `frontend/app/tests/components/JDReviewShell.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { renderWithProviders } from '../_utils/render'
import { JDReviewShell } from '@/components/dashboard/jd-panels/JDReviewShell'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

const mockJob: JobPostingWithSnapshot = {
  // Construct a minimal valid job — paste the helper here or import from a shared fixture.
  // See existing test fixtures in tests/api/ or tests/components/ for the canonical shape.
  // The key fields used by JDReviewShell are: id, description_raw, description_enriched,
  // is_confirmed, can_manage, latest_snapshot.
  id: 'job-1',
  title: 'Test Role',
  org_unit_id: 'unit-1',
  description_raw: 'RAW_JD_BODY',
  description_enriched: 'ENRICHED_JD_BODY',
  status: 'signals_extracted',
  enrichment_status: 'completed',
  is_confirmed: false,
  can_manage: true,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  latest_snapshot: {
    version: 1,
    seniority_level: 'senior',
    role_summary: 'Test summary',
    signals: [
      {
        value: 'Python', type: 'competency', priority: 'required',
        weight: 3, knockout: true, stage: 'screen',
        evaluation_method: 'verbal_response',
        source: 'ai_extracted', inference_basis: null,
      },
    ],
  },
} as JobPostingWithSnapshot

describe('JDReviewShell — 3-way center toggle', () => {
  it('renders three tabs: Raw JD, Enriched JD, Signal details', () => {
    const { getByRole } = renderWithProviders(
      <JDReviewShell job={mockJob} onReEnrich={() => {}} />,
    )
    expect(getByRole('tab', { name: /Raw JD/i })).not.toBeNull()
    expect(getByRole('tab', { name: /Enriched JD/i })).not.toBeNull()
    expect(getByRole('tab', { name: /Signal details/i })).not.toBeNull()
  })

  it('hides the Enriched JD tab when description_enriched is null', () => {
    const skipped: JobPostingWithSnapshot = {
      ...mockJob,
      description_enriched: null,
      enrichment_status: 'idle',
    }
    const { queryByRole } = renderWithProviders(
      <JDReviewShell job={skipped} onReEnrich={() => {}} />,
    )
    expect(queryByRole('tab', { name: /Enriched JD/i })).toBeNull()
  })

  it('switches center body when a tab is clicked', () => {
    const { getByRole, getByText } = renderWithProviders(
      <JDReviewShell job={mockJob} onReEnrich={() => {}} />,
    )
    // Default is Signal details — Raw JD body should not be visible.
    fireEvent.click(getByRole('tab', { name: /Raw JD/i }))
    expect(getByText(/RAW_JD_BODY/)).not.toBeNull()

    fireEvent.click(getByRole('tab', { name: /Enriched JD/i }))
    expect(getByText(/ENRICHED_JD_BODY/)).not.toBeNull()
  })
})
```

- [ ] **Step 3: Run the test — expect FAIL**

Run from `frontend/app`: `npm run test -- tests/components/JDReviewShell.test.tsx`
Expected: FAIL — tabs do not exist yet.

- [ ] **Step 4: Rename `FullJdCanvas` → `EnrichedJdCanvas`**

Run: `git mv frontend/app/components/dashboard/jd-panels/FullJdCanvas.tsx frontend/app/components/dashboard/jd-panels/EnrichedJdCanvas.tsx`

In the renamed file, update the named export from `FullJdCanvas` to `EnrichedJdCanvas`. Update any internal references.

- [ ] **Step 5: Create `RawJdCanvas.tsx`**

Write to `frontend/app/components/dashboard/jd-panels/RawJdCanvas.tsx`:

```tsx
'use client'

import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

/**
 * Renders the original raw JD verbatim (description_raw). No formatting,
 * no markdown — preserved as-is so the user sees what they pasted.
 */
export function RawJdCanvas({ job }: { job: JobPostingWithSnapshot }) {
  return (
    <section
      className="rounded-[10px] border p-6 max-w-none"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
        color: 'var(--px-fg)',
      }}
    >
      <h3
        className="px-eyebrow mb-4"
        style={{ marginBottom: 14 }}
      >
        Raw JD
      </h3>
      <div
        className="text-[13.5px] whitespace-pre-wrap"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.65 }}
      >
        {job.description_raw}
      </div>
    </section>
  )
}
```

- [ ] **Step 6: Refactor `JDReviewShell.tsx` to use 3-way state and the Tabs primitive**

REPLACE the relevant lines of `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx` to change:

1. Import the new `Tabs` and `RawJdCanvas`, `EnrichedJdCanvas`:

```typescript
import { Tabs } from '@/components/px'
import { EnrichedJdCanvas } from './EnrichedJdCanvas'
import { RawJdCanvas } from './RawJdCanvas'
```

(Remove the `FullJdCanvas` import.)

2. Update the `InnerView` type and URL parsing logic (around line 19, 35–45):

```typescript
type InnerView = 'raw' | 'enriched' | 'signals'

const VALID_VIEWS: InnerView[] = ['raw', 'enriched', 'signals']

function defaultView(job: JobPostingWithSnapshot): InnerView {
  // After signals are extracted, default to enriched if it ran, else raw.
  if (job.enrichment_status === 'completed') return 'enriched'
  if (job.description_enriched) return 'enriched'
  return 'raw'
}

// Inside the component body, replace the existing `view` derivation:
const rawView = searchParams.get('view')
const view: InnerView = (
  rawView && (VALID_VIEWS as string[]).includes(rawView) ? rawView : 'signals'
) as InnerView

const setView = (v: InnerView) => {
  const qs = new URLSearchParams(searchParams.toString())
  if (v === 'signals') qs.delete('view')
  else qs.set('view', v)
  qs.set('tab', 'jd')
  router.replace(`/jobs/${job.id}?${qs.toString()}`, { scroll: false })
}
```

3. Add a `Tabs` row above the canvas. Insert this just before the canvas conditional render (around line 164):

```tsx
<div className="col-start-2 col-end-3 mb-3 flex items-center justify-between">
  <Tabs<InnerView>
    ariaLabel="JD view"
    value={view}
    onChange={setView}
    items={[
      { value: 'raw', label: 'Raw JD' },
      {
        value: 'enriched',
        label: 'Enriched JD',
        hidden: !job.description_enriched && job.enrichment_status !== 'streaming',
        disabled: job.enrichment_status === 'failed',
        disabledHint: 'Enrichment failed — retry to re-run',
      },
      { value: 'signals', label: 'Signal details' },
    ]}
  />
</div>
```

(`col-start-2 col-end-3` keeps the Tabs row visually anchored above the center column. Verify against the existing `gridTemplateColumns: '220px 1fr 380px'` layout.)

4. Replace the `view === 'jd' ? <FullJdCanvas /> : <SignalsCanvas />` ternary with a 3-way switch:

```tsx
{view === 'raw' ? (
  <RawJdCanvas job={job} />
) : view === 'enriched' ? (
  <EnrichedJdCanvas job={job} onReEnrich={onReEnrich} />
) : (
  <SignalsCanvas
    must={must}
    nice={nice}
    job={job}
    stateBanner={stateBanner}
    isConfirmed={isConfirmed}
    canManage={canManage}
    isDirty={isDirty}
    saving={saveMutation.isPending}
    confirming={confirmMutation.isPending}
    needsReviewCount={needsReviewCount}
    totalCount={totalCount}
    focusIdx={focusIdx}
    onFocus={setFocus}
    onSave={save}
    onSaveAndConfirm={saveAndConfirm}
    onReEnrich={onReEnrich}
  />
)}
```

5. Update the right-column conditional: keep `InspectorTips` for `raw | enriched`, signal inspector for `signals`:

```tsx
{view !== 'signals' ? (
  <InspectorTips />
) : focusSignal ? (
  <SignalInspector ... />
) : (
  <InspectorHint ... />
)}
```

- [ ] **Step 7: Update `SectionsRail.tsx` — remove "Full JD" button and `onShowJd` prop**

Edit `frontend/app/components/dashboard/jd-panels/SectionsRail.tsx`:

1. Remove `onShowJd` from the props type and function signature.
2. Remove the `<button>` (or whatever element) that renders "Full JD" and the section row that fired it.
3. Remove any `'jd'` value from the `activeSection` enum if it's defined here (otherwise leave it — the shell still tracks `'jd'` internally but this rail no longer drives it).

Update `JDReviewShell.tsx` to no longer pass `onShowJd` and `filename` to `SectionsRail` (delete those props from the call site).

- [ ] **Step 8: Run the test — expect PASS**

Run from `frontend/app`: `npm run test -- tests/components/JDReviewShell.test.tsx`
Expected: 3 passed.

- [ ] **Step 9: Run lint + type-check + full test suite**

Run from `frontend/app`:
```
npm run lint
npm run type-check
npm run test
```
Expected: zero errors, all tests pass.

- [ ] **Step 10: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/ frontend/app/tests/components/JDReviewShell.test.tsx
git commit -m "feat(jobs): 3-way Raw/Enriched/Signal-details center toggle in JD review shell"
```

---

## Phase 4 — Frontend phase-targeted loading + new SSE consumption

Goal: replace the monolithic `LoadingSkeleton` with `JDExtractingView`, a 3-column shell that mirrors the review shell layout but renders phase-targeted loading states. The center column hosts the same Tabs control (for consistency); side panels show "Waiting for signals…" placeholders during phase 1 and skeleton shimmers during phase 2.

### Task 10: Create `JDExtractingView` component

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/JDExtractingView.tsx`
- Create: `frontend/app/tests/components/JDExtractingView.test.tsx`

- [ ] **Step 1: Write failing test for phase-targeted loading**

Write to `frontend/app/tests/components/JDExtractingView.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { renderWithProviders } from '../_utils/render'
import { JDExtractingView } from '@/components/dashboard/jd-panels/JDExtractingView'

describe('JDExtractingView', () => {
  it('phase 1 (enrichment streaming): center shows enrichment skeleton, side panels show waiting placeholder', () => {
    const { getByTestId, queryByTestId } = renderWithProviders(
      <JDExtractingView
        descriptionRaw="raw text"
        enrichmentStatus="streaming"
        skipEnrichment={false}
      />,
    )
    expect(getByTestId('jd-center-loading-enrichment')).not.toBeNull()
    expect(queryByTestId('jd-side-panel-skeleton')).toBeNull()
    expect(getByTestId('jd-side-panel-waiting')).not.toBeNull()
  })

  it('phase 2 with enrichment ran: center shows enriched JD, side panels show signal-loading skeleton', () => {
    const { getByTestId, queryByTestId } = renderWithProviders(
      <JDExtractingView
        descriptionRaw="raw text"
        descriptionEnriched="enriched text"
        enrichmentStatus="completed"
        skipEnrichment={false}
      />,
    )
    expect(queryByTestId('jd-center-loading-enrichment')).toBeNull()
    expect(getByTestId('jd-center-enriched-body')).not.toBeNull()
    expect(getByTestId('jd-side-panel-skeleton')).not.toBeNull()
  })

  it('phase 2 with skip_enrichment=true: center shows raw JD, no enrichment phase visible', () => {
    const { getByTestId, queryByRole } = renderWithProviders(
      <JDExtractingView
        descriptionRaw="raw text"
        enrichmentStatus="idle"
        skipEnrichment={true}
      />,
    )
    expect(getByTestId('jd-center-raw-body')).not.toBeNull()
    expect(queryByRole('tab', { name: /Enriched JD/i })).toBeNull()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

Run from `frontend/app`: `npm run test -- tests/components/JDExtractingView.test.tsx`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement `JDExtractingView`**

Write to `frontend/app/components/dashboard/jd-panels/JDExtractingView.tsx`:

```tsx
'use client'

import { useState } from 'react'

import { Skeleton, Tabs } from '@/components/px'
import type { EnrichmentStatus } from '@/lib/api/jobs'

type Props = {
  descriptionRaw: string
  descriptionEnriched?: string | null
  enrichmentStatus: EnrichmentStatus
  skipEnrichment: boolean
  sseError?: string | null
}

type View = 'raw' | 'enriched' | 'signals'

/**
 * Loading view rendered while a job is in `signals_extracting` state.
 *
 * Layout mirrors JDReviewShell (3-column grid). The center column hosts a
 * Tabs control identical to the review shell's; the loading state is
 * phase-targeted:
 *
 * - Phase 1 (enrichment_status='streaming'): center "Enriched JD" tab shows
 *   skeleton; side panels show a static "Waiting for signals…" placeholder
 *   (no shimmer).
 * - Phase 2 (enrichment_status='completed' or 'idle'+skipEnrichment): center
 *   shows the JD that the model is using; side panels show signal-loading
 *   skeletons.
 *
 * After phase 2 completes, the parent page swaps this component for
 * JDReviewShell.
 */
export function JDExtractingView({
  descriptionRaw,
  descriptionEnriched,
  enrichmentStatus,
  skipEnrichment,
  sseError,
}: Props) {
  // Default tab logic — see spec §5.2 default tab matrix.
  const computeDefaultView = (): View => {
    if (skipEnrichment) return 'raw'
    if (enrichmentStatus === 'streaming') return 'raw'
    if (enrichmentStatus === 'completed') return 'enriched'
    if (enrichmentStatus === 'failed') return 'raw'
    return 'raw'
  }
  const [view, setView] = useState<View>(computeDefaultView())

  const enrichmentRanOrRunning =
    enrichmentStatus === 'streaming' || enrichmentStatus === 'completed'

  // Phase-targeted state flags
  const phase1InFlight = enrichmentStatus === 'streaming'
  const phase2InFlight = !phase1InFlight && enrichmentStatus !== 'failed'

  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: '220px 1fr 380px' }}>
      {/* Left rail — quiet during extraction. */}
      <aside
        className="rounded-[10px] border p-4 self-start"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
        data-testid={
          phase2InFlight ? 'jd-side-panel-skeleton' : 'jd-side-panel-waiting'
        }
      >
        <div
          className="px-eyebrow mb-3"
          style={{ marginBottom: 12, color: 'var(--px-fg-3)' }}
        >
          Sections
        </div>
        {phase2InFlight ? (
          <>
            <Skeleton className="h-3 w-full mb-2" />
            <Skeleton className="h-3 w-3/4 mb-2" />
            <Skeleton className="h-3 w-2/3" />
          </>
        ) : (
          <div
            className="text-[12.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            Waiting for signals…
          </div>
        )}
      </aside>

      {/* Center column — Tabs + body */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <Tabs<View>
            ariaLabel="JD view"
            value={view}
            onChange={setView}
            items={[
              { value: 'raw', label: 'Raw JD' },
              {
                value: 'enriched',
                label: 'Enriched JD',
                hidden: skipEnrichment,
                disabled: enrichmentStatus === 'failed',
                disabledHint: 'Enrichment failed — retry to re-run',
              },
              { value: 'signals', label: 'Signal details', disabled: true },
            ]}
          />
          {sseError && (
            <span
              className="text-[12px] px-2 py-1 rounded border"
              style={{
                background: 'var(--px-warn-tint)',
                borderColor: 'var(--px-warn-line)',
                color: 'var(--px-warn-fg)',
              }}
            >
              {sseError}
            </span>
          )}
        </div>

        {view === 'raw' && (
          <div
            data-testid="jd-center-raw-body"
            className="rounded-[10px] border p-6 whitespace-pre-wrap text-[13.5px]"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
              color: 'var(--px-fg-2)',
              lineHeight: 1.65,
            }}
          >
            {descriptionRaw}
          </div>
        )}

        {view === 'enriched' && (
          phase1InFlight ? (
            <div
              data-testid="jd-center-loading-enrichment"
              className="rounded-[10px] border p-6 space-y-3"
              style={{
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
              }}
            >
              <div
                className="inline-flex items-center gap-2 text-[12px] mb-3"
                style={{ color: 'var(--px-accent)' }}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full animate-pulse"
                  style={{ background: 'var(--px-accent)' }}
                />
                Copilot is enriching the JD…
              </div>
              <Skeleton className="h-4 w-1/3 mb-2" />
              <Skeleton className="h-3 w-full mb-1.5" />
              <Skeleton className="h-3 w-11/12 mb-1.5" />
              <Skeleton className="h-3 w-3/4 mb-4" />
              <Skeleton className="h-4 w-2/5 mb-2" />
              <Skeleton className="h-3 w-full mb-1.5" />
              <Skeleton className="h-3 w-5/6" />
            </div>
          ) : (
            <div
              data-testid="jd-center-enriched-body"
              className="rounded-[10px] border p-6 whitespace-pre-wrap text-[13.5px]"
              style={{
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
                color: 'var(--px-fg-2)',
                lineHeight: 1.65,
              }}
            >
              {descriptionEnriched ?? ''}
            </div>
          )
        )}

        {view === 'signals' && (
          <div
            className="rounded-[10px] border p-6 text-[12.5px]"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
              color: 'var(--px-fg-4)',
            }}
          >
            Signals will appear here once Copilot finishes extracting them.
          </div>
        )}
      </section>

      {/* Right panel */}
      <aside
        className="rounded-[10px] border p-4 self-start"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
        data-testid={
          phase2InFlight ? 'jd-side-panel-skeleton' : 'jd-side-panel-waiting'
        }
      >
        <div
          className="px-eyebrow mb-3"
          style={{ marginBottom: 12, color: 'var(--px-fg-3)' }}
        >
          Signal inspector
        </div>
        {phase2InFlight ? (
          <div className="space-y-3">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-5/6" />
            <Skeleton className="h-3 w-2/3" />
            <div className="flex gap-1.5 flex-wrap pt-2">
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-5 w-20 rounded-full" />
              <Skeleton className="h-5 w-14 rounded-full" />
            </div>
          </div>
        ) : (
          <div
            className="text-[12.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            Waiting for signals…
          </div>
        )}
      </aside>
    </div>
  )
}
```

- [ ] **Step 4: Re-export from the panels barrel**

In `frontend/app/components/dashboard/jd-panels/index.ts`, add:

```typescript
export { JDExtractingView } from './JDExtractingView'
```

(Adjacent to the existing exports.)

- [ ] **Step 5: Run the test — expect PASS**

Run from `frontend/app`: `npm run test -- tests/components/JDExtractingView.test.tsx`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/JDExtractingView.tsx frontend/app/components/dashboard/jd-panels/index.ts frontend/app/tests/components/JDExtractingView.test.tsx
git commit -m "feat(jobs): add JDExtractingView with phase-targeted loading"
```

---

### Task 11: Wire `JDExtractingView` into the page; delete `LoadingSkeleton`

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`
- Delete: `frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/index.ts`

The page currently renders `<LoadingSkeleton status={status} sseError={sseError} />` while the job is in `draft` or `signals_extracting`. Replace with `<JDExtractingView ...>` and pass through `enrichment_status`, `skip_enrichment` (derived from job state), and the JD bodies.

- [ ] **Step 1: Update the page render logic**

In `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`:

1. Replace the import:
```typescript
// Remove:
import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
// Add:
import { JDExtractingView } from '@/components/dashboard/jd-panels/JDExtractingView'
```

2. Replace the loading-state branches (lines 40–46):

```tsx
if (isLoading || !job) {
  return (
    <JDExtractingView
      descriptionRaw=""
      enrichmentStatus="idle"
      skipEnrichment={false}
      sseError={sseError}
    />
  )
}

if (job.status === 'draft' || job.status === 'signals_extracting') {
  // skip_enrichment isn't persisted on the job — infer it from enrichment_status:
  // 'idle' while we're past phase 1 means it was skipped.
  // (Once a refresh lands during phase 2 of a non-skipped job, the column
  //  will be 'completed' — so 'idle' uniquely identifies skipped runs.)
  const skipEnrichment =
    job.enrichment_status === 'idle' && job.status === 'signals_extracting'
  return (
    <JDExtractingView
      descriptionRaw={job.description_raw}
      descriptionEnriched={job.description_enriched ?? null}
      enrichmentStatus={job.enrichment_status}
      skipEnrichment={skipEnrichment}
      sseError={sseError}
    />
  )
}
```

(Note: when `useJob` is loading, we don't yet have `description_raw` so we render the view with empty text; the SSE / fetch will hydrate it within milliseconds.)

- [ ] **Step 2: Delete `LoadingSkeleton.tsx`**

Run: `git rm frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx`

- [ ] **Step 3: Remove the export from the panels barrel**

In `frontend/app/components/dashboard/jd-panels/index.ts`, remove the `export { LoadingSkeleton }` line.

- [ ] **Step 4: Search for any lingering references**

Run from `frontend/app`: `grep -rn "LoadingSkeleton" --include="*.tsx" --include="*.ts" .`
Expected: zero results (other than possibly the test file we already removed). Fix any that appear.

- [ ] **Step 5: Run lint + type-check + tests**

```
npm run lint
npm run type-check
npm run test
```
Expected: zero errors, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx frontend/app/components/dashboard/jd-panels/index.ts
git rm frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx
git commit -m "feat(jobs): replace LoadingSkeleton with phase-targeted JDExtractingView"
```

---

## Phase 5 — Frontend skip-enrichment toggle on the create form

Goal: expose the `skip_enrichment` toggle in the job-creation wizard. After this phase, a user can untoggle "Enrich JD with AI" before publishing and the backend will skip phase 1.

### Task 12: Add toggle to `createJobSchema` + wire to mutation

**Files:**
- Modify: `frontend/app/lib/api/jobs.ts`
- Modify: `frontend/app/app/(dashboard)/jobs/new/page.tsx`

- [ ] **Step 1: Update the API client request type**

In `frontend/app/lib/api/jobs.ts`, find the request body type for `jobsApi.create()` and add `skip_enrichment?: boolean` (optional — defaults to false on the server).

```typescript
// Find the type that the create() function accepts and update it:
export type JobPostingCreateRequest = {
  org_unit_id: string
  title: string
  description_raw: string
  project_scope_raw?: string | null
  target_headcount?: number | null
  // ... existing fields ...
  skip_enrichment?: boolean // ADD THIS
}
```

- [ ] **Step 2: Add the field to `createJobSchema` Zod**

In `frontend/app/app/(dashboard)/jobs/new/page.tsx`, modify `createJobSchema` (lines 61–90) to include the new field:

```typescript
const createJobSchema = z.object({
  // ... existing fields unchanged ...
  start_date_pref: z
    .enum(['immediate', 'within_30_days', 'within_60_days', 'flexible'])
    .nullable()
    .optional(),
  skip_enrichment: z.boolean().default(false), // ADD
})
```

Update the form's `defaultValues` (line 207) to include:
```typescript
skip_enrichment: false,
```

- [ ] **Step 3: Render a toggle in Step 2 of the wizard**

Add the following block at the END of the Step 2 form section (after the `project_scope_raw` Field, around line 588):

```tsx
<div
  className="flex items-start gap-3 rounded-md border p-3.5 mt-2"
  style={{
    background: 'var(--px-surface-2)',
    borderColor: 'var(--px-hairline)',
  }}
>
  <input
    type="checkbox"
    id="enrich-toggle"
    className="mt-0.5"
    checked={!form.watch('skip_enrichment')}
    onChange={(e) =>
      form.setValue('skip_enrichment', !e.target.checked, {
        shouldDirty: true,
      })
    }
  />
  <label htmlFor="enrich-toggle" className="flex-1 text-[13px]" style={{ color: 'var(--px-fg-2)' }}>
    <div style={{ color: 'var(--px-fg)', fontWeight: 600 }}>
      Enrich JD with Copilot
    </div>
    <div className="mt-0.5 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
      Off if your JD is already polished — Copilot will extract signals from it as-is.
    </div>
  </label>
</div>
```

- [ ] **Step 4: Forward `skip_enrichment` in the mutation**

Update the `createMutation` (around line 254) — add `skip_enrichment` to the body:

```typescript
const createMutation = useMutation({
  mutationFn: async (data: CreateJobForm) => {
    const token = await getFreshSupabaseToken()
    return jobsApi.create(token, {
      // ... existing fields ...
      start_date_pref: data.start_date_pref || null,
      skip_enrichment: data.skip_enrichment, // ADD
    })
  },
  // ... rest unchanged
})
```

- [ ] **Step 5: Run lint + type-check + tests**

```
npm run lint
npm run type-check
npm run test
```
Expected: zero errors, all tests pass.

- [ ] **Step 6: Manual smoke test**

In a browser:
1. Open `http://localhost:3000/jobs/new`
2. Fill basics, advance to Step 2.
3. Confirm the "Enrich JD with Copilot" toggle is visible and ON by default.
4. Submit with toggle ON → land on `/jobs/<id>` → see two-phase loading (phase 1 enrichment skeleton in center, then phase 2 with side panels loading).
5. Create another job with toggle OFF → land on `/jobs/<id>` → see phase 2 only (Raw JD in center, side panels loading); no Enriched JD tab.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/lib/api/jobs.ts frontend/app/app/(dashboard)/jobs/new/page.tsx
git commit -m "feat(jobs): expose skip_enrichment toggle on the create wizard"
```

---

## Self-Review

(Run this after the plan above is complete. This is a checklist for you, the plan author, against the spec.)

**1. Spec coverage:**
- §3 State model — Phase 1 reuses `enrichment_status` column; main FSM unchanged ✓ (Tasks 4, 7)
- §4 Backend — schemas (Task 6), service+actor split (Tasks 1, 4, 7), prompts (Task 2), AI schemas (Tasks 1, 5), SSE events via existing `JD_STATUS_CHANGED` with updated payload (Task 4 step 3) ✓
- §5 Frontend — form toggle (Task 12), center 3-tab control (Task 9), phase-targeted loading (Task 10), SSE consumer **already** handles the new payloads via `enrichment_status` field (no hook change needed) ✓
- §6 Provenance — explicitly out of scope, no tasks needed ✓
- §7 Error handling — phase 1 fail = main `signals_extraction_failed` (Task 4 `_run_enrichment` failure path); retry skips completed phase 1 (Task 4 idempotency check `if job.enrichment_status == "completed": return`) ✓
- §8 Testing — unit tests (Tasks 3, 6, 7), component tests (Tasks 8, 9, 10), manual E2E (Task 12 step 6) ✓
- §9 Implementation order — matches plan phases 1–5 ✓
- §10 Touch list — every file in the spec maps to a task ✓
- §11 Open questions — `enrichment_status` already returned by `JobPostingWithSnapshot` (line 226 of `schemas.py`); no work needed.

**2. Placeholder scan:** No "TBD", "TODO", or "implement later" in any task. Test code is full, not stubbed. Commands are exact. ✓

**3. Type consistency:** `JobPostingCreate.skip_enrichment` (Pydantic) ↔ `createJobSchema.skip_enrichment` (Zod) ↔ `JobPostingCreateRequest.skip_enrichment` (TS type). `EnrichmentStatus = 'idle' | 'streaming' | 'completed' | 'failed'` used consistently. `View = 'raw' | 'enriched' | 'signals'` used consistently in both `JDReviewShell` and `JDExtractingView`. ✓

**4. Dependencies between tasks:** Plan ordering enforces them — Task 1 (schemas) before Task 4 (actor uses them); Task 8 (Tabs primitive) before Tasks 9, 10 (use Tabs). ✓

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-28-jd-creation-flow-refinement.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
