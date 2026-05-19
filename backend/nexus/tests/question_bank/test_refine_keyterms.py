"""Tests for the per-bank STT keyterm extraction LLM helper."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.schemas import KeytermExtractionOutput
from app.modules.question_bank.refine import extract_bank_keyterms


@pytest.mark.asyncio
async def test_helper_returns_keyterm_extraction_output() -> None:
    """The helper assembles the user message, calls instructor, returns the model."""
    mock_response = KeytermExtractionOutput(
        keyterms=[f"Term{i}" for i in range(15)]
    )

    fake_client = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch(
        "app.modules.question_bank.refine.get_openai_client",
        return_value=fake_client,
    ):
        result = await extract_bank_keyterms(
            job_title="Sr. Integration Engineer",
            hiring_company_name="Workato",
            industry="SaaS",
            company_about="Workato is an enterprise automation platform.",
            hiring_bar="Builders who ship.",
            role_summary="Lead end-to-end iPaaS delivery on MuleSoft / TIBCO / Boomi.",
            signals=["5+ years with MuleSoft, TIBCO, or Boomi", "API-led architecture"],
            questions=[
                {"text": "How would you design API-led connectivity for order sync?"},
                {"text": "Walk through your end-to-end MuleSoft deployment."},
            ],
        )

    assert isinstance(result, KeytermExtractionOutput)
    assert len(result.keyterms) == 15
    # Confirm instructor was called with the configured model + schema
    call_kwargs = fake_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["response_model"] is KeytermExtractionOutput
    # The model used should be the keyterm-specific one from AIConfig
    assert "nano" in call_kwargs["model"].lower()


@pytest.mark.asyncio
async def test_helper_propagates_llm_exception() -> None:
    """LLM failure surfaces — the actor caller (Task 6) is responsible for swallowing."""
    fake_client = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("simulated LLM failure")
    )

    with patch(
        "app.modules.question_bank.refine.get_openai_client",
        return_value=fake_client,
    ):
        with pytest.raises(RuntimeError, match="simulated LLM failure"):
            await extract_bank_keyterms(
                job_title="x",
                hiring_company_name="x",
                industry="x",
                company_about="x",
                hiring_bar="x",
                role_summary="x",
                signals=["x"],
                questions=[{"text": "x"}],
            )


@pytest.mark.asyncio
async def test_helper_includes_all_inputs_in_user_message() -> None:
    """Verify the user message contains job_title, signals, questions — not just placeholders."""
    fake_response = KeytermExtractionOutput(keyterms=[f"T{i}" for i in range(10)])
    fake_client = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

    with patch(
        "app.modules.question_bank.refine.get_openai_client",
        return_value=fake_client,
    ):
        await extract_bank_keyterms(
            job_title="Cloud Architect",
            hiring_company_name="DataCorp",
            industry="SaaS",
            company_about="DataCorp builds streaming pipelines.",
            hiring_bar="Senior level.",
            role_summary="Design scalable AWS pipelines.",
            signals=["10+ years with Kubernetes"],
            questions=[{"text": "How would you design a fault-tolerant Kafka cluster?"}],
        )

    call_kwargs = fake_client.chat.completions.create.await_args.kwargs
    messages = call_kwargs["messages"]
    # Two messages: system + user
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_content = messages[1]["content"]
    # Verify the user message contains the key facts
    assert "Cloud Architect" in user_content
    assert "DataCorp" in user_content
    assert "Kubernetes" in user_content
    assert "Kafka" in user_content
