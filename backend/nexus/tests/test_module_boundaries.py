"""Phase 4d-3 — module boundary lint test.

Walks every .py file under `app/modules/` and asserts no cross-module
deep imports of behavior-bearing submodules (service, router, authz,
state_machine, errors, schemas, etc.). A "cross-module deep import"
means a statement of the form `from app.modules.<m>.<internal> import X`
that appears in a file outside `app/modules/<m>/`.

The discipline:
- Cross-module callers must use the module's public API:
  `from app.modules.<m> import X`.
- Intra-module deep imports are allowed
  (`from app.modules.<self>.<internal> import X` inside `<self>/`).
- Cross-module `from app.modules.<m>.models import X` is the ONE
  exception — it's allowed because (a) it imports data classes only,
  (b) ORM ergonomics frequently require referencing model classes for
  joins/queries, and (c) some legitimate cycle-breaking patterns
  (auth ↔ org_units, see auth/context.py + org_units/service.py)
  cannot use the module's __init__ without re-entering a partially
  initialized package.
- Routers + Dramatiq actors are deep-imported by `app/main.py` and
  `app/worker.py` — but those files live OUTSIDE `app/modules/`, so
  they don't trip this test.

If a new cross-module deep import lands and it's legitimate (model
ergonomics, etc.), update this test ONLY for `models` — never extend
the allowlist to other submodules. If a non-model deep import is
legitimate (rare), add the symbol to the destination module's
`__init__.py` __all__ and rewrite the caller. Do NOT add the file to
an exemption list — the rule is the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Modules whose public surface lives at app/modules/<m>/__init__.py.
# Add new modules here as they're created.
KNOWN_DOMAIN_MODULES = frozenset(
    {
        "admin",
        "ats",
        "analysis",
        "audit",
        "auth",
        "candidates",
        "interview_engine",
        "interview_engine_v2",
        "interview_runtime",
        "jd",
        "notifications",
        "org_units",
        "pipelines",
        "question_bank",
        "reporting",
        "roles",
        "scheduler",
        "session",
        "settings",
        "tenant_settings",
    }
)

# Cross-module deep imports of these submodules are ALLOWED.
# `models` is the ORM data-class allowance — see this file's docstring.
ALLOWED_CROSS_MODULE_DEEP_SUBMODULES = frozenset({"models"})


def _module_root() -> Path:
    """Locate `app/modules/` from this test file's location.

    The test sits at `backend/nexus/tests/test_module_boundaries.py`;
    `app/modules/` is at `backend/nexus/app/modules/`.
    """
    here = Path(__file__).resolve().parent
    return here.parent / "app" / "modules"


def _module_owning_file(path: Path, modules_root: Path) -> str:
    """Return the domain-module name owning `path`.

    e.g. `app/modules/jd/service.py` -> `"jd"`.
    """
    rel = path.relative_to(modules_root)
    return rel.parts[0]


def _iter_python_files(root: Path):
    for p in root.rglob("*.py"):
        # Skip __pycache__ and similar — rglob already handles dotfiles.
        if "__pycache__" in p.parts:
            continue
        yield p


def test_no_cross_module_deep_imports():
    modules_root = _module_root()
    assert modules_root.is_dir(), f"expected app/modules at {modules_root}"

    violations: list[tuple[str, int, str]] = []

    for py_file in _iter_python_files(modules_root):
        owning_module = _module_owning_file(py_file, modules_root)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as exc:
            raise AssertionError(f"failed to parse {py_file}: {exc}") from exc

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None:
                continue
            if not node.module.startswith("app.modules."):
                continue

            # node.module is e.g. "app.modules.jd.service"
            # We only flag depth >= 4 components (app, modules, <m>, <internal>).
            parts = node.module.split(".")
            if len(parts) < 4:
                # `from app.modules.jd import X` — public API, allowed.
                continue
            target_module = parts[2]
            if target_module not in KNOWN_DOMAIN_MODULES:
                continue
            if target_module == owning_module:
                # Intra-module deep import — allowed.
                continue
            sub_module = parts[3]
            if sub_module in ALLOWED_CROSS_MODULE_DEEP_SUBMODULES:
                # Cross-module deep import of `models` (data classes) is the
                # ORM-ergonomics carve-out — allowed.
                continue

            # Cross-module deep import of a behavior-bearing submodule —
            # violation.
            violations.append(
                (
                    str(py_file.relative_to(modules_root.parent.parent)),
                    node.lineno,
                    node.module,
                )
            )

    assert not violations, (
        "Cross-module deep imports detected — every cross-module import "
        "must go through the destination module's `__init__.py` public API.\n\n"
        "Violations:\n"
        + "\n".join(f"  {path}:{lineno} -> {module}" for path, lineno, module in violations)
    )
