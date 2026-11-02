from groq import Groq


class GroqClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 256):
        if not api_key:
            raise RuntimeError("Missing environment variable: GROQ_API_KEY")
        if not model:
            raise RuntimeError("Missing environment variable: LLM_MODEL")
        self._client = Groq(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens  # answers are <=3 sentences; also keeps
                                        # the request under free-tier OTPM limits

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (response.choices[0].message.content or "").strip()
