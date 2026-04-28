"""Tests for the PromptLoader — file-system-based prompt versioning."""

import pytest

from app.ai.prompts import PromptLoader, prompt_loader


def test_loads_jd_enrichment_prompt():
    """The Phase 1 enrichment prompt must be loadable by name."""
    content = prompt_loader.get("jd_enrichment")
    assert len(content) > 100
    assert "enriched_jd" in content


def test_caches_repeated_reads():
    """Second call for the same prompt returns the cached value without
    re-reading the file."""
    loader = PromptLoader(version="v1")
    first = loader.get("jd_enrichment")
    second = loader.get("jd_enrichment")
    assert first is second  # identity, not just equality — cached


def test_missing_prompt_raises():
    """Unknown prompt name raises FileNotFoundError."""
    loader = PromptLoader(version="v1")
    with pytest.raises(FileNotFoundError):
        loader.get("nonexistent_prompt_name")
