"""
LLM provider abstraction.

All pipeline stages that call an LLM must use the ``LLMProvider`` protocol so
the provider can be swapped without touching business logic.

Swapping the provider
---------------------
1. Implement a class with a ``complete(prompt, system)`` method.
2. Pass an instance of it wherever ``llm: LLMProvider`` is expected.

Example (using Azure with custom model)::

    from pipeline.llm import AzureProvider
    provider = AzureProvider(model="my-deployment-name")
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

# Used when endpoint does not require a specific deployment name in the request
DEFAULT_AZURE_MODEL = "gpt-4"


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


class AzureProvider:
    """Azure cloud endpoint (OpenAI-compatible) implementation of :class:`LLMProvider`.

    Reads ``AZURE_LLM_ENDPOINT`` and ``AZURE_LLM_API_KEY`` from the environment.
    Optional ``AZURE_LLM_MODEL`` (or constructor *model*) is the deployment/model name.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 8192,
    ) -> None:
        from openai import OpenAI

        self.base_url = (base_url or os.environ["AZURE_LLM_ENDPOINT"]).rstrip("/")
        self.api_key = api_key or os.environ["AZURE_LLM_API_KEY"]
        self.model = model or os.environ.get("AZURE_LLM_MODEL", DEFAULT_AZURE_MODEL)
        self.max_tokens = max_tokens
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def complete(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content or ""


def get_default_provider() -> AzureProvider:
    """Return a ready-to-use :class:`AzureProvider` using env vars."""
    return AzureProvider()
