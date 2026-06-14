"""
Brain deterministic policy gates — D5 task.

These gates run on the live critical path.  They are PURE Python functions
(no livekit, no I/O, no LLM) and must NEVER raise under any input.  Each gate
is built with defensive guards so unexpected inputs produce a safe fallback
rather than propagating an exception to the engine loop.

Gates
------
1. scrub_composed_say — no-leak scrub.
   Checks whether `composed_say` echoes any known rubric secret string
   (literal substring match, case-insensitive, length-gated to avoid short
   coincidental words).  A matched secret → safe fallback.  This is NOT intent
   classification; it is literal known-string detection.

2. coerce_probe_dimension — probe coherence.
   Ensures the brain's chosen probe_dimension is a valid, UNFIRED dimension
   slug from the active question's follow_ups list.  Enforces fire-once (each
   dimension at most once) plus a hard per-thread probe cap; returns None when
   every dimension is fired OR the cap is reached.
"""

from __future__ import annotations

from app.modules.interview_engine.contracts import ActiveQuestionRubric, FollowUpDimension

# ---------------------------------------------------------------------------
# Shared constant
# ---------------------------------------------------------------------------

SAFE_FALLBACK: str = (
    "Sorry, let me put that differently — could you tell me a bit more about that?"
)

# ---------------------------------------------------------------------------
# Optional belt-and-suspenders meta-phrase list.
# These are obvious rubric/evaluation meta-phrases that should NEVER appear in
# candidate-facing text regardless of whether they echo a known secret.  The
# primary leak check is the known-rubric-string match above; this list is a
# secondary catch for common slip-through patterns.  Keep it TINY and literal.
# ---------------------------------------------------------------------------
_META_PHRASES: tuple[str, ...] = (
    "what i'm looking for",
    "the rubric",
    "evaluation criteria",
    "evaluation hint",
    "the rubric says",
    "rubric says",
)


# ===========================================================================
# Gate 1 — No-leak scrub
# ===========================================================================

def scrub_composed_say(
    text: str | None,
    rubric: ActiveQuestionRubric,
    *,
    fallback: str = SAFE_FALLBACK,
    min_phrase_len: int = 12,
) -> str | None:
    """Check *text* for leaked rubric secrets and return a safe fallback if found.

    Detection strategy
    ------------------
    1. Collect the rubric's KNOWN secret strings:
       ``excellent``, ``meets_bar``, ``below_bar``, every ``positive_evidence``
       entry, every ``red_flags`` entry, ``evaluation_hint``.
    2. For each non-empty secret of length ≥ *min_phrase_len*, perform a
       case-insensitive substring search inside *text*.  A hit → LEAK → return
       *fallback*.
    3. Additionally check a small fixed list of obvious meta-phrases
       (``_META_PHRASES``) regardless of rubric content (belt-and-suspenders).
    4. If no leak found → return *text* unchanged.

    This is LITERAL known-string detection, NOT intent classification or regex.

    This function NEVER raises.
    """
    if text is None:
        return None

    try:
        text_lower = text.lower()

        # --- Primary check: known rubric secret strings ---
        secrets: list[str] = []
        try:
            candidates_raw: list[str | None] = [
                rubric.excellent,
                rubric.meets_bar,
                rubric.below_bar,
                rubric.evaluation_hint,
                *(rubric.positive_evidence or []),
                *(rubric.red_flags or []),
            ]
            for s in candidates_raw:
                if s and len(s) >= min_phrase_len:
                    secrets.append(s)
        except Exception:  # pragma: no cover
            pass  # rubric access error → no secrets to check, skip to meta-phrases

        for secret in secrets:
            try:
                if secret.lower() in text_lower:
                    return fallback
            except Exception:  # pragma: no cover
                pass

        # --- Secondary check: obvious meta-phrases (belt-and-suspenders) ---
        for phrase in _META_PHRASES:
            if phrase in text_lower:
                return fallback

        return text

    except Exception:  # pragma: no cover
        # Defensive catch-all — if anything goes wrong, return the text
        # unchanged rather than surfacing an exception to the engine turn.
        return text


# ===========================================================================
# Gate 2 — Probe coherence
# ===========================================================================

def coerce_probe_dimension(
    probe_dimension: str | None,
    *,
    follow_ups: list[FollowUpDimension],
    fired: list[str],
    cap: int,
) -> str | None:
    """Coerce the brain's probe_dimension to a valid, UNFIRED dimension slug.

    Returns a slug that exists in follow_ups and is not in `fired`. Returns None when
    every dimension is fired OR the per-thread probe cap is reached (→ caller advances).
    Never raises.
    """
    try:
        if not follow_ups:
            return None
        fired_set: set[str] = set(fired or [])
        # Hard cap: total probes on this thread is bounded regardless of remaining dims.
        if len(fired_set) >= cap:
            return None
        slugs = [d.dimension for d in follow_ups]
        available = [s for s in slugs if s not in fired_set]
        if not available:
            return None
        if probe_dimension in available:
            return probe_dimension
        return available[0]
    except Exception:  # pragma: no cover — defensive
        return None
