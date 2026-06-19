"""Display-unit conversion: internal 0–100 scores → recruiter-facing 0–10.

The scoring engine + DB keep 0–100 (calibrated verdict thresholds). This is the
single place the recruiter-facing 0–10 scale is produced — applied at the
read-model boundary only.
"""
from __future__ import annotations


def to_ten(score_100: int | float | None) -> float | None:
    """0–100 score → 0–10, one decimal. None passes through."""
    return None if score_100 is None else round(score_100 / 10, 1)
