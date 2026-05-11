"""Per-event-kind redaction boundary.

Enforces spec §5.2.  In `metadata` mode every CONTENT field listed in
``_CONTENT_FIELDS_BY_KIND`` is stripped; in `full` mode the payload is
returned unchanged.

This is a pure function — no IO, no globals, no logging. Reasoning about
it should require nothing but the input.

Adding a new event kind that carries content REQUIRES adding an entry
to ``_CONTENT_FIELDS_BY_KIND`` in the same PR. CI (when wired) greps for
new kinds in agent.py and fails if a content-bearing kind is missing
from this map.

Per spec §6.4: engine event kinds listed in ``_ENGINE_PASSTHROUGH_KINDS``
are NEVER redacted in either mode — the candidate utterance is the
audit-grade artifact.  Adding a kind to that set means "no content field
will ever be stripped here"; removing one is a breaking change to the
audit contract.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

# Engine event kinds that are explicitly declared passthrough — not redacted
# in metadata OR full mode.  Per spec §6.4 the candidate utterance captured
# inside these events is the audit-grade artifact and must never be stripped.
_ENGINE_PASSTHROUGH_KINDS: frozenset[str] = frozenset(
    {
        "turn.started",
        "turn.completed",
        "turn.coalesced",
        "judge.call",
        "judge.synthetic",
        "judge.fallback",
        "judge.validation",
        "state.mutation",
        "speaker.call",
        "speaker.cached",
        "speaker.output",
        "speaker.error",
        "lifecycle.transition",
        "checkpoint.written",
        "frontend.attribute.published",
        "session.terminal_delivered",
    }
)

# kind -> list of payload keys that carry user-content/PII and must be
# stripped in metadata mode.
_CONTENT_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "audio.stt.transcribed": ("transcript",),
    "llm.message.added": ("content",),
    "llm.tool.executed": ("arguments", "output"),
    "disqualify.knockout": ("reason",),
    "audio.pipeline.error": ("error",),
    "controller.intent.flag_safety_concern": ("note",),
    "controller.intent.report_technical_issue": ("description",),
    "task.completed": ("result",),
}


def redact_payload(
    kind: str,
    payload: dict[str, Any],
    *,
    mode: Literal["metadata", "full"],
) -> dict[str, Any]:
    """Return a redacted copy of ``payload`` per ``mode``.

    ``mode="full"`` returns the input unchanged (a shallow copy).
    ``mode="metadata"`` strips every key listed in
    ``_CONTENT_FIELDS_BY_KIND`` for the given ``kind``.

    Unknown kinds pass through unchanged in both modes — see module
    docstring on the discipline required when adding new kinds.
    """
    if mode not in ("metadata", "full"):
        raise ValueError(f"invalid redaction mode: {mode!r}")

    # Engine event kinds are the audit-grade artifact — never redacted in
    # either mode (spec §6.4).  Check before the content-field dispatch so
    # a future accidental entry in _CONTENT_FIELDS_BY_KIND cannot override
    # this guarantee.
    if kind in _ENGINE_PASSTHROUGH_KINDS:
        return copy.deepcopy(payload)

    if mode == "full":
        return dict(payload)

    blocked = _CONTENT_FIELDS_BY_KIND.get(kind, ())
    if not blocked:
        return dict(payload)
    return {k: v for k, v in payload.items() if k not in blocked}
