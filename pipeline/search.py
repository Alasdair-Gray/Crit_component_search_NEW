"""
Stage 3 – Web Search for Certifications
=========================================
Executes web searches for each enriched component to discover safety
certifications, and returns structured results.

Responsibilities
----------------
- Accept a list of ``EnrichedComponent`` objects.
- For each component, execute its ``search_queries`` against the configured
  search provider (Brave by default; extensible via the ``_PROVIDERS`` registry).
- For the top 3–5 results per query, fetch the page content and ask the LLM
  to extract certification information as structured JSON.
- Record every search performed in a structured search log.
- Implement retry logic (3 retries with exponential backoff) for page fetches.
- Apply a short inter-query delay to respect search API rate limits.
- Return a list of ``CertificationResult`` objects (one per component).

API contract
------------
Input  : ``list[EnrichedComponent]``
Output : ``list[CertificationResult]``

Environment variables used
--------------------------
- ``SEARCH_API_KEY``    – API key for the search provider.
- ``SEARCH_PROVIDER``  – One of ``brave`` (default).

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

import json
import logging
import os
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from pipeline.llm import LLMProvider, get_default_provider
from pipeline.models import (
    CertificationFound,
    CertificationResult,
    Confidence,
    EnrichedComponent,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT = 10        # seconds per HTTP request
_USER_AGENT = (
    "Mozilla/5.0 (compatible; CriticalComponentChecker/1.0; "
    "safety-cert-lookup)"
)
_MAX_RETRIES = 3             # page-fetch retries before giving up
_RETRY_BASE_DELAY = 1.0     # seconds; doubles after each failed attempt
_TOP_RESULTS = 5             # maximum pages to fetch per search query
_MAX_PAGE_CHARS = 40_000    # truncate page content before sending to LLM
_INTER_QUERY_DELAY = 1.0    # seconds between search API calls (rate limiting)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_CERT_SYSTEM_PROMPT = (
    "You are a safety certification expert specialising in electrical component "
    "standards (UL, CE, IEC, EN, VDE, BASEC, HAR, etc.). Extract structured "
    "certification data from web page content. Return valid JSON only — "
    "no markdown fences, no explanation."
)

_CERT_EXTRACTION_PROMPT = """\
Component details:
  Name:         {component_name}
  Part number:  {part_number}
  Manufacturer: {manufacturer}

Web page content from: {url}
---
{page_content}
---

Extract safety compliance information for this specific component from the page content above.
Include only items that demonstrably relate to this component or its part number.

Distinguish clearly between:
- Standards: technical specifications the component complies with (e.g. IEC 62368-1, EN 55032,
  UL 508A). These describe WHAT requirements the component meets. They may not have a specific
  certificate number.
- Certificates: actual compliance documents issued to this specific component/part
  (e.g. UL Listing File E123456, CE certificate No. 67890, TÜV certificate DE-XY-123).
  Certificates MUST have a certificate or file number.

Return JSON in this exact format:
{{
  "standards": [
    {{
      "name": "<standard designation, e.g. IEC 62368-1 or EN 55032>",
      "scope": "<one-sentence description of what the standard covers>"
    }}
  ],
  "certificates": [
    {{
      "number": "<certificate or file number, e.g. UL E123456 or CE No. 67890>",
      "standard": "<which standard this certificate was issued against, e.g. UL 508>",
      "scope": "<one-sentence description of what is certified>"
    }}
  ]
}}

If nothing is found for this component, return {{"standards": [], "certificates": []}}.
"""

# ---------------------------------------------------------------------------
# Search providers
# ---------------------------------------------------------------------------


class SearchProvider(Protocol):
    """Interface that all search-provider adapters must satisfy."""

    def query(self, search_string: str) -> list[dict]:
        """Submit *search_string* and return a list of raw result dicts.

        Each dict must contain at minimum a ``url`` key.  Optional keys
        ``title`` and ``description`` are used for logging.
        """
        ...


class BraveSearchProvider:
    """Search provider backed by the Brave Search API.

    API reference: https://api.search.brave.com/app/documentation/web-search
    """

    _BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("SEARCH_API_KEY", "")
        if not self._api_key:
            raise EnvironmentError(
                "SEARCH_API_KEY environment variable is not set. "
                "Obtain an API key from https://brave.com/search/api/"
            )

    def query(self, search_string: str) -> list[dict]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        params = {"q": search_string, "count": _TOP_RESULTS}
        resp = requests.get(
            self._BASE_URL,
            headers=headers,
            params=params,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_results = data.get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in raw_results[:_TOP_RESULTS]
        ]


# Registry for swapping providers via SEARCH_PROVIDER env var.
_PROVIDERS: dict[str, type] = {
    "brave": BraveSearchProvider,
}


def get_default_search_provider() -> SearchProvider:
    """Return a configured provider based on the ``SEARCH_PROVIDER`` env var."""
    provider_name = os.environ.get("SEARCH_PROVIDER", "brave").lower()
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise EnvironmentError(
            f"Unknown SEARCH_PROVIDER {provider_name!r}. "
            f"Valid options: {sorted(_PROVIDERS)}"
        )
    return provider_cls()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _domain_of(url: str) -> str:
    """Return the bare domain (no www.) from *url*, lower-cased."""
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _fetch_page(url: str) -> str | None:
    """Fetch *url* with retries and return up to ``_MAX_PAGE_CHARS`` of text.

    Returns ``None`` if all attempts fail.
    """
    headers = {"User-Agent": _USER_AGENT}
    delay = _RETRY_BASE_DELAY
    for attempt in range(_MAX_RETRIES + 1):  # 1 original + _MAX_RETRIES retries
        try:
            resp = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text[:_MAX_PAGE_CHARS]
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                log.debug(
                    "Fetch %s attempt %d/%d failed: %s – retrying in %.1fs",
                    url,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay *= 2
            else:
                log.debug(
                    "Fetch %s failed after %d attempts: %s",
                    url,
                    _MAX_RETRIES + 1,
                    exc,
                )
    return None


def _parse_cert_response(
    response_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse the LLM's JSON response and return ``(standards, certificates)``."""
    text = response_text.strip()
    # Strip markdown fences if the LLM wrapped the output
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start + 3 : end]
            if inner.lower().startswith("json"):
                inner = inner[4:]
            text = inner.strip()
    data: dict[str, Any] = json.loads(text)
    return data.get("standards", []), data.get("certificates", [])


def _extract_certs_from_page(
    component: EnrichedComponent,
    url: str,
    page_content: str,
    llm: LLMProvider,
) -> list[CertificationFound]:
    """Ask *llm* to extract certifications from *page_content* for *component*.

    Returns an empty list on any failure so callers never raise.
    """
    prompt = _CERT_EXTRACTION_PROMPT.format(
        component_name=component.name,
        part_number=component.standardised_part_number,
        manufacturer=component.confirmed_manufacturer,
        url=url,
        page_content=page_content,
    )
    try:
        response = llm.complete(prompt, system=_CERT_SYSTEM_PROMPT)
        raw_standards, raw_certs = _parse_cert_response(response)
    except Exception as exc:
        log.warning("LLM cert extraction failed for %s: %s", url, exc)
        return []

    domain = _domain_of(url)
    results: list[CertificationFound] = []

    # Standards – technical specifications, no cert number required
    for std in raw_standards:
        name = (std.get("name") or "").strip()
        if not name:
            continue
        results.append(
            CertificationFound(
                kind="standard",
                standard=name,
                cert_number=None,
                scope=(std.get("scope") or "").strip() or name,
                source_url=url,
                source_name=domain,
            )
        )

    # Certificates – issued compliance documents, must have a number
    for cert in raw_certs:
        number = (cert.get("number") or "").strip()
        related_standard = (cert.get("standard") or "").strip()
        if not number and not related_standard:
            continue
        results.append(
            CertificationFound(
                kind="certificate",
                standard=related_standard or number,
                cert_number=number or None,
                scope=(cert.get("scope") or "").strip() or related_standard,
                source_url=url,
                source_name=domain,
            )
        )

    return results


def _search_component(
    component: EnrichedComponent,
    provider: SearchProvider,
    llm: LLMProvider,
) -> CertificationResult:
    """Run all search queries for *component* and return a ``CertificationResult``.

    Confidence is left as ``NOT_FOUND``; the compile stage assigns the final
    value based on source domains.
    """
    all_certs: list[CertificationFound] = []
    search_log: list[dict[str, Any]] = []

    for i, query in enumerate(component.search_queries):
        if i > 0:
            time.sleep(_INTER_QUERY_DELAY)  # respect search API rate limits

        log_entry: dict[str, Any] = {
            "query": query,
            "provider": type(provider).__name__,
            "results_fetched": 0,
            "certs_found": 0,
            "summary": "",
        }

        try:
            results = provider.query(query)
            log_entry["results_fetched"] = len(results)

            query_certs: list[CertificationFound] = []
            for result in results[:_TOP_RESULTS]:
                url = result.get("url", "")
                if not url:
                    continue
                page_content = _fetch_page(url)
                if page_content:
                    certs = _extract_certs_from_page(component, url, page_content, llm)
                    query_certs.extend(certs)

            all_certs.extend(query_certs)
            log_entry["certs_found"] = len(query_certs)
            if query_certs:
                std_names = sorted({c.standard for c in query_certs if c.kind == "standard"})
                cert_nums = sorted(
                    {c.cert_number for c in query_certs
                     if c.kind == "certificate" and c.cert_number}
                )
                parts: list[str] = []
                if std_names:
                    parts.append("Standards: " + ", ".join(std_names))
                if cert_nums:
                    parts.append("Certificates: " + ", ".join(cert_nums))
                log_entry["summary"] = "Found: " + " | ".join(parts) if parts else f"Found {len(query_certs)} item(s)"
            else:
                log_entry["summary"] = "No certifications found"

        except Exception as exc:
            log.error(
                "Search query %r failed for %r: %s",
                query,
                component.name,
                exc,
            )
            log_entry["summary"] = f"Error: {exc}"

        search_log.append(log_entry)

    return CertificationResult(
        enriched_component=component,
        certifications=all_certs,
        confidence=Confidence.NOT_FOUND,  # compile stage sets final confidence
        search_log=search_log,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_certifications(
    enriched_components: list[EnrichedComponent],
    provider: SearchProvider | None = None,
    llm: LLMProvider | None = None,
) -> list[CertificationResult]:
    """Search the web for certifications for each :class:`~pipeline.models.EnrichedComponent`.

    Parameters
    ----------
    enriched_components:
        Components enriched by :mod:`pipeline.analyse`.
    provider:
        Search provider to use.  Defaults to :func:`get_default_search_provider`
        which reads ``SEARCH_PROVIDER`` and ``SEARCH_API_KEY`` from the environment.
    llm:
        LLM provider for extracting certifications from fetched page content.
        Defaults to :func:`~pipeline.llm.get_default_provider`.

    Returns
    -------
    list[CertificationResult]
        One result entry per input component, in the same order.
        Each result includes a ``search_log`` documenting every query run.
        Never raises — search failures are logged and the component is marked
        ``NOT_FOUND`` so the pipeline can always continue.
    """
    if not enriched_components:
        return []

    if provider is None:
        provider = get_default_search_provider()
    if llm is None:
        llm = get_default_provider()

    results: list[CertificationResult] = []
    total = len(enriched_components)

    for i, component in enumerate(enriched_components, start=1):
        log.info(
            "Searching for component %d/%d: %r",
            i,
            total,
            component.name,
        )
        try:
            result = _search_component(component, provider, llm)
        except Exception as exc:
            log.error(
                "Unexpected error searching for %r: %s – recording as not_found",
                component.name,
                exc,
            )
            result = CertificationResult(
                enriched_component=component,
                certifications=[],
                confidence=Confidence.NOT_FOUND,
                search_log=[
                    {
                        "query": "(all queries)",
                        "provider": type(provider).__name__,
                        "results_fetched": 0,
                        "certs_found": 0,
                        "summary": f"Unexpected error: {exc}",
                    }
                ],
            )
        results.append(result)

    log.info("Search complete: %d/%d components processed", len(results), total)
    return results
