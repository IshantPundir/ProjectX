# Deepgram nova-3 + en-IN keyterm migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the interview engine's default STT from Sarvam `saaras:v3` to Deepgram `nova-3` (`en-IN`) with deterministic per-session keyterm injection extracted from `SessionConfig`. Sarvam stays as a switchable alternate.

**Architecture:** A pure-functional `extract_keyterms(session_config) -> KeytermExtraction` reads `candidate.name`, `hiring_company_name`, `job_title`, `signals: list[str]`, and `role_summary` from `SessionConfig`. `stt_factory.py` plumbs the result into `build_stt_plugin(keyterms=…)`, which passes the list as `keyterm=…` to `deepgram.STT(...)` when the provider is Deepgram (Sarvam ignores it). A new `audio.stt.keyterms_applied` audit event records the applied list at session start.

**Tech Stack:** Python 3.13, Pydantic v2, LiveKit Agents, `livekit.plugins.deepgram`, pytest. Docker Compose for running tests.

**Spec:** `docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md` (commits `a927a69` … `9cd8fdd`).

**Out of scope (per spec Non-goals):** `orchestrator.py` continuation watcher (preserved verbatim), EOU/endpointing re-tuning, TTS, VAD, noise cancellation, mid-session keyterm updates, LLM-augmented extraction, Sarvam removal.

---

## Task 1: Register the `audio.stt.keyterms_applied` audit-event kind

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/event_kinds.py`
- Modify: `backend/nexus/app/modules/interview_engine/audit_events.py`

- [ ] **Step 1: Add the audit-event kind constant.**

Open `backend/nexus/app/modules/interview_engine/event_kinds.py`. Find the line `AUDIO_STT_TRANSCRIBED = "audio.stt.transcribed"`. Add immediately below it:

```python
AUDIO_STT_KEYTERMS_APPLIED = "audio.stt.keyterms_applied"
```

- [ ] **Step 2: Add the payload model.**

Open `backend/nexus/app/modules/interview_engine/audit_events.py`. The file already imports `from pydantic import BaseModel, Field` and `from typing import Any, Literal`. Append at the end of the file:

```python
# STT keyterm prompting (Phase 3D.deepgram-keyterm — 2026-05-19)
class STTKeytermsAppliedPayload(BaseModel):
    """One-shot record of the keyterm list passed to the STT plugin at session start.

    Emitted once per session, right before AgentSession construction.
    Provider is 'sarvam' (count=0, terms=[]) when Sarvam is toggled back via env,
    or 'deepgram' with the full list. Used for forensic debugging when STT quality
    regresses on a specific role — pairs with the existing audit envelope's
    model_versions.stt field.

    See docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md.
    """
    provider: Literal["sarvam", "deepgram"]
    count: int = Field(ge=0)
    terms: list[str]
    sources: dict[str, int]
```

- [ ] **Step 3: Smoke-import the new symbols.**

Run from the repo root:

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.interview_engine.event_kinds import AUDIO_STT_KEYTERMS_APPLIED; from app.modules.interview_engine.audit_events import STTKeytermsAppliedPayload; p = STTKeytermsAppliedPayload(provider='deepgram', count=2, terms=['MuleSoft', 'TIBCO'], sources={'signal_proper_nouns': 2}); print(AUDIO_STT_KEYTERMS_APPLIED, p.model_dump())"
```

Expected output:
```
audio.stt.keyterms_applied {'provider': 'deepgram', 'count': 2, 'terms': ['MuleSoft', 'TIBCO'], 'sources': {'signal_proper_nouns': 2}}
```

If you see `ImportError` or `ValidationError`, fix before continuing.

- [ ] **Step 4: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/event_kinds.py backend/nexus/app/modules/interview_engine/audit_events.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): register audio.stt.keyterms_applied audit event

New event kind + STTKeytermsAppliedPayload Pydantic model. Foundational
for the upcoming Deepgram nova-3 keyterm migration — emitted once at
session start with the keyterms passed to the STT plugin.
EOF
)"
```

---

## Task 2: TDD the keyterm extractor — basic cases + skeleton

**Files:**
- Create: `backend/nexus/tests/interview_engine/test_keyterms.py`
- Create: `backend/nexus/app/modules/interview_engine/keyterms.py`

- [ ] **Step 1: Create the test file with helper + first three tests.**

Create `backend/nexus/tests/interview_engine/test_keyterms.py` with the following content. The tests target the function and dataclass that don't exist yet — that's the whole point of TDD.

```python
"""Unit tests for the deterministic keyterm extractor.

Reference spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from app.modules.interview_engine.keyterms import (
    KeytermExtraction,
    extract_keyterms,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    SessionConfig,
    StageConfig,
)


def _make_session_config(
    *,
    candidate_name: str = "Ishant Pundir",
    hiring_company_name: str | None = "Workato",
    job_title: str = "Sr. Integration Engineer",
    signals: list[str] | None = None,
    role_summary: str = "",
) -> SessionConfig:
    """Build a minimal SessionConfig fixture for keyterm-extractor tests."""
    return SessionConfig(
        session_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        job_title=job_title,
        hiring_company_name=hiring_company_name,
        role_summary=role_summary,
        jd_text=None,
        seniority_level="senior",
        company=CompanyContext(about="x", industry="x", hiring_bar="x"),
        candidate=CandidateContext(name=candidate_name),
        stage=StageConfig(
            stage_id="00000000-0000-0000-0000-000000000004",
            stage_type="ai_screening",
            name="Bot Screening",
            duration_minutes=15,
            difficulty="hard",
            questions=[],
            advance_behavior="auto_advance",
        ),
        signals=signals or [],
        signal_metadata=[],
    )


class TestExtractKeytermsMinimal:
    def test_returns_keyterm_extraction_dataclass(self) -> None:
        result = extract_keyterms(_make_session_config())
        assert isinstance(result, KeytermExtraction)
        assert isinstance(result.terms, list)
        assert isinstance(result.sources, dict)

    def test_minimum_input_emits_candidate_first_name_and_job_title(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                candidate_name="Ishant Pundir",
                hiring_company_name=None,
                signals=[],
                role_summary="",
            )
        )
        assert "Ishant" in result.terms
        assert "Pundir" not in result.terms
        assert "Sr. Integration Engineer" in result.terms
        assert result.sources["candidate_name"] == 1
        assert result.sources["job_title"] == 1

    def test_hiring_company_name_included_when_present(self) -> None:
        result = extract_keyterms(
            _make_session_config(hiring_company_name="Workato"),
        )
        assert "Workato" in result.terms
        assert result.sources["hiring_company"] == 1
```

- [ ] **Step 2: Run the tests, confirm they fail with ImportError.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.modules.interview_engine.keyterms'`.

- [ ] **Step 3: Create the keyterms module with a minimal implementation that passes the first three tests.**

Create `backend/nexus/app/modules/interview_engine/keyterms.py`:

```python
"""Deterministic per-session keyterm extractor for Deepgram nova-3.

Reads SessionConfig (already-flattened wire contract; see
app/modules/interview_runtime/schemas.py:181) and produces a bounded list of
proper-noun-like terms and signal phrases that bias Deepgram STT toward
role-specific vocabulary. Zero I/O, zero LiveKit deps, zero asyncpg.

Sarvam ignores the output (the STT factory simply does not forward it to the
Sarvam constructor). Deepgram receives the list as `keyterm=[...]` at
websocket open — see app/ai/realtime.py:_build_stt_deepgram.

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_runtime.schemas import SessionConfig

# Hard cap per Deepgram's "20-50 terms recommended" guidance and 500-token
# request limit. Order is insertion order — the most important fields
# (candidate, company, job) are added first so they always survive the cap.
_KEYTERM_CAP = 50


@dataclass(frozen=True)
class KeytermExtraction:
    """Result of the extractor — keyterms plus per-source attribution counts."""

    terms: list[str]
    sources: dict[str, int]


def extract_keyterms(session_config: SessionConfig) -> KeytermExtraction:
    terms: list[str] = []
    sources: dict[str, int] = {}

    def _add(term: str, source: str) -> None:
        if term and term not in terms and len(terms) < _KEYTERM_CAP:
            terms.append(term)
            sources[source] = sources.get(source, 0) + 1

    # Candidate first name only — last names noisy / collide
    first_name = session_config.candidate.name.split()[0] if session_config.candidate.name.strip() else ""
    if first_name:
        _add(first_name, "candidate_name")

    # Hiring company (e.g., "Workato")
    if session_config.hiring_company_name:
        _add(session_config.hiring_company_name, "hiring_company")

    # Job title as-is
    if session_config.job_title:
        _add(session_config.job_title, "job_title")

    return KeytermExtraction(terms=terms, sources=sources)
```

- [ ] **Step 4: Run the tests, confirm all three pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit the scaffolding.**

```bash
git add backend/nexus/tests/interview_engine/test_keyterms.py backend/nexus/app/modules/interview_engine/keyterms.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): keyterm extractor scaffold — candidate/company/job

Pure-functional extract_keyterms(SessionConfig) -> KeytermExtraction with
the three deterministic baseline fields (candidate first name, hiring
company, job title) wired up and unit-tested. Signal-phrase expansion
and proper-noun extraction follow in the next task.
EOF
)"
```

---

## Task 3: TDD — signal-phrase expansion + proper-noun extraction

**Files:**
- Modify: `backend/nexus/tests/interview_engine/test_keyterms.py`
- Modify: `backend/nexus/app/modules/interview_engine/keyterms.py`

- [ ] **Step 1: Add the signal + role_summary tests.**

Append to `backend/nexus/tests/interview_engine/test_keyterms.py`:

```python
class TestSignalPhraseExpansion:
    def test_list_style_signal_emits_phrase_and_each_brand_individually(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["5+ years with MuleSoft, TIBCO, or Dell Boomi"],
            )
        )
        # Full phrase preserved
        assert "5+ years with MuleSoft, TIBCO, or Dell Boomi" in result.terms
        # Each brand pulled out as its own term too
        assert "MuleSoft" in result.terms
        assert "TIBCO" in result.terms
        assert "Boomi" in result.terms
        # Source counts reflect both
        assert result.sources["signal_phrases"] == 1
        assert result.sources["signal_proper_nouns"] >= 3

    def test_acronyms_extracted_from_signal_phrases(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["Hands-on RESTful and SOAP API design over JSON/XML contracts"],
            )
        )
        assert "SOAP" in result.terms
        assert "API" in result.terms
        assert "JSON" in result.terms
        assert "XML" in result.terms

    def test_hyphenated_term_extracted_as_one_token(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["API-led architecture principles"],
            )
        )
        assert "API-led" in result.terms

    def test_camel_case_brand_extracted(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["Working with iPaaS platforms"],
            )
        )
        assert "iPaaS" in result.terms


class TestRoleSummaryExtraction:
    def test_role_summary_yields_proper_nouns_only(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=[],
                role_summary="Delivery on ESB/iPaaS platforms (MuleSoft/TIBCO/Dell Boomi)",
            )
        )
        assert "ESB" in result.terms
        assert "iPaaS" in result.terms
        assert "MuleSoft" in result.terms
        assert "TIBCO" in result.terms
        assert "Boomi" in result.terms
        # The role_summary itself is NOT emitted as a phrase
        assert "Delivery on ESB/iPaaS platforms (MuleSoft/TIBCO/Dell Boomi)" not in result.terms
        assert result.sources["role_summary_proper_nouns"] >= 5
```

- [ ] **Step 2: Run the tests, confirm new ones fail.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: the 3 baseline tests still pass; the 5 new tests fail (the keyterms don't include MuleSoft etc. yet).

- [ ] **Step 3: Extend `keyterms.py` with signal expansion + proper-noun extraction.**

Replace the contents of `backend/nexus/app/modules/interview_engine/keyterms.py` with:

```python
"""Deterministic per-session keyterm extractor for Deepgram nova-3.

Reads SessionConfig (already-flattened wire contract; see
app/modules/interview_runtime/schemas.py:181) and produces a bounded list of
proper-noun-like terms and signal phrases that bias Deepgram STT toward
role-specific vocabulary. Zero I/O, zero LiveKit deps, zero asyncpg.

Sarvam ignores the output (the STT factory simply does not forward it to the
Sarvam constructor). Deepgram receives the list as `keyterm=[...]` at
websocket open — see app/ai/realtime.py:_build_stt_deepgram.

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.interview_runtime.schemas import SessionConfig

_KEYTERM_CAP = 50

# Token candidates: anything that starts with a letter and runs through
# letters/digits/hyphens. Stripping surrounding punctuation (parens, commas,
# slashes, periods) is implicit because the regex won't include them.
_TOKEN_CANDIDATE_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")

# A token qualifies as a "proper noun" keyterm if it matches one of:
#  - CapitalizedWord(-Word)*       Workato, Dell, API-led-Hyphenated
#  - ALL_CAPS acronym (>= 2 chars) API, ESB, SOAP
#  - camelCase (lowercase then Up) iPaaS, eBay
_PROPER_NOUN_RE = re.compile(
    r"^("
    r"[A-Z][a-zA-Z]+(?:-[a-zA-Z]+)+"  # Hyphenated (e.g., "API-led")
    r"|[A-Z][a-zA-Z]+"                 # Capitalized word (e.g., "MuleSoft")
    r"|[A-Z]{2,}"                      # ALL_CAPS acronym (e.g., "API")
    r"|[a-z][A-Z][a-zA-Z]*"            # camelCase (e.g., "iPaaS")
    r")$"
)

# Generic capitalized words that appear at sentence starts — emit them and you
# poison the keyterm budget with noise. Curated for English interview prose.
_GENERIC_CAPITALIZED: frozenset[str] = frozenset({
    "The", "This", "That", "These", "Those",
    "We", "You", "It", "They", "He", "She",
    "In", "On", "At", "For", "As", "By", "Of", "To", "With", "From", "Into",
    "And", "Or", "But", "If", "When", "While",
    "How", "What", "Where", "Why", "Who", "Which",
    "Their", "Your", "Our",
    "Build", "Use", "Make",
})


@dataclass(frozen=True)
class KeytermExtraction:
    """Result of the extractor — keyterms plus per-source attribution counts."""

    terms: list[str]
    sources: dict[str, int]


def _extract_proper_nouns(text: str) -> list[str]:
    """Pull proper-noun-looking tokens out of a free-text phrase, in order.

    Duplicates within the text are deduped against the first-seen casing.
    Generic capitalized sentence-starters are dropped.
    """
    seen: dict[str, str] = {}
    for candidate in _TOKEN_CANDIDATE_RE.findall(text):
        if candidate in _GENERIC_CAPITALIZED:
            continue
        if not _PROPER_NOUN_RE.match(candidate):
            continue
        key = candidate.lower()
        if key not in seen:
            seen[key] = candidate
    return list(seen.values())


def extract_keyterms(session_config: SessionConfig) -> KeytermExtraction:
    terms: list[str] = []
    sources: dict[str, int] = {}

    def _add(term: str, source: str) -> None:
        if not term:
            return
        if len(terms) >= _KEYTERM_CAP:
            return
        # Case-insensitive dedupe — keep first-seen casing
        if any(t.lower() == term.lower() for t in terms):
            return
        terms.append(term)
        sources[source] = sources.get(source, 0) + 1

    # 1. Candidate first name
    if session_config.candidate.name.strip():
        first_name = session_config.candidate.name.split()[0]
        _add(first_name, "candidate_name")

    # 2. Hiring company
    if session_config.hiring_company_name:
        _add(session_config.hiring_company_name, "hiring_company")

    # 3. Job title as-is
    if session_config.job_title:
        _add(session_config.job_title, "job_title")

    # 4. Each signal phrase: emit the full phrase AND extract proper nouns
    for phrase in session_config.signals:
        cleaned = phrase.strip()
        if cleaned:
            _add(cleaned, "signal_phrases")
        for noun in _extract_proper_nouns(phrase):
            _add(noun, "signal_proper_nouns")

    # 5. Proper nouns from role_summary (NOT the prose itself)
    if session_config.role_summary:
        for noun in _extract_proper_nouns(session_config.role_summary):
            _add(noun, "role_summary_proper_nouns")

    return KeytermExtraction(terms=terms, sources=sources)
```

- [ ] **Step 4: Run the tests, confirm all 8 pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/tests/interview_engine/test_keyterms.py backend/nexus/app/modules/interview_engine/keyterms.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): keyterm extractor — signal-phrase + proper-noun expansion

Signal phrases like "5+ years with MuleSoft, TIBCO, or Dell Boomi" now
emit both the full phrase (preserves Deepgram's multi-word boost) and
each brand individually (MuleSoft, TIBCO, Boomi). Proper-noun regex
catches ALL_CAPS acronyms (API, ESB, SOAP), CamelCase (MuleSoft, Workato),
camelCase brands (iPaaS), and hyphenated terms (API-led). role_summary
contributes proper nouns only — the prose itself is too noisy as a phrase.
EOF
)"
```

---

## Task 4: TDD — dedupe, cap enforcement, generic-capitalized filtering

**Files:**
- Modify: `backend/nexus/tests/interview_engine/test_keyterms.py`

- [ ] **Step 1: Add the remaining behavior tests.**

Append to `backend/nexus/tests/interview_engine/test_keyterms.py`:

```python
class TestDedupeAndCap:
    def test_case_insensitive_dedupe_keeps_first_seen_casing(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["MuleSoft is great", "mulesoft is also fine"],
            )
        )
        # Both signals mention the brand, but it should appear exactly once
        # with the FIRST-seen casing preserved.
        lowercased = [t.lower() for t in result.terms]
        assert lowercased.count("mulesoft") == 1
        assert "MuleSoft" in result.terms  # first-seen
        assert "mulesoft" not in result.terms  # lower-seen is dropped

    def test_cap_at_fifty_terms(self) -> None:
        # Build 200 distinct signal phrases, each containing a unique brand-like
        # proper noun. Extractor should emit exactly 50 (insertion order).
        many_signals = [f"Experience with Brand{i}Corp platform" for i in range(200)]
        result = extract_keyterms(_make_session_config(signals=many_signals))
        assert len(result.terms) == 50
        # First-3 baseline fields take 3 slots: candidate, hiring_company, job_title.
        # The remaining 47 are filled by signals (phrase first, then nouns).
        assert "Ishant" in result.terms      # first baseline
        assert "Workato" in result.terms     # second baseline
        assert "Sr. Integration Engineer" in result.terms  # third baseline


class TestGenericCapitalizedFiltering:
    def test_sentence_starting_capitalized_filler_dropped(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["The candidate should know about MuleSoft. This is critical."],
            )
        )
        # Generic capitalized sentence-starters NOT in terms
        assert "The" not in result.terms
        assert "This" not in result.terms
        # Real brand IS in terms
        assert "MuleSoft" in result.terms

    def test_single_letter_and_pure_digit_tokens_dropped(self) -> None:
        result = extract_keyterms(
            _make_session_config(
                signals=["Use B and 5 with MuleSoft for 2024"],
            )
        )
        # Single letters and pure digits never qualify as proper nouns —
        # the regex requires at least one letter and ALL_CAPS needs >=2 chars.
        assert "B" not in result.terms
        assert "5" not in result.terms
        assert "2024" not in result.terms
        assert "MuleSoft" in result.terms
```

- [ ] **Step 2: Run the tests, confirm all 12 pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: 12 passed. If `test_cap_at_fifty_terms` fails because too many or too few terms, inspect — most likely the cap logic in `_add` interacts incorrectly with the case-insensitive dedupe in a way the implementation already handles correctly; if not, fix the ordering of the `len(terms) >= _KEYTERM_CAP` check vs the dedupe check.

- [ ] **Step 3: Commit.**

```bash
git add backend/nexus/tests/interview_engine/test_keyterms.py
git commit -m "$(cat <<'EOF'
test(interview-engine): keyterm extractor — dedupe, cap, generic-word filtering

Verifies case-insensitive dedupe preserves first-seen casing, the 50-term
cap is enforced order-stably (baseline fields always survive), and generic
sentence-starting capitalized words (The, This, ...) are filtered.
EOF
)"
```

---

## Task 5: Snapshot test against a committed `SessionConfig` fixture

**Files:**
- Create: `backend/nexus/tests/interview_engine/fixtures/session_config_mulesoft_sample.json`
- Modify: `backend/nexus/tests/interview_engine/test_keyterms.py`

- [ ] **Step 1: Generate a real `SessionConfig` JSON from the sample build_session_config output.**

The raw `tmp/interview_context.json` is a dict, not a `SessionConfig`. We need to project it into the wire-format shape and serialize it. Run from the repo root:

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python - <<'PY'
import json
from pathlib import Path

raw = json.loads(Path("/app/../tmp/interview_context.json").read_text()) if Path("/app/../tmp/interview_context.json").exists() else json.loads(Path("tmp/interview_context.json").read_text())

# Project the raw build_session_config dict into the SessionConfig wire shape.
# Mirrors what interview_runtime/service.py would produce at run time.
projected = {
    "session_id": "00000000-0000-0000-0000-000000000001",
    "job_id": raw["job"]["id"],
    "candidate_id": raw["candidate"]["id"],
    "job_title": raw["job"]["title"],
    "hiring_company_name": raw["company_profile"]["org_unit_name"],
    "role_summary": raw["signal_snapshot"]["role_summary"],
    "jd_text": raw["job"].get("description_enriched"),
    "seniority_level": raw["signal_snapshot"]["seniority_level"],
    "company": {
        "about": raw["company_profile"]["about"],
        "industry": raw["company_profile"]["industry"],
        "company_stage": "",
        "hiring_bar": raw["company_profile"]["hiring_bar"],
    },
    "candidate": {"name": raw["candidate"]["name"]},
    "stage": {
        "stage_id": raw["stage"]["id"],
        "stage_type": raw["stage"]["stage_type"],
        "name": raw["stage"]["name"],
        "duration_minutes": raw["stage"]["duration_minutes"],
        "difficulty": raw["stage"]["difficulty"],
        "questions": [],
        "advance_behavior": raw["stage"]["advance_behavior"],
    },
    "signals": [s["value"] for s in raw["signal_snapshot"]["signals"]],
    "signal_metadata": [],
}

# Validate it round-trips through the real Pydantic model so the test
# fixture is guaranteed-loadable.
from app.modules.interview_runtime.schemas import SessionConfig
sc = SessionConfig.model_validate(projected)

out = Path("backend/nexus/tests/interview_engine/fixtures/session_config_mulesoft_sample.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(sc.model_dump_json(indent=2))
print("wrote", out)
PY
```

Expected: a new file at `backend/nexus/tests/interview_engine/fixtures/session_config_mulesoft_sample.json` containing the validated `SessionConfig` JSON.

- [ ] **Step 2: Add the snapshot test (initially empty `EXPECTED_TERMS` — we'll fill after first run).**

Append to `backend/nexus/tests/interview_engine/test_keyterms.py`:

```python
import json
from pathlib import Path


_FIXTURE = Path(__file__).parent / "fixtures" / "session_config_mulesoft_sample.json"


class TestSnapshotMulesoftSample:
    """Regression guard against the MuleSoft Sr. Integration Engineer fixture.

    The expected list below was frozen on 2026-05-19 by running the extractor
    against the fixture and pinning the output. If you intentionally change
    the extractor's behavior, regenerate this list and commit the diff.
    """

    EXPECTED_TERMS: list[str] = [
        # FILL after first run — see Step 4 below.
    ]

    def test_snapshot_matches(self) -> None:
        from app.modules.interview_runtime.schemas import SessionConfig

        session_config = SessionConfig.model_validate_json(_FIXTURE.read_text())
        result = extract_keyterms(session_config)
        assert result.terms == self.EXPECTED_TERMS, (
            "Keyterm snapshot drift detected.\n\n"
            f"  Got     ({len(result.terms)}): {result.terms!r}\n\n"
            f"  Expected ({len(self.EXPECTED_TERMS)}): {self.EXPECTED_TERMS!r}"
        )
```

- [ ] **Step 3: Run the snapshot test to print the current output, then freeze it.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py::TestSnapshotMulesoftSample -v
```

The test will fail with an assertion error printing the actual `result.terms` list. Copy that list verbatim.

- [ ] **Step 4: Paste the frozen list into `EXPECTED_TERMS`.**

Edit `backend/nexus/tests/interview_engine/test_keyterms.py` and replace the `EXPECTED_TERMS: list[str] = [...]` placeholder with the actual list from Step 3. Each term on its own line for diff-readability:

```python
EXPECTED_TERMS: list[str] = [
    "Punar",
    "Workato",
    "Sr. Integration Engineer",
    "5+ years hands-on production experience with at least one iPaaS/ESB platform (MuleSoft, TIBCO, or Dell Boomi)",
    # ... (rest of the list as printed by Step 3)
]
```

- [ ] **Step 5: Sanity-check the frozen list before re-running.**

Read it over once. Verify it includes (at minimum) the candidate's first name (`Punar`), the hiring company (`Workato`), the job title (`Sr. Integration Engineer`), and the high-value tech brands (`MuleSoft`, `TIBCO`, `Boomi`, `Salesforce`, `API-led`, `iPaaS`, `ESB`). If any of these are missing, the extractor regex has a bug — debug before freezing.

- [ ] **Step 6: Re-run the snapshot test, confirm it passes.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: 13 passed.

- [ ] **Step 7: Commit.**

```bash
git add backend/nexus/tests/interview_engine/fixtures/session_config_mulesoft_sample.json backend/nexus/tests/interview_engine/test_keyterms.py
git commit -m "$(cat <<'EOF'
test(interview-engine): snapshot regression test for keyterm extractor

Frozen output of extract_keyterms() against a committed SessionConfig
fixture derived from the MuleSoft Sr. Integration Engineer sample. Future
extractor changes that move the output are surfaced by this test — the
engineer can either correct the regression or intentionally re-freeze.
EOF
)"
```

---

## Task 6: Wire keyterms through `realtime.py`

**Files:**
- Modify: `backend/nexus/app/ai/realtime.py`

- [ ] **Step 1: Update `build_stt_plugin` to accept an optional `keyterms` kwarg.**

Open `backend/nexus/app/ai/realtime.py`. Find the function at line 39 (`def build_stt_plugin() -> "_BaseSTT":`). Replace the function signature and body:

```python
def build_stt_plugin(keyterms: list[str] | None = None) -> "_BaseSTT":
    """Construct the realtime STT plugin selected by AIConfig.

    Provider is chosen by ``AIConfig.interview_stt_provider``
    (env: ``INTERVIEW_STT_PROVIDER``). Default ``deepgram`` (``nova-3``);
    ``sarvam`` (``saaras:v3``) is the switchable alternate.

    ``keyterms`` is the Deepgram nova-3 keyterm-prompting list (20-50
    role-specific terms, see spec
    docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
    Sarvam ignores the argument (its STT has no equivalent feature).
    Pass ``None`` (the default) to skip keyterm boosting entirely.
    """
    provider = ai_config.interview_stt_provider
    if provider == "sarvam":
        return _build_stt_sarvam()
    if provider == "deepgram":
        return _build_stt_deepgram(keyterms=keyterms)
    raise ValueError(
        f"Unknown interview_stt_provider {provider!r}; "
        "expected 'sarvam' or 'deepgram'."
    )
```

- [ ] **Step 2: Update `_build_stt_deepgram` to accept and forward the kwarg.**

In the same file, find `def _build_stt_deepgram() -> "_BaseSTT":` (around line 84). Replace its body:

```python
def _build_stt_deepgram(*, keyterms: list[str] | None = None) -> "_BaseSTT":
    """Deepgram STT (default). Auth via DEEPGRAM_API_KEY env.

    ``keyterms`` is forwarded as the Deepgram ``keyterm`` REST API parameter
    when non-empty. Nova-3 boosts recognition for each term (and multi-word
    phrase). The 500-token Deepgram per-request cap is enforced upstream
    by extract_keyterms (caps at 50 entries).
    """
    from livekit.plugins import deepgram

    kwargs: dict[str, object] = {
        "model": ai_config.interview_stt_model,
        "language": ai_config.interview_stt_language,
    }
    if keyterms:
        kwargs["keyterm"] = keyterms

    logger.info(
        "ai.realtime.stt.built",
        provider="deepgram",
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
        keyterm_count=len(keyterms) if keyterms else 0,
    )
    return deepgram.STT(**kwargs)
```

- [ ] **Step 3: Verify the file still parses.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.ai.realtime import build_stt_plugin; help(build_stt_plugin)"
```

Expected: prints the function's docstring without an ImportError.

- [ ] **Step 4: Commit.**

```bash
git add backend/nexus/app/ai/realtime.py
git commit -m "$(cat <<'EOF'
feat(ai/realtime): forward keyterms to Deepgram STT plugin

build_stt_plugin gains an optional keyterms list[str]; when the provider
is Deepgram and keyterms is non-empty, the list is passed as the
keyterm REST API parameter to deepgram.STT(...). Sarvam ignores it.
Logs keyterm_count for forensic correlation.
EOF
)"
```

---

## Task 7: Wire `stt_factory.py` to extract and return both STT and KeytermExtraction

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/stt_factory.py`

- [ ] **Step 1: Replace `stt_factory.py` with the new tuple-returning version.**

Open `backend/nexus/app/modules/interview_engine/stt_factory.py`. Replace its entire contents with:

```python
"""Per-session STT plugin factory — keyterm injection seam.

The factory function returns BOTH the STT plugin and the KeytermExtraction
so the caller (agent.py) can emit a single ``audio.stt.keyterms_applied``
audit event without re-running the extractor.

Sarvam ignores the keyterms argument (no equivalent feature); the STT
factory in app/ai/realtime.py is responsible for the provider dispatch.

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.realtime import build_stt_plugin
from app.modules.interview_engine.keyterms import KeytermExtraction, extract_keyterms
from app.modules.interview_runtime.schemas import SessionConfig

if TYPE_CHECKING:
    # Mirrors the lazy-import discipline in app/ai/realtime.py — the LiveKit
    # plugin packages must NOT be loaded at module import time.
    from livekit.agents.stt import STT as _BaseSTT


def build_stt_plugin_for_session(
    *, session_config: SessionConfig,
) -> tuple["_BaseSTT", KeytermExtraction]:
    """Build the STT plugin for one session AND return the keyterm extraction.

    The caller is expected to emit the
    ``audio.stt.keyterms_applied`` audit event using the returned
    ``KeytermExtraction`` before constructing AgentSession.
    """
    extraction = extract_keyterms(session_config)
    return build_stt_plugin(keyterms=extraction.terms), extraction
```

- [ ] **Step 2: Verify the module imports cleanly.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.interview_engine.stt_factory import build_stt_plugin_for_session; print(build_stt_plugin_for_session)"
```

Expected: prints the function repr without ImportError. (We don't call it without a real SessionConfig — that requires LiveKit plugins to be loadable, which is environment-dependent.)

- [ ] **Step 3: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/stt_factory.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): stt_factory returns (stt, KeytermExtraction) tuple

The seam now runs the keyterm extractor and forwards the list to
build_stt_plugin, returning both the constructed STT plugin and the full
KeytermExtraction so agent.py can emit one audit event without
re-computing the keyterms.
EOF
)"
```

---

## Task 8: Wire the audit event in `agent.py`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

- [ ] **Step 1: Add the new import next to existing imports from interview_engine.**

Open `backend/nexus/app/modules/interview_engine/agent.py`. Find the existing line that imports `EventCollector` (around line 89). Near it (or in the same import block from `app.modules.interview_engine.audit_events` and `event_kinds`), add the new symbols. If those modules aren't yet imported in agent.py, find a sensible place near other interview_engine imports and add:

```python
from app.modules.interview_engine.audit_events import STTKeytermsAppliedPayload
from app.modules.interview_engine.event_kinds import AUDIO_STT_KEYTERMS_APPLIED
```

(If agent.py already imports from those modules, just add the new names to the existing `import ... from ...` block.)

- [ ] **Step 2: Refactor the `AgentSession(stt=...)` call to unpack the tuple and emit the audit event.**

Find the block at line 467-485 (approximately) that looks like:

```python
session = AgentSession(
    stt=build_stt_plugin_for_session(session_config=session_config),
    llm=build_llm_plugin(),
    tts=tts_plugin,
    vad=build_vad(),
    turn_handling=TurnHandlingOptions(
        ...
    ),
)
```

Replace it with:

```python
stt_plugin, keyterm_extraction = build_stt_plugin_for_session(
    session_config=session_config,
)
event_collector.append(
    kind=AUDIO_STT_KEYTERMS_APPLIED,
    payload=STTKeytermsAppliedPayload(
        provider=ai_config.interview_stt_provider,
        count=len(keyterm_extraction.terms),
        terms=keyterm_extraction.terms,
        sources=keyterm_extraction.sources,
    ).model_dump(mode="json"),
)

session = AgentSession(
    stt=stt_plugin,
    llm=build_llm_plugin(),
    tts=tts_plugin,
    vad=build_vad(),
    turn_handling=TurnHandlingOptions(
        # Disabled: the structured agent drives every turn through
        # Judge → State → Speaker. Framework-level preemption would
        # only race the orchestrator.
        preemptive_generation={"enabled": False},
        endpointing={
            "mode": settings.engine_endpointing_mode,
            "min_delay": settings.engine_endpointing_min_delay,
            "max_delay": settings.engine_endpointing_max_delay,
        },
        interruption=build_interruption_options(),
        turn_detection=build_turn_detector(),
    ),
)
```

NOTE: The exact structure of `TurnHandlingOptions(...)` must match what was in the file before — copy from the existing code. Only the STT line and the new audit-event emission change. Keep `turn_detection`, `preemptive_generation`, `endpointing`, and `interruption` exactly as they were.

- [ ] **Step 3: Verify `ai_config` is already in scope at this point.**

Search upward from the modification site for `ai_config` — it should already be imported and used to populate `model_versions["llm"]`, `model_versions["stt"]`, etc. (lines ~410-420). If `ai_config` is not in scope at the new emission site, add `from app.ai.config import ai_config` at the top of the file.

- [ ] **Step 4: Verify the module imports cleanly.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.interview_engine import agent"
```

Expected: no error.

- [ ] **Step 5: Run the existing engine test suite to confirm no regression.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine -v -x
```

Expected: all existing tests still pass; the new keyterm tests (13 tests added across Tasks 2-5) also pass. If any existing test fails, inspect carefully — most likely the test built its own AgentSession fixture and now needs to receive the tuple shape. Fix the test by changing `stt = build_stt_plugin_for_session(...)` to `stt, _ = build_stt_plugin_for_session(...)`.

- [ ] **Step 6: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): emit audio.stt.keyterms_applied at session start

agent.py now unpacks the (stt, KeytermExtraction) tuple from
build_stt_plugin_for_session, emits one audit event with the full keyterm
list + per-source counts, then hands the STT to AgentSession. The
continuation watcher in orchestrator.py is untouched — only the
constructor wiring changes.
EOF
)"
```

---

## Task 9: Flip provider defaults

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Flip the Settings field defaults.**

Open `backend/nexus/app/config.py`. Find lines 460-462:

```python
    interview_stt_provider: Literal["sarvam", "deepgram"] = "sarvam"
    interview_stt_model: str = "saaras:v3"
    interview_stt_language: str = "en-IN"
```

Replace with:

```python
    interview_stt_provider: Literal["sarvam", "deepgram"] = "deepgram"
    interview_stt_model: str = "nova-3"
    interview_stt_language: str = "en-IN"
```

Leave `interview_stt_mode` and any other adjacent fields unchanged — `mode` is Sarvam-only and is still readable if someone toggles back via env.

- [ ] **Step 2: Flip `.env.example`.**

Open `backend/nexus/.env.example`. Find lines 164-172:

```
# STT — provider-switchable. Default sarvam (saaras:v3, en-IN, code-mix capable).
# To use Deepgram (rollback path): set INTERVIEW_STT_PROVIDER=deepgram AND
# INTERVIEW_STT_MODEL=nova-3 AND INTERVIEW_STT_LANGUAGE=en.
# Mode applies to Sarvam saaras:v3 only (transcribe | translate | verbatim |
# translit | codemix). Deepgram ignores INTERVIEW_STT_MODE.
INTERVIEW_STT_PROVIDER=sarvam
INTERVIEW_STT_MODEL=saaras:v3
INTERVIEW_STT_LANGUAGE=en-IN
INTERVIEW_STT_MODE=transcribe
```

Replace with:

```
# STT — provider-switchable. Default deepgram (nova-3, en-IN, with per-session
# keyterm prompting derived from SessionConfig — see
# docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
# To switch to Sarvam (alternate path, e.g. for code-mix Hindi/English candidates):
# set INTERVIEW_STT_PROVIDER=sarvam AND INTERVIEW_STT_MODEL=saaras:v3 AND
# INTERVIEW_STT_LANGUAGE=en-IN AND INTERVIEW_STT_MODE=codemix.
# Mode applies to Sarvam saaras:v3 only (transcribe | translate | verbatim |
# translit | codemix). Deepgram ignores INTERVIEW_STT_MODE.
INTERVIEW_STT_PROVIDER=deepgram
INTERVIEW_STT_MODEL=nova-3
INTERVIEW_STT_LANGUAGE=en-IN
INTERVIEW_STT_MODE=transcribe
```

- [ ] **Step 3: Smoke-check the new defaults.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.config import settings; print(settings.interview_stt_provider, settings.interview_stt_model, settings.interview_stt_language)"
```

Expected output:
```
deepgram nova-3 en-IN
```

If your local `.env` file overrides these (it currently does — `INTERVIEW_STT_PROVIDER=sarvam`), the printed values will reflect the override, not the new defaults. That's expected. The smoke check is: does the import work and produce a valid Literal value?

- [ ] **Step 4: Commit.**

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example
git commit -m "$(cat <<'EOF'
feat(config): flip default STT provider to deepgram/nova-3/en-IN

Sarvam remains as a switchable alternate (toggle via
INTERVIEW_STT_PROVIDER=sarvam in .env). Deepgram nova-3 with en-IN gains
the per-session keyterm boost wired in earlier commits, addressing the
tech-vocab transcription regressions visible in
engine-events/a0388c8e-...json.
EOF
)"
```

---

## Task 10: End-to-end startup smoke

**Files:** None modified. Verification only.

- [ ] **Step 1: Ensure the local `.env` is set for Deepgram.**

Inspect `backend/nexus/.env` (NOT `.env.example`). Confirm these lines exist and are set as expected:

```
DEEPGRAM_API_KEY=<your real key — already present per user>
INTERVIEW_STT_PROVIDER=deepgram
INTERVIEW_STT_MODEL=nova-3
INTERVIEW_STT_LANGUAGE=en-IN
```

If the local `.env` still has `INTERVIEW_STT_PROVIDER=sarvam`, update it to match. (The `.env.example` defaults will apply to fresh checkouts, but the existing local file overrides them.)

- [ ] **Step 2: Run the full engine test suite once more.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine -v
```

Expected: all pre-existing tests still pass + 13 new keyterm tests pass (3 baseline + 5 signal/role + 4 dedupe/cap/filter + 1 snapshot).

- [ ] **Step 3: Start the stack and inspect the engine boot log for the new audit event setup.**

```bash
docker compose -f backend/nexus/docker-compose.yml up -d
docker compose -f backend/nexus/docker-compose.yml logs nexus | grep -E "ai.realtime.stt.built|stt_keyterms"
```

Expected: the engine boots clean (no ImportError). No real audit event will fire until a session actually starts — that's part of the manual smoke test, not this gate.

- [ ] **Step 4: Tear down.**

```bash
docker compose -f backend/nexus/docker-compose.yml down
```

- [ ] **Step 5: Manual smoke test (user-driven, not gated here).**

This is the user's responsibility per the project's documented preference for manual-only validation of AI-agent behavior (`feedback_manual_agent_testing.md`). Run one real interview session against the live stack. In the engine-events JSON for that session, confirm:

1. `model_versions.stt` reads `deepgram/nova-3`.
2. Exactly one `audio.stt.keyterms_applied` event is present.
3. Its `payload.terms` list contains the candidate's first name, the hiring company, the job title, and 20-40 role-specific terms.
4. The candidate's actual speech transcripts (`audio.stt.transcribed` events) show correct spelling for the keyterm-listed brands (e.g., "MuleSoft" not "mule soft").
5. The continuation watcher still fires correctly on long candidate pauses — look for `turn.aborted_for_continuation` and `turn.stitched_continuation` events when the candidate pauses mid-sentence.

If any of these fail, file an issue referencing the spec and the failing session's correlation_id; do not regress to Sarvam from this PR — revert via env toggle.

- [ ] **Step 6 (optional): Tag the commit.**

If the user confirms the manual smoke passes, tag the final commit:

```bash
git tag -a phase-3d-deepgram-keyterm -m "Deepgram nova-3 + en-IN + keyterm migration complete"
```

---

## Self-Review Notes

**Spec coverage (each Non-goal + capability traced to a task):**
- New extractor (spec §1) → Tasks 2, 3, 4, 5
- stt_factory wiring (spec §2) → Task 7
- realtime.py kwargs (spec §2) → Task 6
- agent.py audit-event emission (spec §2, §4) → Task 8
- Default flip (spec §3) → Task 9
- Audit event (spec §4) → Tasks 1, 8
- Tests (spec §5) → Tasks 2, 3, 4, 5
- Continuation watcher preserved (spec Non-goal) → no task touches `orchestrator.py`; verified in Task 10 Step 5 of manual smoke

**Placeholder scan:** None. Every step contains actual code, exact commands, and concrete expected output.

**Type consistency:** `extract_keyterms` is consistently `(SessionConfig) -> KeytermExtraction`. `build_stt_plugin_for_session` is consistently `(*, session_config) -> tuple[_BaseSTT, KeytermExtraction]`. `build_stt_plugin` is consistently `(keyterms: list[str] | None = None) -> _BaseSTT`. Audit-event constant name (`AUDIO_STT_KEYTERMS_APPLIED`) and payload model (`STTKeytermsAppliedPayload`) match across files.
