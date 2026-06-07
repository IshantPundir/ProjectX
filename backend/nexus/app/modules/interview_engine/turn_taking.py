"""Pure turn-taking helpers (gen-2 parity) — no LLM, no livekit, no regex.

`is_backchannel` is a tiny sanctioned allowlist + word-count check (a
non-authoritative realtime signal, like the no-leak token list — NOT intent
classification). A committed turn made ENTIRELY of backchannel tokens is
engagement ("mm", "yeah", "haan"), not a real turn — the engine drops it so it
never reaches the brain and never moves the floor.

Kept deliberately tight: "yes"/"no" are NOT backchannels (they can be real
answers to a yes/no probe), and any clause with a non-backchannel word is a real
turn — so a fragment like "threshold value" is NOT dropped (the brain must see
it).
"""
from __future__ import annotations

# Lowercased single-token backchannels (English + common Indian-English). A turn
# made entirely of these (any length) is engagement, not a turn grab.
BACKCHANNEL_TOKENS: frozenset[str] = frozenset({
    "yeah", "yep", "yup", "ok", "okay", "right", "sure", "uh-huh", "uhhuh",
    "mm", "mmm", "mhm", "mm-hmm", "mhmm", "hmm", "haan", "han", "achha",
    "accha", "theek", "thik", "cool", "got", "gotcha",
})


def is_backchannel(text: str) -> bool:
    """True if `text` should NOT grab the floor (pure engagement / silence).

    Backchannel when: blank, OR every word is a backchannel token. A clause with
    ANY non-backchannel word is a real turn.
    """
    words = [w.strip(".,!?-") for w in text.strip().lower().split() if w.strip(".,!?-")]
    if not words:
        return True
    return all(w in BACKCHANNEL_TOKENS for w in words)
