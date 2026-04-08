"""PromptLoader — reads prompts/v{N}/<name>.txt at first access, caches in memory.

A future /api/admin/prompts/reload endpoint can bust the cache without a
restart (not in 2A). Failures to load are loud: the caller gets
FileNotFoundError, not a silent empty string."""

from pathlib import Path

import structlog

logger = structlog.get_logger()

# Repository layout: backend/nexus/prompts/v{version}/<name>.txt
# __file__ is backend/nexus/app/ai/prompts.py → parents[2] == backend/nexus
PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "prompts"


class PromptLoader:
    def __init__(self, version: str = "v1") -> None:
        self._version = version
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = PROMPTS_ROOT / self._version / f"{name}.txt"
            if not path.exists():
                raise FileNotFoundError(
                    f"Prompt not found: version={self._version} name={name} "
                    f"expected at {path}"
                )
            content = path.read_text(encoding="utf-8")
            self._cache[name] = content
            logger.info(
                "prompts.loaded",
                name=name,
                version=self._version,
                chars=len(content),
            )
        return self._cache[name]


prompt_loader = PromptLoader()
