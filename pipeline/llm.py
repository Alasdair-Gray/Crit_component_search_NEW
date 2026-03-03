"""
LLM provider abstraction.

All pipeline stages that call an LLM must use the ``LLMProvider`` protocol so
the provider can be swapped without touching business logic.

Swapping the provider
---------------------
1. Implement a class with a ``complete(prompt, system)`` method.
2. Pass an instance of it wherever ``llm: LLMProvider`` is expected.

Example (using a different model)::

    from pipeline.llm import AnthropicProvider
    provider = AnthropicProvider(model="claude-opus-4-6")
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal interface every LLM adapter must satisfy."""

    def complete(self, prompt: str, system: str = "") -> str:
        """Send *prompt* to the model and return the text response.

        Parameters
        ----------
        prompt:
            The user-turn message.
        system:
            Optional system-prompt string.

        Returns
        -------
        str
            The model's text response.
        """
        ...


class AnthropicProvider:
    """Anthropic Claude implementation of :class:`LLMProvider`."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = 8192,
    ) -> None:
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
        )

    def complete(self, prompt: str, system: str = "") -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system or "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


def get_default_provider() -> AnthropicProvider:
    """Return a ready-to-use :class:`AnthropicProvider` using env vars."""
    return AnthropicProvider()
