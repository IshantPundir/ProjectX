"""Phase 2 contract tests — pin the openai/instructor/Python uplift.

These tests assert the *interfaces* business code depends on still resolve:
- instructor.from_openai factory shape
- instructor.core.InstructorRetryException path
- openai exception classes used by _PERMANENT_EXCEPTIONS / _SAFE_MESSAGES
- OpenAI auto-instrumentor still attaches to chat.completions.create

Failures here indicate a vendor library moved out from under us — the fix
goes in app/ai/* or app/modules/jd/errors.py, not in this test."""

from __future__ import annotations

import sys


def test_python_version_is_3_13_or_newer():
    """Phase 2 lifts requires-python to >=3.13."""
    assert sys.version_info >= (3, 13), (
        f"Phase 2 requires Python 3.13+, got {sys.version_info}"
    )


def test_instructor_from_openai_factory_works():
    """instructor.from_openai(AsyncOpenAI()) still produces a usable client.

    The factory signature is unchanged 1.7.x → 1.15.x but explicit assertion
    locks the contract."""
    import instructor
    from openai import AsyncOpenAI

    raw = AsyncOpenAI(api_key="sk-test-not-real")
    client = instructor.from_openai(raw, mode=instructor.Mode.TOOLS_STRICT)

    assert isinstance(client, instructor.AsyncInstructor)
    # The wrapped chat namespace is what business code calls.
    assert hasattr(client, "chat")
    assert hasattr(client.chat, "completions")
    assert hasattr(client.chat.completions, "create")


def test_instructor_core_retry_exception_path_is_canonical():
    """InstructorRetryException lives at instructor.core in 1.15.x.

    instructor.exceptions still works but emits DeprecationWarning. Our code
    imports from instructor.core (app/modules/jd/errors.py:32,
    app/modules/jd/actors.py:24) — this test fails if the canonical path
    moves again."""
    from instructor.core import InstructorRetryException

    # Should be a real exception subclass.
    assert issubclass(InstructorRetryException, Exception)


def test_openai_permanent_exception_classes_resolve():
    """_PERMANENT_EXCEPTIONS in jd/actors.py references these by name. If
    openai 2.x ever renames one, this test fails before retry classification
    silently degrades."""
    import openai

    for name in (
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
    ):
        cls = getattr(openai, name, None)
        assert cls is not None, f"openai.{name} is missing in this version"
        assert issubclass(cls, Exception)


def test_openai_rate_limit_error_constructor_shape():
    """tests/test_jd_errors.py constructs openai.RateLimitError directly with
    (message, response=..., body=...) — pin that signature."""
    import openai

    class _FakeResponse:
        request = None
        status_code = 429
        headers: dict = {}

    exc = openai.RateLimitError("rate limit hit", response=_FakeResponse(), body=None)
    assert isinstance(exc, openai.RateLimitError)
    assert isinstance(exc, openai.APIStatusError)


def test_phase_1_otel_instrumentor_still_attached():
    """Phase 1 wired OpenAIInstrumentor at app startup. The 2.3b0 instrumentor
    + openai 2.x must remain compatible — if the instrumentor stops attaching,
    every chat.completions.create span vanishes silently."""
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

    instr = OpenAIInstrumentor()
    # We don't actually call .instrument() here (app/ai/otel.py owns that
    # idempotently). Just assert the class loads and exposes the contract.
    assert callable(instr.instrument)
    assert callable(instr.uninstrument)
    # instrumentation_dependencies returns the openai package range it covers.
    deps = instr.instrumentation_dependencies()
    assert any("openai" in d for d in deps), deps


def test_wrapt_is_under_2():
    """opentelemetry-instrumentation 0.58b0 caps wrapt<2. If a transitive
    bumps wrapt to 2.x, the OpenAI auto-instrumentor's wrap_function_wrapper
    call breaks at runtime. Catch it at test time, not at first LLM call."""
    import wrapt

    major = int(wrapt.__version__.split(".", 1)[0])
    assert major < 2, (
        f"wrapt is at {wrapt.__version__}; OTel openai-v2 instrumentor "
        f"requires <2. Check uv.lock and the Phase 1 transitive constraints."
    )
