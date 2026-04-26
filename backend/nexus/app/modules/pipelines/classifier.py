"""Edit-category diff classifier — see spec §8.

Classifies a proposed pipeline edit into one of four categories:
  A — Forward-only safe (config tweaks, participant swaps, question CRUD)
  B — Shape additive (add a stage, reorder, unpause)
  C — Shape subtractive (remove a stage, pause)
  D — Identity-changing (stage_type change)

The PATCH endpoint runs this server-side as the source of truth; the
preview-changes endpoint exposes it to the frontend for warning UX.

This is a pure function — no DB, no FastAPI. Test from unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EditCategory(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


@dataclass
class ClassificationResult:
    category: EditCategory
    warnings: list[str] = field(default_factory=list)
    in_flight: dict[str, int] = field(default_factory=dict)


def _stages_by_id(stages: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in stages if s.get("id") is not None}


def classify_pipeline_diff(
    *,
    current: list[dict],
    proposed: list[dict],
    in_flight: dict[str, int],
) -> ClassificationResult:
    """Classify the diff between current and proposed stage lists.

    current/proposed: list of stage dicts with at minimum {id, position, stage_type,
                      name, paused_at, duration_minutes, difficulty, signal_filter,
                      pass_criteria, advance_behavior, sla_days}.
    in_flight: stage_id → count of in-flight candidates currently in that stage.

    Returns the highest-severity category triggered by any change.
    Returns A by default (including no-changes case).
    """
    cur_by_id = _stages_by_id(current)
    new_by_id = _stages_by_id(proposed)
    cur_ids = set(cur_by_id)
    new_ids = set(new_by_id)

    # D: stage_type changed on a kept stage
    for sid in cur_ids & new_ids:
        if cur_by_id[sid]["stage_type"] != new_by_id[sid]["stage_type"]:
            return ClassificationResult(
                category=EditCategory.D,
                warnings=[f"stage_type changed on stage {sid}"],
                in_flight={sid: in_flight.get(sid, 0)},
            )

    # C: stages removed OR newly paused
    removed_ids = cur_ids - new_ids
    paused_ids = {
        sid for sid in cur_ids & new_ids
        if not cur_by_id[sid].get("paused_at") and new_by_id[sid].get("paused_at")
    }
    if removed_ids or paused_ids:
        affected = removed_ids | paused_ids
        return ClassificationResult(
            category=EditCategory.C,
            warnings=[f"stages affected: {sorted(affected)}"],
            in_flight={sid: in_flight.get(sid, 0) for sid in affected},
        )

    # B: stages added OR reordered OR unpaused
    added_ids = new_ids - cur_ids
    reordered = any(
        cur_by_id[sid]["position"] != new_by_id[sid]["position"]
        for sid in cur_ids & new_ids
    )
    unpaused_ids = {
        sid for sid in cur_ids & new_ids
        if cur_by_id[sid].get("paused_at") and not new_by_id[sid].get("paused_at")
    }
    if added_ids or reordered or unpaused_ids:
        warnings = []
        if added_ids:
            warnings.append(f"added: {sorted(added_ids)}")
        if reordered:
            warnings.append("reordered")
        if unpaused_ids:
            warnings.append(f"unpaused: {sorted(unpaused_ids)}")
        return ClassificationResult(
            category=EditCategory.B,
            warnings=warnings,
            in_flight={},
        )

    # A: anything else (config tweaks on kept stages, no shape change)
    return ClassificationResult(category=EditCategory.A)
