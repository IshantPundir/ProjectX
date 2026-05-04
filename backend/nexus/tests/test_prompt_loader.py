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


def test_include_directive_unknown_target_raises():
    """An include pointing at a non-existent prompt raises FileNotFoundError."""
    import tempfile
    import textwrap
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        v = Path(tmp) / "v1"
        v.mkdir()
        (v / "leaf.txt").write_text(textwrap.dedent("""
            {{include:does_not_exist}}
        """).strip(), encoding="utf-8")
        loader = PromptLoader(version="v1")
        # Point loader at our temp dir.
        from app.ai import prompts as prompts_mod
        original_root = prompts_mod.PROMPTS_ROOT
        prompts_mod.PROMPTS_ROOT = Path(tmp)
        try:
            with pytest.raises(FileNotFoundError):
                loader.get("leaf")
        finally:
            prompts_mod.PROMPTS_ROOT = original_root


def test_include_directive_cycle_raises():
    """A → B → A include cycle raises RuntimeError."""
    import tempfile
    import textwrap
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        v = Path(tmp) / "v1"
        v.mkdir()
        (v / "a.txt").write_text("{{include:b}}", encoding="utf-8")
        (v / "b.txt").write_text("{{include:a}}", encoding="utf-8")
        loader = PromptLoader(version="v1")
        from app.ai import prompts as prompts_mod
        original_root = prompts_mod.PROMPTS_ROOT
        prompts_mod.PROMPTS_ROOT = Path(tmp)
        try:
            with pytest.raises(RuntimeError, match="cycle"):
                loader.get("a")
        finally:
            prompts_mod.PROMPTS_ROOT = original_root
