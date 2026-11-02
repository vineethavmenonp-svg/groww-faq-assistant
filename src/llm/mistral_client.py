from src.llm.base import NotConfiguredClient


class MistralClient(NotConfiguredClient):
    def __init__(self, api_key: str = "", model: str = ""):
        super().__init__("mistral")
