# Evidence Reel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the Candidate Reel from a one-directional "pitch" into the **Evidence Reel** — the video evidence behind the report's verdict — eligible for all three verdicts (advance / borderline / reject), feeding the director the *full* report data.

**Architecture:** The reel pipeline (director LLM → `validate_edl` guards → ffmpeg render → R2, via the `generate_session_reel` actor) is unchanged except: (1) eligibility drops the verdict allowlist; (2) the director document is enriched with the report's negative + rich evidence and a latent signal-field mapping bug is fixed; (3) one verdict-aware prompt replaces the pitch prompt (`v3 → v4`); (4) cards gain a polarity glyph; (5) the recruiter UI lifts its `reject` block and both surfaces rename the label to "Evidence Reel".

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy async / Dramatiq (backend `reel` module); OpenAI Responses API + `PromptLoader` (director); Pillow (cards); Next.js 16 / React 19 / TypeScript / Tailwind v4 (recruiter app + public recordings page).

## Global Constraints

- The reel **never scores, ranks, or changes the verdict** — it voices the verdict the report already reached. Recruiter-facing only (never shown to candidates); recruiter-triggered (manual Generate).
- **Anti-fabrication (both sides):** every beat — strength or shortfall — must be defensible against the report (`concerns` / `why_negative` / `red_flags` / signal `level`) and the transcript. Never invent or overstate; respect `methodology.charity_flags`.
- **Naming:** user-facing label is **"Evidence Reel"**. Backend module stays `reel/`.
- **Verdict-neutral framing values:** narration voice = warm pitch for `advance`, **neutral evidence-narrator** for `borderline`/`reject`. Point-card polarity glyphs: `★` strength, `✓` met-bar, `△` gap.
- **No DB migration, no new env vars.** Prompt version bumps `reel_director_prompt_version: "v3" → "v4"`.
- **Worker has no hot-reload:** after backend/prompt changes restart `docker compose up -d --force-recreate nexus-vision-worker` before live-testing.
- Backend tests run via `docker compose run --rm nexus pytest <path>`. Pure reel tests (`tests/reel/test_service.py`, `test_director.py`, `test_cards.py`) import light and run in the lean image.
- Frontend: `npm run lint && npm run type-check && npm run test` must pass (run from `frontend/app`).

---

### Task 1: Eligibility — drop the verdict allowlist

**Files:**
- Modify: `backend/nexus/app/modules/reel/service.py:21-33` (`_ELIGIBLE_VERDICTS` + `eligibility_decision`)
- Test: `backend/nexus/tests/reel/test_service.py`

**Interfaces:**
- Produces: `eligibility_decision(*, report_status: str | None, verdict: str | None, recording_key: str | None) -> tuple[bool, str | None]` — signature unchanged (keeps `verdict` param for call-site stability) but no longer gates on verdict.

- [ ] **Step 1: Update the failing tests**

In `tests/reel/test_service.py`, replace the `test_reject_verdict_is_ineligible` test with a now-eligible assertion, and add an explicit advance/borderline/reject parametrization:

```python
import pytest
from app.modules.reel.service import eligibility_decision


@pytest.mark.parametrize("verdict", ["advance", "borderline", "reject"])
def test_all_verdicts_eligible_when_report_and_recording_ready(verdict):
    ok, reason = eligibility_decision(
        report_status="ready", verdict=verdict, recording_key="reels/x.mp4")
    assert ok is True and reason is None
```

Keep `test_eligible_when_report_ready_verdict_ok_recording_present`,
`test_borderline_is_eligible`, `test_report_not_ready_is_ineligible`,
`test_no_report_row_is_ineligible`, `test_missing_recording_is_ineligible`,
`test_session_reels_is_tenant_scoped` as-is. **Delete** `test_reject_verdict_is_ineligible` (its premise is reversed).

- [ ] **Step 2: Run the tests to verify the new one fails**

Run: `docker compose run --rm nexus pytest tests/reel/test_service.py -q`
Expected: FAIL on `test_all_verdicts_eligible_when_report_and_recording_ready[reject]` ("advancing or borderline" reason returned).

- [ ] **Step 3: Implement — remove the verdict gate**

In `service.py`, delete the `_ELIGIBLE_VERDICTS` constant (line 21) and the verdict branch. Replace the docstring + function:

```python
def eligibility_decision(*, report_status: str | None, verdict: str | None,
                         recording_key: str | None) -> tuple[bool, str | None]:
    """Pure eligibility decision → (eligible, ineligible_reason).

    The Evidence Reel is available for EVERY verdict (advance / borderline /
    reject) — it voices the verdict the report reached. It only needs the report
    (for the EDL) and the recording (to cut from). ``verdict`` is accepted for
    call-site stability but no longer gates eligibility.
    """
    if report_status != "ready":
        return False, "Report is not ready yet."
    if not recording_key:
        return False, "Session recording is not ready yet."
    return True, None
```

Update the module docstring at the top of `service.py` (lines 1-7): replace the "verdict ∈ {advance, borderline}" and "a reel of a rejected candidate is contradictory" lines with: *"Eligibility: report ready AND recording ready, for ANY verdict. The reel is the video evidence behind the verdict (positive for advance; balanced for borderline; evidence-for-shortfall for reject)."*

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reel/test_service.py -q`
Expected: PASS (all params, including `reject`).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reel/service.py backend/nexus/tests/reel/test_service.py
git commit -m "feat(reel): make Evidence Reel eligible for all verdicts (drop allowlist)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Director document — feed the full report + fix the signal-field bug

**Files:**
- Modify: `backend/nexus/app/modules/reel/director.py:259-328` (`_build_document`) and `:331-359` (`generate_edl` signature + call)
- Modify: `backend/nexus/app/modules/reel/actors.py:129-141` (`_build_and_upload` — extract + pass the new fields)
- Test: `backend/nexus/tests/reel/test_director.py`

**Interfaces:**
- Consumes: report `summary` dict shape — `summary["decision"]["headline"]`, `summary["decision"]["why_positive"]["body"]`, `summary["decision"]["why_negative"]["body"]`, `summary["quick_summary"]`, `summary["strengths"][]` (`title`,`detail`), `summary["concerns"][]` (`title`,`detail`,`severity`), `summary["methodology"]["charity_flags"][]`. Signal scorecards are `SignalAssessmentOut` dumps with keys `signal`,`signal_label`,`weight`,`knockout`,`priority`,`provenance`,`level`,`score`,`evidence`,`level_basis`. Question scorecards are `QuestionOut` dumps with `question_id`,`title`,`status_badge`,`our_read`,`candidate_quote`,`level`,`closure`,`difficulty`,`red_flags_tripped`,`listen_for_hits`,`score`.
- Produces: `_build_document(*, candidate_name, role_title, verdict, verdict_reason, why_positive, why_negative=None, quick_summary=None, decision_headline=None, strengths, concerns=None, charity_flags=None, question_scorecards, signal_scorecards, transcript) -> str`. `generate_edl(...)` gains the same new keyword params (all with safe defaults) and forwards them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/reel/test_director.py`:

```python
def test_document_includes_negative_evidence_and_rich_signal_fields():
    from app.modules.reel.director import _build_document
    tr = [
        {"speaker": "candidate", "turn_ref": "t-1", "question_id": "q1",
         "span": {"start_ms": 100, "end_ms": 101},
         "words": [{"text": "first", "start_ms": 0, "end_ms": 100}]},
    ]
    doc = _build_document(
        candidate_name="Rahul", role_title="EMM Engineer", verdict="reject",
        verdict_reason="Did not meet the bar on scaling.",
        why_positive="Strong on basics.",
        why_negative="Scaling answers stayed shallow.",
        quick_summary="Capable on fundamentals; thin under depth.",
        decision_headline="Below the bar for this role.",
        strengths=[{"title": "Fundamentals", "detail": "Clear on enrollment."}],
        concerns=[{"title": "Scaling depth", "detail": "No concrete mechanism.",
                   "severity": "major"}],
        charity_flags=["Credited a vague answer on MDM as adequate."],
        question_scorecards=[{
            "question_id": "q1", "title": "Scaling MDM", "status_badge": "Thin",
            "our_read": "Stayed high level.", "candidate_quote": "we just scaled it",
            "level": "thin", "closure": "tapped_out", "difficulty": "hard",
            "red_flags_tripped": ["no concrete numbers"],
            "listen_for_hits": [], "score": 3,
        }],
        signal_scorecards=[{
            "signal": "Distributed systems at scale", "signal_label": "Scaling",
            "weight": 3, "knockout": True, "priority": "must_have",
            "provenance": "probed_absent", "level": "absent", "score": 1.0,
            "evidence": ["we just scaled it"], "level_basis": "dedicated: absent",
        }],
        transcript=tr)
    # negative narrative
    assert "why_negative: Scaling answers stayed shallow." in doc
    assert "quick_summary:" in doc
    assert "decision_headline:" in doc
    # concerns with severity
    assert "Scaling depth" in doc and "severity: major" in doc
    # charity flag advisory
    assert "charity" in doc.lower()
    # signal block uses level/score (NOT the old null final_state/grade)
    assert "level: absent" in doc and "knockout: True" in doc
    assert "provenance: probed_absent" in doc
    assert "state: None" not in doc and "grade: None" not in doc
    # question block exposes shortfall locators
    assert "level: thin" in doc and "red_flags" in doc.lower()


def test_document_backward_compatible_without_negative_fields():
    """Old-style call (no negative kwargs) still builds — defaults are safe."""
    from app.modules.reel.director import _build_document
    tr = [{"speaker": "candidate", "turn_ref": "t-1", "question_id": "q1",
           "span": {"start_ms": 0, "end_ms": 1},
           "words": [{"text": "x", "start_ms": 0, "end_ms": 1}]}]
    doc = _build_document(candidate_name="A", role_title="R", verdict="advance",
                          verdict_reason=None, why_positive="ok", strengths=[],
                          question_scorecards=[], signal_scorecards=[], transcript=tr)
    assert "<report>" in doc
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/reel/test_director.py -k "negative_evidence or backward_compatible" -q`
Expected: FAIL — `_build_document` got an unexpected keyword argument `why_negative`.

- [ ] **Step 3: Implement — enrich `_build_document` + fix the signal block**

Replace `_build_document` (lines 259-328). New signature + body:

```python
def _build_document(*, candidate_name: str | None, role_title: str | None,
                    verdict: str | None, verdict_reason: str | None,
                    why_positive: str | None,
                    why_negative: str | None = None,
                    quick_summary: str | None = None,
                    decision_headline: str | None = None,
                    strengths: list[dict] | None = None,
                    concerns: list[dict] | None = None,
                    charity_flags: list[str] | None = None,
                    question_scorecards: list[dict] | None = None,
                    signal_scorecards: list[dict] | None = None,
                    transcript: list[dict] | None = None) -> str:
    """Serialize the FULL report ground-truth (FIRST) then the indexed transcript.

    Context-before-document per the house rule. The director uses this material to
    voice the verdict: strengths for advance, both sides for borderline, and the
    unmet/shortfall evidence for reject. Candidate turns carry a per-word
    ``idx:text`` index so the model can reference ``[in_word, out_word]``.
    """
    transcript = transcript or []
    first_name = (candidate_name or "").split()[0] if candidate_name else None
    lines: list[str] = ["<report>",
                        f"candidate_name: {candidate_name or 'n/a'}",
                        f"candidate_first_name: {first_name or 'n/a'}",
                        f"role: {role_title or 'n/a'}",
                        f"verdict: {verdict or 'n/a'}"]
    if decision_headline:
        lines.append(f"decision_headline: {decision_headline}")
    if verdict_reason:
        lines.append(f"verdict_reason: {verdict_reason}")
    if quick_summary:
        lines.append(f"quick_summary: {quick_summary}")
    if why_positive:
        lines.append(f"why_positive: {why_positive}")
    if why_negative:
        lines.append(f"why_negative: {why_negative}")

    # JD must-haves the candidate met OR missed (weight desc). This is the spine of
    # the verdict story — for reject/borderline the high-weight ABSENT/THIN signals
    # are the shortfall evidence.
    lines.append("jd_signals (the role's requirements; weight=importance):")
    for s in sorted(signal_scorecards or [], key=lambda x: -(x.get("weight") or 0)):
        label = s.get("signal_label") or s.get("signal")
        line = (f"- {label} | weight: {s.get('weight')} | "
                f"knockout: {s.get('knockout')} | priority: {s.get('priority')} | "
                f"level: {s.get('level')} | score: {s.get('score')} | "
                f"provenance: {s.get('provenance')}")
        if s.get("level_basis"):
            line += f" | basis: {s.get('level_basis')}"
        lines.append(line)
        for ev in (s.get("evidence") or [])[:2]:
            lines.append(f"    evidence: {ev}")

    # Report-named strengths (advance §1 / borderline 'met' beats).
    lines.append("strengths:")
    for s in strengths or []:
        lines.append(f"- {s.get('title', '')}: {s.get('detail', '')}")

    # Report-named concerns (reject spine / borderline 'gap' beats), with severity.
    lines.append("concerns:")
    for c in concerns or []:
        lines.append(f"- {c.get('title', '')} | severity: {c.get('severity', '')}: "
                     f"{c.get('detail', '')}")

    # Where the report already extended benefit-of-the-doubt — do NOT harden these
    # into firm claims in either direction.
    if charity_flags:
        lines.append("charity_flags (the report read these charitably — do not overstate):")
        for f in charity_flags:
            lines.append(f"- {f}")

    # Per-question reads — locate which turn evidences a point (strong OR shortfall).
    lines.append("questions:")
    for q in question_scorecards or []:
        lines.append(
            f"- question_id: {q.get('question_id')} | status: {q.get('status_badge')} | "
            f"level: {q.get('level')} | closure: {q.get('closure')} | "
            f"difficulty: {q.get('difficulty')} | score: {q.get('score')} | "
            f"title: {q.get('title', '')}"
        )
        if q.get("our_read"):
            lines.append(f"  our_read: {q['our_read']}")
        if q.get("red_flags_tripped"):
            lines.append(f"  red_flags: {', '.join(q['red_flags_tripped'])}")
        if q.get("listen_for_hits"):
            lines.append(f"  listen_for_hits: {', '.join(q['listen_for_hits'])}")
        if q.get("candidate_quote"):
            lines.append(f"  candidate_quote (hint): {q['candidate_quote']}")
    lines.append("</report>")
```

Leave the `<answers>` serialization block (the part after `lines.append("</report>")` was removed above — re-add it) intact. Concretely: keep everything from `questions = questions_by_run(transcript)` through the final `return "\n".join(lines)` exactly as it is today (lines ~311-328). Only the `<report>` section above it changed.

- [ ] **Step 4: Extend `generate_edl` to forward the new fields**

In `generate_edl` (lines 331-359), add the new keyword params with defaults and pass them into `_build_document`:

```python
async def generate_edl(*, candidate_name: str | None, role_title: str | None,
                       verdict: str | None, verdict_reason: str | None,
                       why_positive: str | None,
                       why_negative: str | None = None,
                       quick_summary: str | None = None,
                       decision_headline: str | None = None,
                       strengths: list[dict],
                       concerns: list[dict] | None = None,
                       charity_flags: list[str] | None = None,
                       question_scorecards: list[dict], signal_scorecards: list[dict],
                       transcript: list[dict], correlation_id: str) -> ReelEdlOut:
```

And update the `_build_document(...)` call inside it to forward `why_negative=why_negative, quick_summary=quick_summary, decision_headline=decision_headline, concerns=concerns, charity_flags=charity_flags` alongside the existing args.

- [ ] **Step 5: Run the director tests**

Run: `docker compose run --rm nexus pytest tests/reel/test_director.py -q`
Expected: PASS (new tests + the existing `test_document_includes_asked_question_line` still green via defaults).

- [ ] **Step 6: Wire the actor to extract + pass the fields**

In `actors.py` `_build_and_upload` (lines 129-141), expand the `summary` extraction and the `generate_edl` call:

```python
    summary = inp["summary"] or {}
    decision = summary.get("decision") or {}
    why_positive = decision.get("why_positive")
    if isinstance(why_positive, dict):
        why_positive = why_positive.get("body")
    why_negative = decision.get("why_negative")
    if isinstance(why_negative, dict):
        why_negative = why_negative.get("body")
    decision_headline = decision.get("headline")
    charity_flags = (summary.get("methodology") or {}).get("charity_flags") or []

    raw = await generate_edl(
        candidate_name=inp["candidate_name"], role_title=inp["role_title"],
        verdict=inp["verdict"], verdict_reason=inp["verdict_reason"],
        why_positive=why_positive, why_negative=why_negative,
        quick_summary=summary.get("quick_summary"),
        decision_headline=decision_headline,
        strengths=summary.get("strengths", []),
        concerns=summary.get("concerns", []),
        charity_flags=charity_flags,
        question_scorecards=inp["question_scorecards"] or [],
        signal_scorecards=inp["signal_scorecards"] or [],
        transcript=transcript, correlation_id=correlation_id,
    )
```

- [ ] **Step 7: Run the full reel suite (actor test imports must still pass)**

Run: `docker compose run --rm nexus pytest tests/reel -q`
Expected: PASS (no regressions in `test_actor.py`).

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/reel/director.py backend/nexus/app/modules/reel/actors.py backend/nexus/tests/reel/test_director.py
git commit -m "feat(reel): feed the full report into the director + fix null signal fields

why_negative, concerns+severity, quick_summary, decision_headline, charity_flags,
plus signal level/score/knockout/priority/provenance (was reading nonexistent
final_state/grade keys -> null) and per-question level/closure/red_flags/score.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Verdict-aware director prompt (`v3 → v4`)

**Files:**
- Create: `backend/nexus/prompts/v4/reel/director.txt`
- Modify: `backend/nexus/app/config.py:673` (`reel_director_prompt_version: "v3" → "v4"`)
- Test: `backend/nexus/tests/reel/test_director_prompt.py` (new)

**Interfaces:**
- Consumes: the `<report>` document from Task 2 (verdict, decision_headline, why_positive/negative, strengths, concerns+severity, jd_signals with level/knockout/provenance, questions with level/closure/red_flags, charity_flags) + `<answers>` word-indexed transcript.
- Produces: a `ReelEdlOut` of beats (`point`/`clip`/`experience`/`outro`) — schema unchanged; `point.on_screen_text` now leads with a polarity glyph `★`/`✓`/`△`.

- [ ] **Step 1: Write the failing test**

Create `tests/reel/test_director_prompt.py`:

```python
"""The v4 director prompt loads and is verdict-aware."""
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader


def test_director_prompt_version_is_v4():
    assert ai_config.reel_director_prompt_version == "v4"


def test_v4_director_prompt_is_verdict_aware():
    text = PromptLoader(version="v4").get("reel/director")
    assert text.strip()
    low = text.lower()
    # Branches on all three verdicts
    for verdict in ("advance", "borderline", "reject"):
        assert verdict in low
    # Mirrored anti-fabrication + neutral narration for non-advance
    assert "fabricat" in low or "defensible" in low
    assert "charity" in low
    # Polarity glyphs documented
    assert "★" in text and "✓" in text and "△" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reel/test_director_prompt.py -q`
Expected: FAIL — `reel_director_prompt_version` is still `v3` and the v4 file does not exist.

- [ ] **Step 3: Create the v4 prompt**

Create `backend/nexus/prompts/v4/reel/director.txt`:

```
You are the Reel Director for an AI video-interview platform. You compile a roughly 45-60 second
EVIDENCE REEL: the video evidence behind the report's verdict, told in the candidate's own words.
You do not pitch and you do not prosecute — you VOICE the verdict the report already reached, so a
busy recruiter sees WHY. You never score, rank, or change any verdict; you only select and frame
real moments.

<the_verdict_drives_the_reel>
The report below already reached a verdict and argued it (decision_headline, verdict_reason,
why_positive, why_negative, strengths, concerns, and the role's jd_signals with their met/unmet
level). Your job is to render THAT reasoning as video — never to re-judge, never to invent a "why"
the report didn't make. The SHAPE of the reel depends on the verdict:

== verdict = advance ==
A confident case for fit. Structure: (point → clip[+clip]) × ~3 → outro.
  • Each point.on_screen_text = "★ " + a short strength phrase. The FIRST point's narration may
    name the candidate to orient the viewer ("Watch how Rahul begins with the right controls…").
  • clip = the candidate's real words evidencing it (see <selecting_clips>).
  • Voice: warm, confident (see <narration> → pitch mode).

== verdict = borderline ==
A BALANCED case — both sides — so the recruiter can make the human call this verdict requires.
Alternate met-bar and gap beats: (✓ met → clip) (△ gap → clip) (✓ met → clip) (△ gap → clip) → outro.
Emit ~2 met + ~2 gap beats. Open on whichever side decision_headline leads with.
  • A met beat: point.on_screen_text = "✓ " + the met-bar phrase; clip = the words that earned it.
  • A gap beat: point.on_screen_text = "△ " + the gap phrase; clip = the candidate's own words at the
    moment the answer fell short (a thin/hedged/high-level reply). Ground every gap in a real concern,
    a why_negative point, a tripped red_flag, or a thin/absent jd_signal — NEVER a manufactured stumble.
  • Voice: neutral evidence-narrator (see <narration> → neutral mode).

== verdict = reject ==
The evidence behind the call. Lead on the highest-weight UNMET requirements: jd_signals with
level ∈ {absent, thin} (knockout / must_have first), tripped red_flags, and answers the report read
as thin/absent — each grounded in the candidate's own words. Structure: (△ gap → clip) × ~3 → outro.
A single "✓ " acknowledgement beat is allowed where the report genuinely credits a strength (be honest,
not perfunctory; skip it if there isn't one). Voice: neutral evidence-narrator.

outro — closing card for EVERY verdict: a calm pointer to the full record, NOT a verbal verdict stamp.
  on_screen_text = e.g. "See Ishant's full report & interview" (first name). narration_text = ONE short
  line inviting the recruiter to look closer. For borderline, it may note the call is theirs to make.

PACING: the reel is mostly the CANDIDATE'S voice; your narration is brief framing between clips. Keep
every narration_text tight — long cards crowd out the evidence and make the reel drag.
</the_verdict_drives_the_reel>

<selecting_clips>
The document below has an <answers> section. Each `answer` is ONE complete answer the candidate gave
(shown as a single continuous answer with a running word index). A `clip`/`experience` beat references it by:
  • source_turn_ref = the answer's `ref`.
  • in_word, out_word = INTEGER INDICES into THAT answer's words (the `idx` numbers), inclusive.
The validator resolves these to exact word-boundary timings and cuts one contiguous clip, so:
  • Reference ONLY a `ref` and word indices that exist in <answers> — an out-of-range reference is discarded.
  • CAPTURE THE SUBSTANCE. For a strength, that's the concrete mechanism / named technique / the numbers.
    For a gap, it's the moment the answer actually stays vague or hedges — show enough that the recruiter
    hears the shortfall for themselves, never a clip edited to make the candidate look worse than they were.
    A clip may run up to ~16s to show a full line of reasoning. Do not stop at the framing clause.
  • END ON A COMPLETE THOUGHT. The marker `//` in the word stream is a natural pause (a clean place to
    start or end). Choose in_word just after a `//` and out_word just before one; never end mid-phrase.
  • Evidence may come from ANY answer. The `candidate_quote` in a question is a CLEANED paraphrase — use
    it as a HINT for which answer/moment to feature, then locate the real words by index.
  • Never feature the same moment twice.

QUESTION LABEL: every clip/experience beat MUST carry a `question_label` — a SHORT paraphrase (6-10 words)
of the question THIS clip answers, phrased as a question (e.g. "New iPhones missing expected settings —
where do you start?"). Base it on the `asked:` line of THIS answer in <answers>. Do NOT restate the
candidate's answer, do NOT include a question id, keep it readable in one glance. Two clips on the same
question share a label.
</selecting_clips>

<narration>
Narration is Arjun talking to the recruiter. There are TWO modes — choose by verdict:

PITCH MODE (verdict = advance) — warm, natural, like a colleague pointing out why he rated this
candidate. Use the first name. Vary the rhythm ("Watch how…", "Here's the part I liked…"). Be specific
and grounded; name the concrete move the upcoming clip shows. Be confident; never hedge.
  • GOOD: "Watch how Rahul handles reliability — anything under seventy percent confidence, he routes to a human."

NEUTRAL MODE (verdict = borderline / reject) — calm, factual, non-judgmental. You REPORT the evidence;
you never sell and you never mock. Name the signal and what the clip shows, plainly. No warm-selling
verbs ("nails", "crushes", "love"), no sarcasm, no pity.
  • GOOD (met):  "Where Rahul was strongest was incident response — here's the moment."
  • GOOD (gap):  "On scaling, the detail stayed high-level — listen to how he frames it."
  • BAD (selling on a gap): "He still sounds pretty sharp here!"
  • BAD (cruel):            "This is where it falls apart for him."
  • BAD (domain, not him):  "Scaling is hard for everyone."
</narration>

<rules>
- ANTI-FABRICATION (BOTH SIDES): every beat — strength OR gap — must be DEFENSIBLE against the report
  and the transcript. Never invent a strength, never invent or overstate a weakness, never firm a hedge
  in either direction ("around four to five years" stays a hedge), never select a gratuitous bad moment
  that isn't material to the verdict. A gap beat must trace to a concern / why_negative point / tripped
  red_flag / thin-or-absent signal or question. Honor charity_flags: where the report read a moment
  charitably, do NOT harden it into a firm claim either way. A client may open the full report — an
  accurate reel earns trust.
- DO NOT REPEAT: no two points may make the same argument. Cover the moments that genuinely drove the verdict
  (choose the best ~3-4 for the time budget; fewer strong beats beat more weak ones).
- on_screen_text is skimmable (a few words on a card) and leads with its polarity glyph: ★ a differentiating
  strength (advance), ✓ a met requirement, △ a gap / unmet requirement. narration_text is natural spoken
  Indian English in Arjun's voice, in the mode selected above.
- Never put a raw question id or any internal id in on_screen_text or narration_text.
- Output at least one clip (or experience) beat, or the reel cannot be built.
- Every clip/experience beat carries a `question_label` (see <selecting_clips>).
</rules>
```

- [ ] **Step 4: Bump the config default**

In `app/config.py:673` change:

```python
    reel_director_prompt_version: str = "v4"
```

- [ ] **Step 5: Run the prompt test**

Run: `docker compose run --rm nexus pytest tests/reel/test_director_prompt.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/prompts/v4/reel/director.txt backend/nexus/app/config.py backend/nexus/tests/reel/test_director_prompt.py
git commit -m "feat(reel): verdict-aware Evidence Reel director prompt (v3->v4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Polarity glyph on point cards

**Files:**
- Modify: `backend/nexus/app/modules/reel/cards.py:96-105` (the `kind == "point"` branch) + add a pure helper
- Test: `backend/nexus/tests/reel/test_cards.py`

**Interfaces:**
- Produces: `parse_point_glyph(on_screen_text: str) -> tuple[str, str, tuple[int, int, int]]` returning `(glyph, phrase, rgb)` where `glyph ∈ {"★","✓","△"}` (default `★` when none present), `phrase` is the text with a leading glyph stripped, and `rgb` is the glyph fill color (gap `△` → neutral soft ink; `★`/`✓` → accent).

- [ ] **Step 1: Write the failing test**

Append to `tests/reel/test_cards.py`:

```python
def test_parse_point_glyph_defaults_to_star():
    from app.modules.reel.cards import parse_point_glyph, _ACCENT_SOFT
    glyph, phrase, rgb = parse_point_glyph("Strong on reliability")
    assert glyph == "★" and phrase == "Strong on reliability" and rgb == _ACCENT_SOFT


def test_parse_point_glyph_met_check():
    from app.modules.reel.cards import parse_point_glyph, _ACCENT_SOFT
    glyph, phrase, rgb = parse_point_glyph("✓ Met the bar on incident response")
    assert glyph == "✓" and phrase == "Met the bar on incident response"
    assert rgb == _ACCENT_SOFT


def test_parse_point_glyph_gap_is_neutral_not_accent():
    from app.modules.reel.cards import parse_point_glyph, _INK_SOFT, _ACCENT_SOFT
    glyph, phrase, rgb = parse_point_glyph("△ Scaling depth stayed shallow")
    assert glyph == "△" and phrase == "Scaling depth stayed shallow"
    assert rgb == _INK_SOFT and rgb != _ACCENT_SOFT
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reel/test_cards.py -k parse_point_glyph -q`
Expected: FAIL — `parse_point_glyph` is not defined.

- [ ] **Step 3: Implement the helper + use it in `render_card`**

Add the pure helper near `format_identity_tag` in `cards.py`:

```python
# Point-card polarity: which moment is this card framing?
_POINT_GLYPHS = ("★", "✓", "△")


def parse_point_glyph(on_screen_text: str) -> tuple[str, str, tuple[int, int, int]]:
    """Split a point card's leading polarity glyph from its phrase + pick its color.

    ``★`` (differentiating strength) and ``✓`` (met requirement) render in the violet
    accent. ``△`` (a gap / unmet requirement) renders in NEUTRAL soft ink — this is
    evidence behind a verdict, not an alarm. Missing glyph → defaults to ``★``.
    """
    text = (on_screen_text or "").strip()
    glyph = "★"
    for g in _POINT_GLYPHS:
        if text.startswith(g):
            glyph = g
            text = text[len(g):].strip()
            break
    color = _INK_SOFT if glyph == "△" else _ACCENT_SOFT
    return glyph, text, color
```

Replace the `kind == "point"` branch (lines 98-105) to use it:

```python
    if kind == "point":
        glyph, phrase, glyph_color = parse_point_glyph(text)
        gfont = font(_FONT_BOLD, 96)
        gw = text_w(glyph, gfont)
        draw.text(((width - gw) / 2, 170), glyph, font=gfont, fill=glyph_color)
        phrase = phrase or text
        y = centered_block(phrase, font(_FONT_BOLD, 54), top=320, fill=_INK)
        if subtitle:
            centered_block(subtitle, font(_FONT_REG, 30), top=y + 22, fill=_INK_SOFT)
```

(The old code stripped only `"★ "`; `parse_point_glyph` now strips whichever glyph leads.)

- [ ] **Step 4: Run the card tests**

Run: `docker compose run --rm nexus pytest tests/reel/test_cards.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reel/cards.py backend/nexus/tests/reel/test_cards.py
git commit -m "feat(reel): polarity glyph on point cards (star/check/triangle, neutral gap tint)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Recruiter UI — rename to "Evidence Reel" + lift the reject block

**Files:**
- Modify: `frontend/app/components/dashboard/reports/ImmersiveHeader.tsx:126-129,240` (lift `verdict !== 'reject'`, button aria-label)
- Modify: `frontend/app/components/dashboard/reports/ReelCard.tsx` (labels: lines 51, 70, 147, 153, 177, 206, 225; verdict-aware subtitle)
- Modify: `frontend/app/components/dashboard/reports/theater/ReelTheater.tsx` (theater title/label — grep for "reel"/"highlight")
- Test: `frontend/app/tests/components/` — add `EvidenceReelCard.test.tsx` (composition test) if a ReelCard test does not already exist; otherwise extend it.

**Interfaces:**
- Consumes: `ReelCard` props `{ sessionId, candidateName, verdict }` (verdict already threaded for subtitle); the reel playback envelope's `eligible` / `ineligible_reason` from `useReel`.
- Produces: no API/type changes — copy + gate logic only.

- [ ] **Step 1: Write the failing composition test**

Create `frontend/app/tests/components/EvidenceReelCard.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@/tests/_utils/render'
import { ReelCard } from '@/components/dashboard/reports/ReelCard'

// Mock the reel hooks at the API boundary.
vi.mock('@/lib/hooks/use-reel', () => ({
  useReel: () => ({
    data: { status: 'absent', eligible: true, ineligible_reason: null },
    isLoading: false,
  }),
  useGenerateReel: () => ({ mutate: vi.fn(), isPending: false }),
}))

describe('Evidence Reel card', () => {
  it('labels the feature "Evidence Reel", not "Highlight Reel"', () => {
    render(<ReelCard sessionId="s1" candidateName="Rahul" verdict="reject" />)
    expect(screen.getByText(/evidence reel/i)).toBeInTheDocument()
    expect(screen.queryByText(/highlight reel/i)).not.toBeInTheDocument()
  })
})
```

(If `ReelCard`'s prop names differ — confirm against the file — match them; the test must render the real component with mocked hooks.)

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/app`): `npm run test -- EvidenceReelCard`
Expected: FAIL — current copy says "candidate reel" / "highlight reel".

- [ ] **Step 3: Rename labels in `ReelCard.tsx`**

Apply these copy changes (verbatim targets → replacements):
- Line ~51 `Candidate reel` → `Evidence Reel`
- Line ~70 `Generating reel… this takes a minute.` → `Generating Evidence Reel… this takes a minute.`
- Line ~147 `A ~60s highlight reel for this candidate` → a verdict-aware blurb. Add above the JSX, using the existing `verdict` prop:
  ```tsx
  const reelBlurb =
    verdict === 'advance' ? 'A ~60s reel: why this candidate fits.'
    : verdict === 'borderline' ? 'A ~60s reel: the case both ways.'
    : 'A ~60s reel: the evidence behind this call.'
  ```
  and render `{reelBlurb}` in place of the old static line.
- Line ~153 `Create candidate reel` → `Create Evidence Reel` (and the in-flight `Starting…` stays).
- Line ~177 `Reel generation failed` → keep (verdict-neutral).
- Line ~206 aria-label `Play ${candidateName}'s candidate reel` → `Play ${candidateName}'s Evidence Reel`
- Line ~225 `{candidateName} · highlight reel` → `{candidateName} · Evidence Reel`
- The doc-comment at lines 14-16 ("positive highlight") → update to "the video evidence behind the verdict".

If `ReelCard` does not currently accept `verdict`, add it to its props type and thread it from the parent (`ReportView` / report page) where `ReelCard` is rendered.

- [ ] **Step 4: Lift the reject block in `ImmersiveHeader.tsx`**

Replace lines 126-129:

```tsx
  // Evidence Reel is available for every verdict; the backend `reelEligible`
  // flag already gates on report + recording readiness.
  const showReel = hasReel
  const showGenerate = !hasReel && reelEligible
```

Update the comment on the `reelEligible` prop (lines 105-106) to drop "verdict advance/borderline" and say "report + recording ready (any verdict)". Change the generate button aria-label (line 240) `Generate highlight video` → `Generate Evidence Reel`.

- [ ] **Step 5: Rename in `ReelTheater.tsx` (recruiter)**

Grep the file for user-visible "reel"/"highlight" strings and titles; rename any "Highlight Reel" / "Candidate Reel" heading to "Evidence Reel". (Leave prop/variable names.)

Run: `grep -ni "highlight\|candidate reel" frontend/app/components/dashboard/reports/theater/ReelTheater.tsx`

- [ ] **Step 6: Run test + lint + type-check**

Run (from `frontend/app`):
```bash
npm run test -- EvidenceReelCard
npm run lint
npm run type-check
```
Expected: test PASS; lint + type-check zero errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/reports/ReelCard.tsx \
        frontend/app/components/dashboard/reports/ImmersiveHeader.tsx \
        frontend/app/components/dashboard/reports/theater/ReelTheater.tsx \
        frontend/app/tests/components/EvidenceReelCard.test.tsx
git commit -m "feat(reports): rename to Evidence Reel + show it for reject verdicts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Public recordings page — rename to "Evidence Reel"

**Files:**
- Modify: `frontend/session/components/recordings/PublicRecordingsView.tsx` (reel/full-session toggle label)
- Modify: `frontend/session/components/recordings/theater/ReelTheater.tsx` (theater title/label)
- Test: existing `frontend/session` test suite must stay green (no new test required — copy-only; add one only if a PublicRecordingsView test already exists).

**Interfaces:**
- Consumes: the same `PublicRecordingsEnvelope` reel payload (no shape change).
- Produces: copy-only changes.

- [ ] **Step 1: Find the strings**

Run:
```bash
grep -ni "highlight\|reel" frontend/session/components/recordings/PublicRecordingsView.tsx
grep -ni "highlight\|reel" frontend/session/components/recordings/theater/ReelTheater.tsx
```

- [ ] **Step 2: Rename user-visible labels**

In both files, change any user-visible "Highlight Reel" / "Candidate Reel" / "highlight" label that names the feature to **"Evidence Reel"**. The reel ↔ full-session toggle in `PublicRecordingsView.tsx` should read "Evidence Reel" / "Full session". Leave variable, prop, and CSS-class names unchanged.

- [ ] **Step 3: Build + lint + type-check the session app**

Run (from `frontend/session`):
```bash
npm run lint
npm run type-check
npm run test
```
Expected: zero errors; existing tests green.

- [ ] **Step 4: Commit**

```bash
git add frontend/session/components/recordings/PublicRecordingsView.tsx \
        frontend/session/components/recordings/theater/ReelTheater.tsx
git commit -m "feat(recordings): rename public reel label to Evidence Reel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Live verification (manual — the user runs the talk-test)

**Files:** none (ops).

- [ ] **Step 1: Restart the vision worker (no hot-reload)**

```bash
cd backend/nexus && docker compose up -d --force-recreate nexus-vision-worker
```

- [ ] **Step 2: Regenerate a reel for each verdict from existing sessions**

Using the three known report sessions (advance / borderline / reject), trigger "Create Evidence Reel" from the recruiter report page (the reject one should now offer the button). Confirm:
- **advance** — pitch framing, warm narration, `★` cards (unchanged feel).
- **borderline** — alternating `✓`/`△` beats, neutral narration, balanced.
- **reject** — `△` shortfall beats grounded in real answers, neutral narration, optional single honest `✓`.

- [ ] **Step 3: Inspect an EDL without rendering (optional debug)**

```bash
docker compose exec nexus python -m app.modules.reel.director <session_id>
```
Confirm the printed beats carry the expected polarity glyphs + that gap clips resolve to real answer quotes.

- [ ] **Step 4: Confirm the public page**

Open the shared `/recordings/<token>` page; confirm the toggle reads "Evidence Reel" and the reel plays.

---

## Self-Review

**Spec coverage:**
- §3 Eligibility (drop allowlist) → Task 1. ✔
- §4 Director inputs (why_negative, concerns+severity, quick_summary, decision_headline, charity_flags; signal-field fix to level/score + knockout/priority/provenance/evidence/level_basis; per-question level/closure/difficulty/red_flags/listen_for_hits/score; `generate_edl` signature; actor extraction) → Task 2. ✔
- §5 Verdict-aware prompt + version bump → Task 3. ✔
- §6 Card polarity glyph + neutral gap tint → Task 4. ✔
- §7 UI rename + lift reject gate (recruiter) → Task 5; public page → Task 6. ✔ (Note: the spec's UI section didn't call out `ImmersiveHeader`'s `verdict !== 'reject'` block — discovered during planning; folded into Task 5, which is required or the backend change stays invisible.)
- §8 Tests → folded into each task (eligibility table-tests T1, `_build_document` content T2, prompt assertions T3, glyph parse T4, recruiter composition T5). Opt-in `-m prompt_quality` fixtures are replaced by Task 7 manual talk-test, consistent with the project's "manual testing for AI agents" convention.
- §9 Ops (version bump, worker restart) → Task 3 + Task 7. ✔
- §10 Out-of-scope respected (no render/timing/verdict-logic changes; one prompt).

**Placeholder scan:** No TBD/TODO. Every code step shows full code. The only `grep`-then-edit steps (T5 step 5, T6) are copy renames on small files where listing every line pre-emptively would be guesswork against live strings; the grep makes the targets explicit at execution time.

**Type consistency:** `_build_document` / `generate_edl` new kwargs (`why_negative`, `quick_summary`, `decision_headline`, `concerns`, `charity_flags`) match between Task 2 definition and the actor call. `parse_point_glyph` return shape `(glyph, phrase, rgb)` matches its use in Task 4 `render_card`. `reel_director_prompt_version` literal `"v4"` consistent across config (T3 step 4), `ai_config` accessor, and the prompt test.
