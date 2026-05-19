"""Tests for app/ai/schemas.py Pydantic output models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas import KeytermExtractionOutput


class TestKeytermExtractionOutput:
    def test_valid_input_accepted(self) -> None:
        out = KeytermExtractionOutput(
            keyterms=[f"Brand{i}" for i in range(20)]
        )
        assert len(out.keyterms) == 20

    def test_too_few_terms_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=["Only", "Five", "Brands", "Here", "x"])

    def test_too_many_terms_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=[f"X{i}" for i in range(51)])

    def test_empty_string_in_list_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=["Valid"] * 9 + [""])

    def test_overly_long_term_rejected(self) -> None:
        too_long = "x" * 81
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=["Valid"] * 9 + [too_long])
