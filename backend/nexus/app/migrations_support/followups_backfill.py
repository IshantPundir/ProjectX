"""Pure transform helpers for the follow-ups governed-dimensions backfill (migration 0055).
Dependency-free + unit-tested; lives outside app.modules to stay importable from Alembic
without triggering the question_bank package import cycle."""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(text: str, *, max_len: int = 60) -> str:
    s = _NON_ALNUM.sub("_", (text or "").strip().lower()).strip("_")
    return s[:max_len].rstrip("_")


def _is_object_shape(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(v, dict) and "dimension" in v and "seed_probe" in v for v in value)
    )


def upgrade_value(follow_ups: object) -> list[dict]:
    """list[str] -> list[{dimension, intent, seed_probe, listen_for}]. Idempotent."""
    if not isinstance(follow_ups, list):
        return []
    if _is_object_shape(follow_ups):
        return follow_ups  # already migrated
    out: list[dict] = []
    seen: dict[str, int] = {}
    for item in follow_ups:
        text = item if isinstance(item, str) else str(item)
        base = slug(text) or "probe"
        seen[base] = seen.get(base, 0) + 1
        dim = base if seen[base] == 1 else f"{base}_{seen[base]}"
        out.append({"dimension": dim, "intent": text, "seed_probe": text, "listen_for": []})
    return out


def downgrade_value(follow_ups: object) -> list[str]:
    """object shape -> list[str] (seed_probe only). Idempotent on plain strings.

    Note: downgrade is LOSSY — only ``seed_probe`` survives. A subsequent
    re-upgrade will not restore the original ``intent`` or ``listen_for`` values.
    """
    if not isinstance(follow_ups, list):
        return []
    if not _is_object_shape(follow_ups):
        return [x if isinstance(x, str) else str(x) for x in follow_ups]
    return [str(v.get("seed_probe", "")) for v in follow_ups]
