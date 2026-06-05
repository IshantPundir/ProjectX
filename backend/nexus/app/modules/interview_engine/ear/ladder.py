"""Gen-3 Ear — pure fusion ladder (B1).

Fuses three signals into a single turn-taking decision:

    vad_silence_ms   — how long the candidate has been silent (Silero VAD).
    smart_turn_prob  — audio/prosody EOU probability from Smart Turn v3
                       (higher = "voice sounded finished"). The CORRECTIVE signal.
    text_eou_prob    — text EOU probability from LiveKit MultilingualModel,
                       or None when the text vote is unavailable
                       (Smart-Turn-only fallback — a supported mode).

The decision is one of three EarDecision values:
    commit    — candidate is done → fire the agent's turn.
    wait      — still going / too soon → keep listening.
    hold_cue  — candidate is mid-thought on a long pause → play a gentle
                "take your time" patience cue.

No I/O, no LiveKit, no async — fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class EarDecision(StrEnum):
    commit = "commit"
    wait = "wait"
    hold_cue = "hold_cue"


@dataclass(frozen=True)
class EarLadderConfig:
    """Tunable thresholds for the fusion ladder.

    All four values are [VALIDATE] items — set to sane defaults and tuned
    empirically in Phase F3 talk-tests.  See app/config.py for the matching
    Settings fields and their inline rationale.
    """

    # Smart Turn v3 EOU probability at/above which the voice signal is "complete".
    smart_turn_commit_thr: float

    # MultilingualModel EOU probability at/above which the text signal is "complete".
    # Probs run very small for disfluent Indian-English — tune carefully.
    text_commit_thr: float

    # Hard floor: no commit is allowed before this many ms of VAD silence.
    # Prevents cutting the candidate off mid-word even if both models say done.
    min_silence_ms: int

    # At this many ms of silence (or more) with BOTH signals still incomplete,
    # play a gentle patience cue instead of waiting silently.
    hold_cue_ms: int


# ---------------------------------------------------------------------------
# Fusion rule — implements the §4 ladder exactly
# ---------------------------------------------------------------------------

def decide(
    *,
    vad_silence_ms: int,
    smart_turn_prob: float,
    text_eou_prob: float | None,
    cfg: EarLadderConfig,
) -> EarDecision:
    """Fuse VAD silence + Smart Turn + text EOU into a single EarDecision.

    §4 rule (verbatim mapping — each branch is labelled with its rule name):

    1. FLOOR — Never commit before min_silence_ms; don't cut off mid-word.
    2. SMART-TURN-ONLY PATH — when text_eou_prob is None, the text vote is
       unavailable.  Use Smart Turn as sole arbiter:
         voice complete → COMMIT
         voice incomplete + long silence → HOLD_CUE (mid-thought pause)
         voice incomplete + short silence → WAIT
    3. BOTH-COMPLETE / voice-finished-but-text-unsure → COMMIT.
       The gen-2 rescue: text was the wrong signal on disfluent Indian-English
       endings; voice takes priority when voice says done.
    4. TEXT-COMPLETE but voice still going → WAIT.
       Never cut someone off mid-word.
    5. BOTH-INCOMPLETE:
         long silence (>= hold_cue_ms) → HOLD_CUE
         short silence → WAIT
    """

    # ── 1. FLOOR ────────────────────────────────────────────────────────────
    # §4 rule: never commit before the minimum silence threshold.
    if vad_silence_ms < cfg.min_silence_ms:
        return EarDecision.wait

    # ── 2. SMART-TURN-ONLY FALLBACK ─────────────────────────────────────────
    # §4 rule: text vote is unavailable — Smart Turn is the sole arbiter.
    voice_complete = smart_turn_prob >= cfg.smart_turn_commit_thr

    if text_eou_prob is None:
        if voice_complete:
            # §4 ST-only: voice says done → COMMIT.
            return EarDecision.commit
        if vad_silence_ms >= cfg.hold_cue_ms:
            # §4 ST-only: voice still going on a long pause → mid-thought.
            return EarDecision.hold_cue
        # §4 ST-only: voice still going, silence short → keep listening.
        return EarDecision.wait

    # ── 3. BOTH-COMPLETE or voice-finished-but-text-unsure → COMMIT ─────────
    # §4 rule: when voice says done, commit regardless of the text vote.
    # This is the gen-2 rescue: text EOU probs are unreliable on disfluent
    # Indian-English endings, so voice takes priority.
    if voice_complete:
        return EarDecision.commit

    # ── 4. TEXT-COMPLETE but voice still going → WAIT ───────────────────────
    # §4 rule: protect mid-word; text alone is not enough to commit.
    text_complete = text_eou_prob >= cfg.text_commit_thr
    if text_complete:
        return EarDecision.wait

    # ── 5. BOTH-INCOMPLETE ──────────────────────────────────────────────────
    # §4 rule: candidate is still speaking.  On a long pause they may be
    # mid-thought; play a patience cue rather than waiting in silence.
    if vad_silence_ms >= cfg.hold_cue_ms:
        return EarDecision.hold_cue
    return EarDecision.wait


# ---------------------------------------------------------------------------
# Live-engine builder — reads thresholds from AIConfig
# ---------------------------------------------------------------------------

def ladder_config_from_ai_config() -> EarLadderConfig:
    """Build an EarLadderConfig from the app's env-driven AIConfig.

    Called once at session start by the live Ear.  Tests pass an explicit
    EarLadderConfig instead so they run without any env setup.
    """
    # Import lazily so the module stays importable from tests without the
    # full app.config environment wired up.
    from app.ai.config import ai_config  # noqa: PLC0415

    return EarLadderConfig(
        smart_turn_commit_thr=ai_config.ear_smart_turn_commit_thr,
        text_commit_thr=ai_config.ear_text_commit_thr,
        min_silence_ms=ai_config.ear_min_silence_ms,
        hold_cue_ms=ai_config.ear_hold_cue_ms,
    )
