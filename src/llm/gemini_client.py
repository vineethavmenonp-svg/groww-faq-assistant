from src.llm.base import NotConfiguredClient


class GeminiClient(NotConfiguredClient):
    def __init__(self, api_key: str = "", model: str = ""):
        super().__init__("gemini")
