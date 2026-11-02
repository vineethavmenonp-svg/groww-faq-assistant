from __future__ import annotations

from src.config_loader import get_config, get_env
from src.llm.anthropic_client import AnthropicClient
from src.llm.base import LLMClient, NotConfiguredClient
from src.llm.gemini_client import GeminiClient
from src.llm.groq_client import GroqClient
from src.llm.mistral_client import MistralClient
from src.llm.openai_client import OpenAIClient

_STUBS = {
    "openai": OpenAIClient,
    "gemini": GeminiClient,
    "anthropic": AnthropicClient,
    "mistral": MistralClient,
}


def get_llm() -> LLMClient:
    provider = str(get_config()["llm"]["provider"]).lower()
    if provider == "groq":
        return GroqClient(api_key=get_env("GROQ_API_KEY"), model=get_env("LLM_MODEL"))
    stub = _STUBS.get(provider)
    if stub:
        return stub()
    return NotConfiguredClient(provider)
