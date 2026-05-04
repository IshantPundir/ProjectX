"""PromptLoader — reads prompts/v{N}/<name>.txt at first access, caches in memory.

A future /api/admin/prompts/reload endpoint can bust the cache without a
restart (not in 2A). Failures to load are loud: the caller gets
FileNotFoundError, not a silent empty string.

Include directive: a prompt body may reference another prompt with the literal
``{{include:other_name}}`` token (e.g. ``{{include:interview/_shared_voice_rules}}``).
Includes resolve eagerly at load time and the resolved body is what gets cached.
Cycles raise RuntimeError. Includes do not nest more than 8 deep."""

import re
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Repository layout: backend/nexus/prompts/v{version}/<name>.txt
# __file__ is backend/nexus/app/ai/prompts.py → parents[2] == backend/nexus
PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "prompts"

_INCLUDE_RE = re.compile(r"\{\{include:([A-Za-z0-9_./-]+)\}\}")
_INCLUDE_MAX_DEPTH = 8


class PromptLoader:
    def __init__(self, version: str = "v1") -> None:
        self._version = version
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            content = self._load_and_resolve(name, parents=())
            self._cache[name] = content
            logger.info(
                "prompts.loaded",
                name=name,
                version=self._version,
                chars=len(content),
            )
        return self._cache[name]

    def _load_and_resolve(self, name: str, *, parents: tuple[str, ...]) -> str:
        if name in parents:
            cycle = " -> ".join((*parents, name))
            raise RuntimeError(f"Prompt include cycle detected: {cycle}")
        if len(parents) >= _INCLUDE_MAX_DEPTH:
            raise RuntimeError(
                f"Prompt include depth exceeded {_INCLUDE_MAX_DEPTH} at {name}"
            )
        path = PROMPTS_ROOT / self._version / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt not found: version={self._version} name={name} "
                f"expected at {path}"
            )
        body = path.read_text(encoding="utf-8")

        def _replace(match: re.Match[str]) -> str:
            include_name = match.group(1)
            return self._load_and_resolve(include_name, parents=(*parents, name))

        return _INCLUDE_RE.sub(_replace, body)

    def load_pair(self, common_name: str, type_name: str) -> str:
        """Concatenate a common header file with a per-type specialization file.

        Used by question_bank actors: common = 'question_bank_common',
        type = 'question_bank_phone_screen'. Returns header + '\n\n' + type.
        """
        header = self.get(common_name)
        specialization = self.get(type_name)
        return f"{header}\n\n{specialization}"


prompt_loader = PromptLoader()
