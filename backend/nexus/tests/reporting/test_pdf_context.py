"""Tests for 0–10 score scale in pdf/context.py (rescale from 0–100)."""
from app.modules.reporting.pdf.context import _bar_color, assessed_dimensions


def test_bar_color_green():
    assert _bar_color(8.0) == "#137a45"


def test_bar_color_amber():
    assert _bar_color(6.5) == "#b4791a"


def test_bar_color_red():
    assert _bar_color(4.0) == "#d23b34"


def test_assessed_dimensions_preserves_one_decimal_score():
    scores = {"technical": {"score": 8.1, "tier_label": "Strong"}}
    dims = assessed_dimensions(scores)
    assert len(dims) == 1
    assert dims[0]["score"] == 8.1
