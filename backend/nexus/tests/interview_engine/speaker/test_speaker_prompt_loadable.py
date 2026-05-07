"""Smoke test: every per-action Speaker prompt composes via PromptLoader."""
from app.ai.prompts import prompt_loader
from app.modules.interview_engine.models.speaker import InstructionKind


def test_all_per_action_speaker_prompts_load():
    """Every InstructionKind that goes through the Speaker LLM must have
    a corresponding per-action body file. The `repeat` kind is bypassed
    (cached delivery in the orchestrator) so it has no body file."""
    SPEAKER_LLM_KINDS = [
        InstructionKind.deliver_first_question,
        InstructionKind.deliver_question,
        InstructionKind.deliver_probe,
        InstructionKind.clarify,
        InstructionKind.redirect,
        InstructionKind.acknowledge_no_experience,
        InstructionKind.polite_close,
    ]
    for kind in SPEAKER_LLM_KINDS:
        body = prompt_loader.load_pair(
            "engine/speaker/_preamble",
            f"engine/speaker/{kind.value}",
        )
        assert "OUTPUT DISCIPLINE" in body, f"preamble missing for {kind.value}"
        assert "TASK" in body, f"task statement missing for {kind.value}"
        assert "EXAMPLES" in body or "EXAMPLE" in body, f"examples missing for {kind.value}"
        assert len(body) > 200, f"body suspiciously short for {kind.value}"


def test_repeat_has_no_body_file():
    """`repeat` is handled deterministically by the orchestrator (cached
    delivery, bypassing the Speaker LLM). Asserting absence prevents a
    future contributor from creating a redundant repeat.txt."""
    import pathlib
    from app.ai.prompts import PROMPTS_ROOT
    repeat_path = PROMPTS_ROOT / "v1" / "engine" / "speaker" / "repeat.txt"
    assert not repeat_path.exists()
