"""Prompt loaders for nexus.

Two loaders share an include-resolution helper:

* ``PromptLoader`` — original loader; reads ``prompts/v{N}/<name>.txt``.
  Versioning is at the directory level (``v1``, ``v2`` are sibling dirs
  containing the full set of prompts). Used by the JD pipeline and the
  question-bank generator.

* ``TemplateLoader`` — per-template versioning; reads
  ``<base>/<role>/<name>.<version>.txt``. Two versions of the same
  template (``intro.v1.txt`` and ``intro.v2.txt``) live side-by-side in
  the same role directory and can be loaded simultaneously. Used by the
  structured AI Screening Agent (`app/modules/interview_engine/`),
  where the design doc mandates per-template versioning so old sessions
  keep running on their pinned versions while new sessions adopt the
  latest.

A future ``/api/admin/prompts/reload`` endpoint can bust the caches
without a restart (not in scope here). Failures to load are loud — the
caller gets ``FileNotFoundError``, not a silent empty string.

Include directive: a prompt body may reference another prompt with the
literal ``{{include:other_name}}`` token. Includes resolve eagerly at
load time and the resolved body is what gets cached. Cycles raise
``RuntimeError``. Includes do not nest more than 8 deep. The include
helper is shared between both loaders; lookup-path semantics differ.

Dev-mode reload: ``TemplateLoader(reload_on_change=True)`` re-reads a
template from disk when the file's mtime changes. Default is ``False``;
production is cache-forever (process restart on deploy invalidates).
``app.modules.interview_engine`` instantiates with
``reload_on_change=settings.environment == "development"``.
"""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Repository layout: backend/nexus/prompts/v{version}/<name>.txt
# __file__ is backend/nexus/app/ai/prompts.py → parents[2] == backend/nexus
PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "prompts"

_INCLUDE_RE = re.compile(r"\{\{include:([A-Za-z0-9_./-]+)\}\}")
_INCLUDE_MAX_DEPTH = 8


def _resolve_includes_in_body(
    body: str,
    *,
    lookup: Callable[[str], Path],
    parents: tuple[str, ...] = (),
    max_depth: int = _INCLUDE_MAX_DEPTH,
) -> str:
    """Replace ``{{include:NAME}}`` tokens with the resolved bodies.

    ``lookup(name)`` returns the file path for an included template name.
    The two loaders pass different lookups (flat-by-name vs.
    role/version-aware) — the helper itself is layout-agnostic.

    Raises ``RuntimeError`` on include cycle or depth exceeded;
    ``FileNotFoundError`` on a missing include target.
    """

    def _replace(match: re.Match[str]) -> str:
        include_name = match.group(1)
        if include_name in parents:
            cycle = " -> ".join((*parents, include_name))
            raise RuntimeError(f"Prompt include cycle detected: {cycle}")
        if len(parents) >= max_depth:
            raise RuntimeError(
                f"Prompt include depth exceeded {max_depth} at {include_name}"
            )
        nested_path = lookup(include_name)
        if not nested_path.exists():
            raise FileNotFoundError(
                f"Prompt include not found: {include_name} expected at {nested_path}"
            )
        nested_body = nested_path.read_text(encoding="utf-8")
        return _resolve_includes_in_body(
            nested_body,
            lookup=lookup,
            parents=(*parents, include_name),
            max_depth=max_depth,
        )

    return _INCLUDE_RE.sub(_replace, body)


class PromptLoader:
    """Flat directory-versioned prompts: ``prompts/v{N}/<name>.txt``."""

    def __init__(self, version: str = "v1") -> None:
        self._version = version
        self._cache: dict[str, str] = {}

    @property
    def version(self) -> str:
        """The prompts/v{N}/ directory this loader reads from."""
        return self._version

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = PROMPTS_ROOT / self._version / f"{name}.txt"
            if not path.exists():
                raise FileNotFoundError(
                    f"Prompt not found: version={self._version} name={name} "
                    f"expected at {path}"
                )
            body = path.read_text(encoding="utf-8")
            resolved = _resolve_includes_in_body(
                body,
                lookup=lambda inc: PROMPTS_ROOT / self._version / f"{inc}.txt",
            )
            self._cache[name] = resolved
            logger.info(
                "prompts.loaded",
                name=name,
                version=self._version,
                chars=len(resolved),
            )
        return self._cache[name]

    # ``load`` is an alias for ``get`` — the two names are interchangeable.
    # ``get`` is the original name (kept for back-compat); ``load`` is the
    # preferred name in new callers (reads more naturally: "load a prompt").
    load = get

    def load_pair(self, common_name: str, type_name: str) -> str:
        """Concatenate a common header file with a per-type specialization file.

        Used by question_bank actors: common = 'question_bank_common',
        type = 'question_bank_phone_screen'. Returns header + '\n\n' + type.
        """
        header = self.get(common_name)
        specialization = self.get(type_name)
        return f"{header}\n\n{specialization}"


class TemplateLoader:
    """Per-template versioning: ``<base>/<role>/<name>.<version>.txt``.

    The structured AI Screening Agent uses this so two versions of the
    same template (e.g. ``intro.v1.txt`` and ``intro.v2.txt``) can coexist
    in the same role directory. Sessions pin the version at start time
    (recorded in ``InterviewState.prompt_versions``) and continue running
    on their pinned versions; new sessions can pick up newer versions.

    Cache key: ``(role, name, version)``. Cache stores
    ``(resolved_body, mtime_at_load)``.

    ``reload_on_change=True`` (dev-mode) re-reads a template when its
    file mtime changes. Includes are NOT mtime-tracked individually —
    a stale include will be picked up on the next access of any
    template that references it (because the parent's mtime check
    determines whether to re-resolve). For dev-iteration this is
    sufficient; production runs with ``reload_on_change=False`` and is
    invalidated by process restart.

    Includes within a template look up siblings in the SAME role at the
    SAME version: ``<base>/<role>/<include_name>.<version>.txt``. Cross-
    role includes are intentionally not supported in v1; if needed, add
    a ``shared/`` role with version-pinned files and reference them via
    a future qualified-include syntax (out of scope here).
    """

    def __init__(
        self,
        base_path: Path,
        *,
        reload_on_change: bool = False,
    ) -> None:
        self._base = base_path
        self._reload = reload_on_change
        self._cache: dict[tuple[str, str, str], tuple[str, float]] = {}

    def _path(self, role: str, name: str, version: str) -> Path:
        return self._base / role / f"{name}.{version}.txt"

    def get(self, role: str, name: str, version: str) -> str:
        """Return the resolved body for ``role/name.version``.

        Raises ``FileNotFoundError`` if the template (or any include) is
        missing; ``RuntimeError`` on include cycle or depth exceeded.
        """
        key = (role, name, version)
        path = self._path(role, name, version)
        if not path.exists():
            raise FileNotFoundError(
                f"Template not found: role={role} name={name} version={version} "
                f"expected at {path}"
            )

        if key in self._cache:
            cached_body, cached_mtime = self._cache[key]
            if not self._reload:
                return cached_body
            current_mtime = path.stat().st_mtime
            if current_mtime == cached_mtime:
                return cached_body
            # File touched on disk — fall through to reload.
            logger.info(
                "templates.reload_on_change",
                role=role,
                name=name,
                version=version,
                cached_mtime=cached_mtime,
                current_mtime=current_mtime,
            )

        body = path.read_text(encoding="utf-8")
        resolved = _resolve_includes_in_body(
            body,
            lookup=lambda inc: self._base / role / f"{inc}.{version}.txt",
        )
        mtime = path.stat().st_mtime
        self._cache[key] = (resolved, mtime)
        logger.info(
            "templates.loaded",
            role=role,
            name=name,
            version=version,
            chars=len(resolved),
        )
        return resolved

    def hash(self, role: str, name: str, version: str) -> str:
        """Return ``sha256:<hex>`` of the resolved template body.

        Used by the audit envelope's ``controller_prompt_hash`` /
        ``task_prompt_hashes`` fields. Computed on demand from the same
        cached body returned by ``get()``.
        """
        body = self.get(role, name, version)
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


prompt_loader = PromptLoader()
