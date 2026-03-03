"""
Stage 3 – Web Search for Certifications
=========================================
Executes web searches for each enriched component to discover safety
certifications, and returns structured results.

Responsibilities
----------------
- Accept a list of ``EnrichedComponent`` objects.
- For each component, execute its ``search_queries`` against the configured
  search provider (Brave, SerpAPI, or Google Custom Search).
- Parse result snippets and URLs to extract certification information.
- Record every search performed in a structured search log.
- Return a list of ``CertificationResult`` objects.

API contract
------------
Input  : ``list[EnrichedComponent]``
Output : ``list[CertificationResult]``

Environment variables used
--------------------------
- ``SEARCH_API_KEY``    – API key for the search provider.
- ``SEARCH_PROVIDER``  – One of ``brave``, ``serpapi``, ``google``.

Swapping the search provider
-----------------------------
Implement a new class that satisfies the ``SearchProvider`` protocol defined
in this module, then register it in ``_PROVIDERS``.

Example usage
-------------
::

    from pipeline.search import search_certifications

    results = search_certifications(enriched_components)
    # [CertificationResult(certifications=[CertificationFound(...)], ...), ...]
"""

from __future__ import annotations

from typing import Protocol

from pipeline.models import CertificationResult, EnrichedComponent


class SearchProvider(Protocol):
    """Interface that all search-provider adapters must satisfy."""

    def query(self, search_string: str) -> list[dict]:
        """Submit *search_string* and return a list of raw result dicts."""
        ...


def search_certifications(
    enriched_components: list[EnrichedComponent],
) -> list[CertificationResult]:
    """Search the web for certifications for each :class:`~pipeline.models.EnrichedComponent`.

    Parameters
    ----------
    enriched_components:
        Components enriched by :mod:`pipeline.analyse`.

    Returns
    -------
    list[CertificationResult]
        One result entry per input component, in the same order.
        Each result includes a ``search_log`` documenting every query run.

    Raises
    ------
    EnvironmentError
        If ``SEARCH_API_KEY`` or ``SEARCH_PROVIDER`` are not configured.
    """
    raise NotImplementedError("Stage 3 (search) is not yet implemented.")
