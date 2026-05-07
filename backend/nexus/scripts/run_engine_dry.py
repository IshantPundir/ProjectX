"""Dry-run harness for the structured agent.

Drives the orchestrator with mocked LiveKit, scripted candidate utterances, and
either stubbed (default) or real Judge/Speaker services. Prints the final
SessionResult JSON and the audit envelope event sequence.

Usage:
    python -m scripts.run_engine_dry --scenario scripts/scenarios/quick_smoke.yaml --mode stub
    python -m scripts.run_engine_dry --scenario scripts/scenarios/quick_smoke.yaml --mode live
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import yaml

from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import AttributePublisher
from app.modules.interview_engine.judge.fallback import FallbackReason
from app.modules.interview_engine.models.judge import (
    AdvancePayload, EndSessionPayload, JudgeOutput, NextAction, PoliteClosePayload, TurnMetadata,
)
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig,
)
from app.modules.interview_engine.state.engine import StateEngine
from app.modules.interview_runtime.schemas import SessionConfig


@dataclass(slots=True)
class ScenarioStep:
    utterance: str
    expected_next_action: str | None = None
    expected_observations_count: int | None = None
    notes: str | None = None


def _resolve_fixture_path(scenario_path: Path, fixture_rel: str) -> Path:
    """Resolve the session_config_fixture path relative to the container's /app root."""
    candidate = Path(fixture_rel)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    # Try relative to cwd (/app in container)
    from_cwd = Path.cwd() / fixture_rel
    if from_cwd.exists():
        return from_cwd
    # Try relative to the scenario file's parent
    from_scenario = scenario_path.parent / fixture_rel
    if from_scenario.exists():
        return from_scenario
    # Try one level up from the scenario file (scripts/../)
    from_scripts_parent = scenario_path.parent.parent / fixture_rel
    if from_scripts_parent.exists():
        return from_scripts_parent
    raise FileNotFoundError(
        f"session_config_fixture not found: {fixture_rel!r}\n"
        f"  Tried: {from_cwd}, {from_scenario}, {from_scripts_parent}"
    )


def _load_scenario(path: Path) -> tuple[SessionConfig, list[ScenarioStep]]:
    raw = yaml.safe_load(path.read_text())
    cfg_path = _resolve_fixture_path(path, raw["session_config_fixture"])
    cfg = SessionConfig.model_validate_json(cfg_path.read_text())
    steps = [
        ScenarioStep(
            utterance=s["utterance"],
            expected_next_action=s.get("expected_next_action"),
            expected_observations_count=s.get("expected_observations_count"),
            notes=s.get("notes"),
        )
        for s in raw["candidate_responses"]
    ]
    return cfg, steps


class _StubJudgeResult:
    """Stub Judge result that always advances to the next pending mandatory."""

    def __init__(self, target: str):
        self.judge_output = JudgeOutput(
            thought="stub", observations=[], candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id=target),
            turn_metadata=TurnMetadata(),
        )
        self.is_fallback = False
        self.fallback_reason: FallbackReason | None = None
        self.original_failure_context: dict | None = None
        self.latency_ms = 10
        self.usage: dict[str, int] | None = {"prompt_tokens": 1, "completion_tokens": 1}
        self.model_used = "stub"


class _StubJudgeService:
    """Always advances to the queue's next pending mandatory; no rubric reasoning."""

    def __init__(self, *, state_engine: StateEngine):
        self._state_engine = state_engine

    async def call(self, **kwargs) -> _StubJudgeResult:
        target = self._state_engine.next_pending_mandatory_id()
        if target is None:
            # All mandatory questions done — emit end_session so the lifecycle
            # closes cleanly instead of trying to backward-advance.
            result = _StubJudgeResult.__new__(_StubJudgeResult)
            result.judge_output = JudgeOutput(
                thought="stub: all mandatory complete",
                observations=[], candidate_claims=[],
                next_action=NextAction.end_session,
                next_action_payload=EndSessionPayload(initiated_by="agent_initiated"),
                turn_metadata=TurnMetadata(),
            )
            result.is_fallback = False
            result.fallback_reason = None
            result.original_failure_context = None
            result.latency_ms = 10
            result.usage = {"prompt_tokens": 1, "completion_tokens": 1}
            result.model_used = "stub"
            return result
        return _StubJudgeResult(target)


class _StubSpeakerHandle:
    def __init__(self, text: str):
        self._text = text
        self.usage = {"prompt_tokens": 5, "completion_tokens": 5}
        self.latency_ms_first_token = 20
        self.latency_ms_total = 80

    def stream(self):
        async def gen():
            yield self._text
        return gen()

    async def final_text(self) -> str:
        return self._text


class _StubSpeakerService:
    async def stream(self, **kwargs) -> _StubSpeakerHandle:
        si = kwargs["speaker_input"]
        text = f"[stub: {si.instruction_kind.value}] {si.bank_text or ''}".strip()
        return _StubSpeakerHandle(text)


def _build_live_services(state_engine: StateEngine) -> tuple[Any, Any]:
    """Construct real JudgeService + SpeakerService from settings + prompts.

    NOTE: For prompt iteration; not used in CI.
    """
    import hashlib

    from openai import AsyncOpenAI

    from app.ai.prompts import prompt_loader
    from app.config import settings
    from app.modules.interview_engine.judge.service import JudgeService
    from app.modules.interview_engine.speaker.service import SpeakerService

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    judge_prompt = prompt_loader.get("engine/judge.system")
    speaker_prompt = prompt_loader.get("engine/speaker.system")
    judge = JudgeService(
        openai_client=openai_client,
        model=settings.engine_judge_model,
        system_prompt=judge_prompt,
        system_prompt_hash="sha256:" + hashlib.sha256(judge_prompt.encode("utf-8")).hexdigest(),
        next_pending_mandatory_resolver=state_engine.next_pending_mandatory_id,
        total_budget_ms=settings.engine_judge_total_budget_ms,
        retry_wait_ms=settings.engine_judge_retry_wait_ms,
    )
    speaker = SpeakerService(
        openai_client=openai_client,
        model=settings.engine_speaker_model,
        system_prompt=speaker_prompt,
        system_prompt_hash="sha256:" + hashlib.sha256(speaker_prompt.encode("utf-8")).hexdigest(),
    )
    return judge, speaker


async def _run(scenario_path: Path, mode: str) -> int:
    cfg, steps = _load_scenario(scenario_path)

    state_engine = StateEngine(session_config=cfg)
    state_engine.set_persona_name("Sam")

    if mode == "live":
        judge, speaker = _build_live_services(state_engine)
    else:
        judge = _StubJudgeService(state_engine=state_engine)
        speaker = _StubSpeakerService()

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)

    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    collector = EventCollector(
        session_id=cfg.session_id, tenant_id="dry-run", correlation_id="dry-run-c",
        controller_prompt_hash="sha256:ctrl",
        model_versions={"judge": "stub", "speaker": "stub"} if mode == "stub" else {"judge": "live", "speaker": "live"},
        redaction_mode="full",
        task_prompt_hashes={"judge": "sha256:j", "speaker": "sha256:s"},
    )

    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="dry-run", config=OrchestratorConfig(),
    )

    print(f"=== Running scenario: {scenario_path.name} (mode={mode}) ===")
    print(f"Session: {cfg.session_id} | Questions: {len(cfg.stage.questions)} | Signals: {len(cfg.signal_metadata)}\n")

    await orch.on_enter(fake_agent)

    # Replay candidate utterances.
    from livekit.agents.llm import ChatMessage
    from livekit.agents.llm import StopResponse

    pass_count = 0
    fail_count = 0
    for i, step in enumerate(steps):
        # Stop replaying once the session lifecycle has moved to closing.
        if state_engine.lifecycle_snapshot().state.value == "closing":
            print(f"--- Turn {i+1}: session already closing — stopping replay ---\n")
            break

        print(f"--- Turn {i+1}: candidate utterance ---")
        print(f"  > {step.utterance}")
        msg = ChatMessage(role="user", content=[step.utterance])
        try:
            await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)
        except StopResponse:
            pass

        # Look up the latest judge.call event for assertions.
        latest = next(
            (e for e in reversed(collector.events) if e.kind in ("judge.call", "judge.fallback", "judge.synthetic")),
            None,
        )
        if latest is None:
            continue
        latest_action = (
            latest.payload.get("output", {}).get("next_action")
            or latest.payload.get("synthesized_output", {}).get("next_action")
        )
        latest_obs_count = len((latest.payload.get("output") or {}).get("observations") or [])
        print(f"    judge action: {latest_action}, observations={latest_obs_count}")

        if step.expected_next_action and latest_action != step.expected_next_action:
            print(f"    [FAIL] expected next_action={step.expected_next_action}, got {latest_action}")
            fail_count += 1
        elif step.expected_next_action:
            print(f"    [PASS] next_action={step.expected_next_action}")
            pass_count += 1

        if step.expected_observations_count is not None and latest_obs_count != step.expected_observations_count:
            print(f"    [FAIL] expected observations={step.expected_observations_count}, got {latest_obs_count}")
            fail_count += 1
        elif step.expected_observations_count is not None:
            print(f"    [PASS] observations={step.expected_observations_count}")
            pass_count += 1

        if step.notes:
            print(f"    notes: {step.notes}")
        print()

    # Finalize.
    result = await orch.on_close(fake_agent, audio_tuning_summary=None)

    print("=== Summary ===")
    print(f"  questions_asked: {result.questions_asked}")
    print(f"  total_probes_fired: {result.total_probes_fired}")
    print(f"  knockout_failures: {len(result.knockout_failures)}")
    print(f"  signal_ledger.next_seq: {result.signal_ledger.next_seq}")
    print(f"  signal_ledger.entries: {len(result.signal_ledger.entries)}")
    print()

    if pass_count + fail_count > 0:
        print(f"Assertions: {pass_count} passed, {fail_count} failed")

    print()
    print("=== Final SessionResult (truncated) ===")
    dump = result.model_dump(mode="json")
    # Truncate transcript for readability.
    if len(dump.get("full_transcript", [])) > 4:
        dump["full_transcript"] = dump["full_transcript"][:2] + ["... truncated ..."] + dump["full_transcript"][-2:]
    print(json.dumps(dump, indent=2)[:4000])
    return 1 if fail_count > 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, help="Path to scenario YAML")
    parser.add_argument(
        "--mode", choices=["stub", "live"], default="stub",
        help="stub: Judge always advances, Speaker echoes; live: real OpenAI calls (for prompt iteration; not used in CI)",
    )
    args = parser.parse_args()
    return asyncio.run(_run(Path(args.scenario), args.mode))


if __name__ == "__main__":
    sys.exit(main())
