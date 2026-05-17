"""Pytest entry point for the Judge eval suite.

Marker discipline: @pytest.mark.prompt_quality is skipped by default
via pyproject.toml's addopts. Opt-in with: pytest -m prompt_quality

A/B mode: set JUDGE_PROMPT_VERSION=v1 (or v2) to run against that prompt.

Spec: docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md §4.5.
"""
import os

import pytest

from .corpus import load_all_fixtures
from .runner import format_failure, run_fixture


@pytest.mark.prompt_quality
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", load_all_fixtures(), ids=lambda f: f.id)
async def test_judge_decision_matches_expected(fixture):
    prompt_version = os.getenv("JUDGE_PROMPT_VERSION", "v2")
    result = await run_fixture(fixture, prompt_version=prompt_version)
    assert result.passed, format_failure(result)
