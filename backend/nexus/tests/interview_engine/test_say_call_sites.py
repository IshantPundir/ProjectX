"""AST invariant: every session.say(...) call in app/modules/interview_engine/
must be inside StructuredInterviewAgent._say.

Precedent: tests/test_module_boundaries.py uses the same pattern
(ast.parse + ast.walk + assertion + helpful failure message).

This test catches future regressions where a contributor adds a direct
session.say(...) call that bypasses the safety gate. The structured
agent's three-layer guardrail (spec §3.1) requires this invariant; the
guard is brittle without enforcement.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENGINE_DIR = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "interview_engine"
)


def _is_session_say_call(node: ast.AST) -> bool:
    """True if node is a Call whose .func is .say on something containing 'session'."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "say":
        return False
    # Resolve the base — could be `session.say`, `self.session.say`,
    # `agent.session.say`, etc. Walk down to the innermost Name/Attribute
    # and check if any segment contains "session".
    base = func.value
    parts: list[str] = []
    while True:
        if isinstance(base, ast.Attribute):
            parts.append(base.attr)
            base = base.value
            continue
        if isinstance(base, ast.Name):
            parts.append(base.id)
            break
        return False
    return any("session" in p.lower() for p in parts)


def _find_say_method_lineno_range(
    tree: ast.Module,
) -> tuple[int, int] | None:
    """Find StructuredInterviewAgent._say's lineno range, if present."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "StructuredInterviewAgent":
            continue
        for item in node.body:
            if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)) and item.name == "_say":
                return item.lineno, item.end_lineno or item.lineno
    return None


def test_session_say_only_called_inside_structured_agent_say() -> None:
    violations: list[str] = []

    for py_file in ENGINE_DIR.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            violations.append(f"{py_file}: SyntaxError {exc}")
            continue

        # In structured_agent.py, find _say's lineno range so we can allow
        # session.say(...) calls inside it.
        say_range: tuple[int, int] | None = None
        if py_file.name == "structured_agent.py":
            say_range = _find_say_method_lineno_range(tree)

        for node in ast.walk(tree):
            if not _is_session_say_call(node):
                continue
            lineno = getattr(node, "lineno", 0)
            inside_say = (
                say_range is not None
                and say_range[0] <= lineno <= say_range[1]
            )
            if not inside_say:
                # Find enclosing function/class for a helpful error message.
                rel = py_file.relative_to(ENGINE_DIR)
                violations.append(
                    f"{rel}:{lineno} — session.say(...) outside "
                    f"StructuredInterviewAgent._say. Every utterance must "
                    f"go through _say so the SpeechRenderHandle pipeline is "
                    f"the single source of truth for agent-spoken content."
                )

    assert not violations, (
        "AST invariant failed: session.say(...) must be the sole "
        "responsibility of StructuredInterviewAgent._say.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
