from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class NotConfiguredClient:
    def __init__(self, provider: str):
        self.provider = provider

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError(f"Provider {self.provider} is not configured in v1")
