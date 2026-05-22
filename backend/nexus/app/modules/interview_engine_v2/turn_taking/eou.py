"""EOU behavioral layer — backchannel gate + unresponsive ladder (pure).

No livekit, no LLM, NO regex (DESIGN-SPEC §6 / feedback_no_regex). The backchannel
gate is a sanctioned tiny allowlist + word-count heuristic (a non-authoritative
realtime signal, exactly like M1's no-leak token list — not intent classification).
The unresponsive ladder is the doc-08 "resolved" reflex: ~7s gentle nudge -> ~15s
"still there?" -> after N no-responses, close as candidate_unresponsive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Lowercased single-token backchannels (English + common Indian-English). A turn
# made entirely of these (any length) is engagement, not a turn grab. Kept tight
# to avoid swallowing real one-word answers like "no"/"yes" to a yes/no probe.
BACKCHANNEL_TOKENS: frozenset[str] = frozenset({
    "yeah", "yep", "yup", "ok", "okay", "right", "sure", "uh-huh", "uhhuh",
    "mm", "mmm", "mhm", "hmm", "haan", "han", "achha", "accha", "theek",
    "thik", "cool", "got", "gotcha",
})


def _words(text: str) -> list[str]:
    return [w for w in text.strip().lower().split() if w]


def is_backchannel(text: str, *, min_words: int) -> bool:
    """True if `text` should NOT grab the floor (engagement / silence).

    Backchannel when: blank, OR fewer than `min_words` words, OR every word is a
    backchannel token. A clause with any non-backchannel word is a real turn.
    """
    words = _words(text)
    if not words:
        return True
    if len(words) < min_words:
        return True
    stripped = [w.strip(".,!?-") for w in words]
    return all(w in BACKCHANNEL_TOKENS for w in stripped if w)


@dataclass(frozen=True)
class EouConfig:
    prompt_1_s: float
    prompt_2_s: float
    max_no_responses: int


class LadderAction(str, Enum):
    NONE = "none"
    PROMPT_1 = "prompt_1"               # ~7s gentle nudge
    PROMPT_2 = "prompt_2"               # ~15s "still there?"  (== one no-response)
    CLOSE_UNRESPONSIVE = "close_unresponsive"


class UnresponsiveLadder:
    """Tracks silence after a posed question and escalates per doc 08.

    Reaching PROMPT_2 (the candidate ignored a question through both rungs) counts
    as one no-response. After `max_no_responses`, the next PROMPT_2 boundary
    returns CLOSE_UNRESPONSIVE instead. A real response resets everything.
    """

    def __init__(self, config: EouConfig) -> None:
        self._config = config
        self._posed_at: float | None = None
        self._fired_1 = False
        self._fired_2 = False
        self._no_responses = 0

    def on_question_posed(self, at_s: float) -> None:
        self._posed_at = at_s
        self._fired_1 = False
        self._fired_2 = False

    def on_candidate_responded(self) -> None:
        self._posed_at = None
        self._fired_1 = False
        self._fired_2 = False
        self._no_responses = 0

    def action(self, now_s: float) -> LadderAction:
        if self._posed_at is None:
            return LadderAction.NONE
        elapsed = now_s - self._posed_at
        if not self._fired_2 and elapsed >= self._config.prompt_2_s:
            self._fired_2 = True
            self._no_responses += 1
            if self._no_responses >= self._config.max_no_responses:
                return LadderAction.CLOSE_UNRESPONSIVE
            return LadderAction.PROMPT_2
        if not self._fired_1 and elapsed >= self._config.prompt_1_s:
            self._fired_1 = True
            return LadderAction.PROMPT_1
        return LadderAction.NONE
