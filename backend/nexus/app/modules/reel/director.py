"""Reel Director — LLM -> validated EDL (ordered beats) for the candidate reel.

Two layers:

  * ``generate_edl`` (LLM, manual-tested): reads the report ground truth + the
    word-timed transcript and emits a raw ``ReelEdlOut`` whose ``clip``/
    ``experience`` beats reference an ANSWER RUN by its run index
    (``source_turn_ref``) and a turn-relative WORD-INDEX range
    ``[in_word, out_word]``. The LLM never sees video ms.
  * ``validate_edl`` (pure, deterministic guardrails — this file): resolves the
    word indices to per-word records (each carrying its turn's ``turn_ref`` +
    ``turn_start_ms`` + turn-relative ms), rejects hallucinations (unknown ref /
    out-of-bounds index), enforces the duration budget (per-clip soft cap, then
    drop trailing question groups), and fails honestly if no clip survives.

The renderer maps a validated beat to video by the gen-3 word-timed contract:
``video_ms = turn_start_ms + rel_ms + offset`` (see ``timing.py`` /
``render._clip_to_video``); the Director stays purely in transcript space.

Keep this import-light enough for the lean image — the LLM call imports ``app.ai``
lazily so the pure validation path (and its tests) need no OpenAI/ffmpeg deps.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.modules.reel.transcript import AnswerRun, answer_runs, is_pause_before

# --- tuning constants (transcript-space; ms) ------------------------------
MAX_TOTAL_MS = 80_000        # soft target (quality may run a little over)
TARGET_MS = 60_000           # aim for ~60s
CLIP_SOFT_CAP_MS = 16_000    # a single clip may run this long to show full substance
EST_BOUNDARY_PAUSE_MS = 500  # estimated inter-turn pause inside a multi-turn clip
SPEAK_WPS = 2.75             # ~165 wpm, Arjun narration, for card duration estimate
_CARD_FLOOR_MS = {"title": 3_000, "match": 4_000, "point": 3_500, "outro": 4_000}

# Edge-only disfluency/discourse tokens trimmed off a clip's IN/OUT (and captions).
# This is lexical edge cleanup, NOT semantic intent classification.
_EDGE_TRIM = {"um", "uh", "uhh", "umm", "mm", "mmm", "er", "ah", "hmm", "so",
              "like", "yeah", "okay", "ok", "sure", "well", "right"}

TIMED_KINDS = {"clip", "experience"}   # beats cut from the recording (carry timing)
LEAD_CARDS = {"match", "point"}        # a card that leads a drop-group of clips
BeatKind = Literal["title", "match", "experience", "point", "clip", "outro"]


class NoClipBeatsError(Exception):
    """No clip/experience beat survived validation — the reel cannot be built."""


# --- LLM output schema -----------------------------------------------------
class ReelBeat(BaseModel):
    kind: BeatKind
    source_turn_ref: int | None = None   # answer-run index; clip/experience only
    in_word: int | None = None           # index into the run's words[]
    out_word: int | None = None
    on_screen_text: str | None = None    # card copy (title/ask/credit/outro)
    caption: str | None = None           # optional hint; words[] is the caption truth
    narration_text: str | None = None    # Arjun TTS script for card beats


class ReelEdlOut(BaseModel):
    beats: list[ReelBeat]


# --- validated (renderable) EDL -------------------------------------------
@dataclass
class ValidatedBeat:
    kind: str
    duration_ms: int
    source_turn_ref: int | None = None
    # clip/experience: the selected words, each carrying its origin turn
    # (turn_ref + turn_start_ms) + turn-relative timing so the renderer maps a
    # multi-turn clip to one contiguous cut.
    words: list[dict] = field(default_factory=list)
    on_screen_text: str | None = None
    caption: str | None = None
    narration_text: str | None = None


@dataclass
class ValidatedEdl:
    beats: list[ValidatedBeat]
    duration_ms: int


def _estimate_clip_duration(words: list[dict]) -> int:
    """Estimated video duration of a (possibly multi-turn) clip word list.

    Sum each same-turn segment's covered span + an estimated pause per turn
    boundary (the real inter-turn pause is VAD-only; the renderer measures it).
    """
    if not words:
        return 0
    total = 0
    seg_start = words[0]["rel_start_ms"]
    prev = words[0]
    boundaries = 0
    for w in words[1:]:
        if w["turn_ref"] != prev["turn_ref"]:
            total += prev["rel_end_ms"] - seg_start
            boundaries += 1
            seg_start = w["rel_start_ms"]
        prev = w
    total += prev["rel_end_ms"] - seg_start
    return total + boundaries * EST_BOUNDARY_PAUSE_MS


def _edge_trim(words: list) -> list:
    """Drop leading/trailing edge-disfluency tokens (lexical cleanup, not intent)."""
    lo, hi = 0, len(words)
    while lo < hi and words[lo].text.lower().strip(".,?!") in _EDGE_TRIM:
        lo += 1
    while hi > lo and words[hi - 1].text.lower().strip(".,?!") in _EDGE_TRIM:
        hi -= 1
    return words[lo:hi]


def _resolve_clip(beat: ReelBeat, runs_by_ref: dict[int, AnswerRun]) -> ValidatedBeat | None:
    """Resolve a clip/experience beat over its answer run, or None to drop it.

    Drops on a hallucinated run ref or an out-of-bounds / inverted word range.
    Edge-trims disfluencies, then trims over-cap clips inward to ``CLIP_SOFT_CAP_MS``.
    """
    ref = beat.source_turn_ref
    run = runs_by_ref.get(ref) if ref is not None else None
    if run is None:
        return None
    iw, ow = beat.in_word, beat.out_word
    if iw is None or ow is None or not (0 <= iw <= ow < len(run.words)):
        return None

    selected = _edge_trim(run.words[iw:ow + 1])
    if not selected:
        return None
    # per-clip soft cap: drop trailing words until the estimate fits.
    while len(selected) > 1 and _estimate_clip_duration(
            [_word_dict(w) for w in selected]) > CLIP_SOFT_CAP_MS:
        selected = selected[:-1]

    words = [_word_dict(w) for w in selected]
    return ValidatedBeat(
        kind=beat.kind, duration_ms=_estimate_clip_duration(words),
        source_turn_ref=ref, words=words, on_screen_text=beat.on_screen_text,
        caption=beat.caption, narration_text=beat.narration_text,
    )


def _word_dict(w) -> dict:
    return {"idx": w.idx, "text": w.text, "turn_ref": w.turn_ref,
            "turn_start_ms": w.turn_start_ms,
            "rel_start_ms": w.rel_start_ms, "rel_end_ms": w.rel_end_ms}


def _estimate_card(beat: ReelBeat) -> ValidatedBeat:
    """Estimate a card beat's duration from its narration (the render recomputes)."""
    n_words = len((beat.narration_text or "").split())
    est = math.ceil(n_words / SPEAK_WPS * 1000)
    dur = max(_CARD_FLOOR_MS.get(beat.kind, 2_000), est)
    return ValidatedBeat(
        kind=beat.kind, duration_ms=dur, on_screen_text=beat.on_screen_text,
        caption=beat.caption, narration_text=beat.narration_text,
    )


def _has_clip(group: list[ValidatedBeat]) -> bool:
    return any(b.kind in TIMED_KINDS for b in group)


def _group_body(body: list[ValidatedBeat]) -> list[list[ValidatedBeat]]:
    """Group body beats into drop-units: a lead card (match/point) + its clips.

    A new group starts at each lead card; clips/other beats attach to the current
    group. A clip before any lead card (e.g. a §1 `experience`) forms its own group.
    """
    groups: list[list[ValidatedBeat]] = []
    cur: list[ValidatedBeat] = []
    for b in body:
        if b.kind in LEAD_CARDS and cur:
            groups.append(cur)
            cur = [b]
        else:
            cur.append(b)
    if cur:
        groups.append(cur)
    return groups


def _fit_budget(beats: list[ValidatedBeat]) -> list[ValidatedBeat]:
    """Drop trailing groups until total <= ``MAX_TOTAL_MS``, preserving narrative.

    title (leading) and outro (trailing) are pinned; the body is grouped so a
    dropped clip takes its ask/credit with it. The last clip-bearing group is
    never dropped (>=1 clip is guaranteed by validate_edl).
    """
    title = [beats[0]] if beats and beats[0].kind == "title" else []
    outro = [beats[-1]] if beats and beats[-1].kind == "outro" else []
    body = beats[len(title): len(beats) - len(outro)]
    groups = _group_body(body)

    def total() -> int:
        return sum(b.duration_ms for g in groups for b in g) + \
            sum(b.duration_ms for b in title) + sum(b.duration_ms for b in outro)

    while groups and total() > MAX_TOTAL_MS:
        clip_groups = sum(1 for g in groups if _has_clip(g))
        if _has_clip(groups[-1]) and clip_groups <= 1:
            break   # would drop the only clip group — stop
        groups.pop()

    return title + [b for g in groups for b in g] + outro


def validate_edl(edl: ReelEdlOut, transcript: list[dict]) -> ValidatedEdl:
    """Resolve + guard a raw LLM EDL into a renderable, budget-fitting EDL.

    Raises ``NoClipBeatsError`` if no clip/experience beat survives resolution.
    """
    runs_by_ref = {r.ref: r for r in answer_runs(transcript)}
    kept_ranges: dict[int, list[tuple[int, int]]] = {}   # run ref -> [(lo_idx, hi_idx)]
    resolved: list[ValidatedBeat] = []
    for beat in edl.beats:
        if beat.kind in TIMED_KINDS:
            vb = _resolve_clip(beat, runs_by_ref)
            if vb is None:
                continue
            # structural dedup backstop: drop a word range overlapping a kept one.
            lo, hi = vb.words[0]["idx"], vb.words[-1]["idx"]
            ranges = kept_ranges.setdefault(vb.source_turn_ref, [])
            if any(lo <= b and hi >= a for a, b in ranges):
                continue
            ranges.append((lo, hi))
            resolved.append(vb)
        else:
            resolved.append(_estimate_card(beat))

    if not any(b.kind in TIMED_KINDS for b in resolved):
        raise NoClipBeatsError("EDL has no valid clip/experience beat")

    fitted = _fit_budget(resolved)
    return ValidatedEdl(beats=fitted, duration_ms=sum(b.duration_ms for b in fitted))


# --- LLM call (manual-tested) ---------------------------------------------
def _build_document(*, candidate_name: str | None, role_title: str | None,
                    verdict: str | None, verdict_reason: str | None,
                    why_positive: str | None, strengths: list[dict],
                    question_scorecards: list[dict], signal_scorecards: list[dict],
                    transcript: list[dict]) -> str:
    """Serialize report fit-context (FIRST) then the indexed transcript (the document).

    Context-before-document per the house rule. The fit-context is the material
    for §1 (role + must-have signals) and §2 (strengths). Candidate turns are
    rendered with a per-word ``idx:text`` index so the model can reference
    [in_word, out_word].
    """
    first_name = (candidate_name or "").split()[0] if candidate_name else None
    lines: list[str] = ["<report>",
                        f"candidate_name: {candidate_name or 'n/a'}",
                        f"candidate_first_name: {first_name or 'n/a'}",
                        f"role: {role_title or 'n/a'}",
                        f"verdict: {verdict or 'n/a'}"]
    if verdict_reason:
        lines.append(f"verdict_reason: {verdict_reason}")
    if why_positive:
        lines.append(f"why_positive: {why_positive}")

    # §1 source: the JD must-haves (highest-weight signals) the candidate met.
    lines.append("jd_signals (the role's requirements; weight=importance):")
    for s in sorted(signal_scorecards or [], key=lambda x: -(x.get("weight") or 0)):
        lines.append(
            f"- {s.get('signal')} | weight: {s.get('weight')} | "
            f"state: {s.get('final_state')} | grade: {s.get('grade')}"
        )

    # §2 source: the report's named strengths.
    lines.append("strengths:")
    for s in strengths or []:
        lines.append(f"- {s.get('title', '')}: {s.get('detail', '')}")

    # Per-question reads — to locate which turn evidences a point.
    lines.append("questions:")
    for q in question_scorecards or []:
        lines.append(
            f"- question_id: {q.get('question_id')} | status: {q.get('status_badge')} | "
            f"title: {q.get('title', '')}"
        )
        if q.get("our_read"):
            lines.append(f"  our_read: {q['our_read']}")
        if q.get("candidate_quote"):
            lines.append(f"  candidate_quote (hint): {q['candidate_quote']}")
    lines.append("</report>")

    # The document: each ANSWER (a contiguous run of the candidate's turns) with a
    # continuous word index. ``//`` marks a natural pause (a clean in/out point).
    # A clip references an answer by ref + [in_word, out_word] over its word index.
    lines.append("<answers>")
    for run in answer_runs(transcript):
        if not run.words:
            continue
        parts = []
        for w in run.words:
            if w.idx != run.words[0].idx and is_pause_before(w):
                parts.append("//")
            parts.append(f"{w.idx}:{w.text}")
        lines.append(f"answer ref={run.ref} | question_id={run.question_id}")
        lines.append("words: " + " ".join(parts))
        lines.append("---")
    lines.append("</answers>")
    return "\n".join(lines)


async def generate_edl(*, candidate_name: str | None, role_title: str | None,
                       verdict: str | None, verdict_reason: str | None,
                       why_positive: str | None, strengths: list[dict],
                       question_scorecards: list[dict], signal_scorecards: list[dict],
                       transcript: list[dict], correlation_id: str) -> ReelEdlOut:
    """Call the LLM to produce a raw EDL. Caller must run ``validate_edl`` after.

    Mirrors ``reporting/scoring/judge.py``: Responses API + native structured
    output, effort-gated reasoning, prompt_cache_key, OTel span, no-PII logs.
    app.ai imports are lazy so the pure-validation path stays import-light.
    """
    import structlog
    from opentelemetry import trace

    from app.ai.client import get_raw_openai_client
    from app.ai.config import ai_config
    from app.ai.prompts import PromptLoader
    from app.ai.tracing import set_llm_span_attributes

    log = structlog.get_logger()
    system_prompt = PromptLoader(
        version=ai_config.reel_director_prompt_version
    ).get("reel/director")
    document = _build_document(
        candidate_name=candidate_name, role_title=role_title, verdict=verdict,
        verdict_reason=verdict_reason, why_positive=why_positive, strengths=strengths,
        question_scorecards=question_scorecards, signal_scorecards=signal_scorecards,
        transcript=transcript,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": document},
    ]
    prompt_cache_key = (
        f"{ai_config.reel_director_prompt_cache_key_prefix}"
        f":{ai_config.reel_director_prompt_version}"
        f":{ai_config.reel_director_model}"
    )
    kwargs: dict[str, object] = {
        "model": ai_config.reel_director_model,
        "input": messages,
        "text_format": ReelEdlOut,
        "prompt_cache_key": prompt_cache_key,
    }
    if ai_config.reel_director_effort:
        kwargs["reasoning"] = {"effort": ai_config.reel_director_effort}

    client = get_raw_openai_client()
    tracer = trace.get_tracer("nexus.ai.openai")
    with tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(
            prompt_name="reel_director",
            prompt_version=ai_config.reel_director_prompt_version,
            correlation_id=correlation_id,
        )
        response = await client.responses.parse(**kwargs)

    usage = getattr(response, "usage", None)
    if usage is not None:
        details = getattr(usage, "input_tokens_details", None)
        log.info(
            "reel.director.usage",
            input_tokens=getattr(usage, "input_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            correlation_id=correlation_id,
        )

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reel.director.no_parse", correlation_id=correlation_id)
        return ReelEdlOut(beats=[])
    log.info("reel.director.parsed", n_beats=len(parsed.beats),
             correlation_id=correlation_id)
    return parsed


def edl_to_dict(vedl: ValidatedEdl) -> dict:
    """Serialize a ValidatedEdl to a plain dict (for the dev EDL dump / inspection)."""
    return {"duration_ms": vedl.duration_ms, "beats": [asdict(b) for b in vedl.beats]}


# --- dev entrypoint: inspect the EDL for a session without rendering ----------
async def _dev_main(session_id: str) -> int:
    """Run the Director on a real session: report (DB) + transcript (fixture) -> EDL.

    Prints the EDL (beats + resolved clip quotes) for debugging; production
    rendering goes through ``generate_session_reel`` (the actor), not here.
        docker compose exec nexus python -m app.modules.reel.director <session_id>
    """
    import json
    import os

    from sqlalchemy import text

    from app.database import get_bypass_session

    async with get_bypass_session() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (await db.execute(text(
            "SELECT r.verdict, r.verdict_reason, r.summary, r.question_scorecards, "
            "       r.signal_scorecards, j.title, c.name "
            "FROM session_reports r "
            "LEFT JOIN candidate_job_assignments a ON a.id = r.assignment_id "
            "LEFT JOIN job_postings j ON j.id = a.job_posting_id "
            "LEFT JOIN candidates c ON c.id = a.candidate_id "
            "WHERE r.session_id = :sid"
        ), {"sid": session_id})).first()
    if not row:
        print(f"[director] no session_report for {session_id}")
        return 1
    verdict, verdict_reason, summary, qsc, ssc, role_title, candidate_name = row
    why_positive = ((summary or {}).get("decision") or {}).get("why_positive")
    if isinstance(why_positive, dict):
        why_positive = why_positive.get("body")

    fixture = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                           "tests/fixtures/candidate_reel",
                           f"session_{session_id[:8]}_transcript.json")
    with open(os.path.abspath(fixture), encoding="utf-8") as f:
        transcript = json.load(f)

    raw = await generate_edl(
        candidate_name=candidate_name, role_title=role_title, verdict=verdict,
        verdict_reason=verdict_reason, why_positive=why_positive,
        strengths=(summary or {}).get("strengths", []),
        question_scorecards=qsc or [], signal_scorecards=ssc or [],
        transcript=transcript, correlation_id=f"reel-dev-{session_id[:8]}",
    )
    print(f"[director] raw beats: {[b.kind for b in raw.beats]}")
    vedl = validate_edl(raw, transcript)
    print(f"[director] validated {len(vedl.beats)} beats, {vedl.duration_ms/1000:.1f}s")
    for b in vedl.beats:
        if b.words:
            turns = sorted({w["turn_ref"] for w in b.words})
            quote = " ".join(w["text"] for w in b.words)
            print(f"   {b.kind:11s} {b.duration_ms/1000:4.1f}s  turns={turns}")
            print(f"               CLIP: \"{quote}\"")
        else:
            if b.on_screen_text:
                print(f"   {b.kind:11s} {b.duration_ms/1000:4.1f}s  CARD: {b.on_screen_text}")
            if b.narration_text:
                print(f"               NARR: {b.narration_text}")

    out_dir = "/app/tmp"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"edl_{session_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edl_to_dict(vedl), f, indent=2)
    print(f"[director] wrote {out_path}")
    return 0


if __name__ == "__main__":
    import asyncio
    import sys

    raise SystemExit(asyncio.run(_dev_main(sys.argv[1])))
