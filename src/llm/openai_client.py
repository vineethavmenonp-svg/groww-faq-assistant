from src.llm.base import NotConfiguredClient


class OpenAIClient(NotConfiguredClient):
    def __init__(self, api_key: str = "", model: str = ""):
        super().__init__("openai")
