"""Model routing utilities.

Centralizes model selection by use-case and provider/client routing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import (
    CHAT_MODEL,
    CODEX_MODEL,
    IMAGE_MODEL,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    VIDEO_MODEL,
)


def _build_openai_client() -> AsyncOpenAI:
    """Create the default OpenAI-compatible client."""
    kwargs: dict = {"api_key": OPENAI_API_KEY}
    if OPENAI_API_BASE:
        kwargs["base_url"] = OPENAI_API_BASE
    return AsyncOpenAI(**kwargs)


@dataclass(frozen=True)
class RoutedModel:
    """Resolved model + client pair."""

    client: AsyncOpenAI
    model: str


class ModelRouter:
    """Resolve model IDs and API clients for different bot workloads."""

    _OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    _OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")

    def __init__(self) -> None:
        self.openai_client = _build_openai_client()
        self.ollama_client = AsyncOpenAI(
            base_url=self._OLLAMA_BASE_URL,
            api_key=self._OLLAMA_API_KEY,
        )

    def chat_model(self) -> str:
        return CHAT_MODEL

    def image_model(self) -> str:
        return IMAGE_MODEL

    def video_model(self) -> str:
        return VIDEO_MODEL

    def codex_model(self) -> str:
        return CODEX_MODEL

    def resolve(self, model_id: str) -> RoutedModel:
        """Route ollama/* models to local Ollama; all others to default OpenAI client."""
        if model_id.startswith("ollama/"):
            return RoutedModel(client=self.ollama_client, model=model_id.removeprefix("ollama/"))
        return RoutedModel(client=self.openai_client, model=model_id)

    # ------------------------------------------------------------------
    # Cross-endpoint JSON completion helper
    # ------------------------------------------------------------------
    # Some OpenAI models (gpt-5-codex, o1/o3 reasoning variants) are only
    # available via /v1/responses and 404 on /v1/chat/completions. This helper
    # picks the right endpoint transparently and always returns the raw JSON
    # string produced by the model.
    # ------------------------------------------------------------------

    _RESPONSES_ONLY_PREFIXES = ("gpt-5-codex", "o1", "o3", "o4")

    @classmethod
    def _needs_responses_api(cls, model: str) -> bool:
        m = (model or "").lower()
        return any(m.startswith(p) for p in cls._RESPONSES_ONLY_PREFIXES)

    async def create_json(
        self,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Call the LLM and return its raw JSON text reply.

        Uses /v1/responses for codex / reasoning models, otherwise
        /v1/chat/completions with response_format=json_object.
        """
        routed = self.resolve(model_id)

        if self._needs_responses_api(routed.model):
            response = await routed.client.responses.create(
                model=routed.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={"format": {"type": "json_object"}},
            )
            # SDK exposes a convenience property that concatenates all text
            # output items; fall back to manual walk if unavailable.
            text = getattr(response, "output_text", None)
            if text:
                return text
            parts: list[str] = []
            for item in getattr(response, "output", []) or []:
                for c in getattr(item, "content", []) or []:
                    t = getattr(c, "text", None)
                    if t:
                        parts.append(t)
            return "".join(parts) or "{}"

        response = await routed.client.chat.completions.create(
            model=routed.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"
