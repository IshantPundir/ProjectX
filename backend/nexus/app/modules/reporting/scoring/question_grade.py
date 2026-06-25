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
from app.modules.reporting.schemas import QuestionGradeOut
from app.modules.reporting.scoring.constants import level_score
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.types import DemonstrationLevel

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

_TEXTURE_RANK = {EvidenceTexture.thin: 0, EvidenceTexture.concrete: 1, EvidenceTexture.strong: 2}
_RANK_LEVEL = {2: "strong", 1: "solid", 0: "thin"}


def score_from_level(level: str) -> int:
    """Fallback 0–10 question score from an engine level (LEVEL_POINTS ÷ 10, rounded)."""
    return round(level_score(level) / 10)


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
    """Grade ONE question against its full bank card via the OpenAI Responses API.

    Uses the standard Responses-API pattern (PromptLoader, responses.parse,
    text_format, tracing span, ground_quotes, prompt_cache_key construction).
    On refusal (output_parsed is None) gracefully falls back to the engine base_level.
    """
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
        lvl = base_level if base_level != "not_reached" else "thin"
        return QuestionGradeOut(level=lvl, score=score_from_level(lvl))

    grounded, _ = ground_quotes(parsed.evidence_quotes, notes_block)
    clamped = max(0, min(10, parsed.score))
    return parsed.model_copy(update={"evidence_quotes": grounded, "score": clamped})
