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
"""

from __future__ import annotations

from typing import Any, Literal

# kind -> list of payload keys that carry user-content/PII and must be
# stripped in metadata mode.
_CONTENT_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "audio.stt.transcribed": ("transcript",),
    "llm.message.added": ("content",),
    "llm.tool.executed": ("arguments", "output"),
    "disqualify.knockout": ("reason",),
    "audio.pipeline.error": ("error",),
    # Phase 2 will add: task.completed (result_dict), controller.intent.end_early (summary)
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

    if mode == "full":
        return dict(payload)

    blocked = _CONTENT_FIELDS_BY_KIND.get(kind, ())
    if not blocked:
        return dict(payload)
    return {k: v for k, v in payload.items() if k not in blocked}
