"""
Unit tests for pipeline/search.py.

All external I/O (search API calls, HTTP page fetches, LLM calls) is mocked
so no real network traffic is generated.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from pipeline.models import Component, EnrichedComponent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_enriched(
    name: str = "Mean Well HDR-100-12",
    assembly: str = "Main Assembly",
    part_number: str = "HDR-100-12",
    manufacturer: str = "Mean Well",
    manufacturer_uncertain: bool = False,
    search_queries: list[str] | None = None,
) -> EnrichedComponent:
    return EnrichedComponent(
        name=name,
        assembly=assembly,
        raw_text=name,
        part_number=part_number,
        manufacturer=manufacturer,
        confirmed_manufacturer=manufacturer,
        standardised_part_number=part_number,
        component_type="PSU",
        search_queries=search_queries or [
            f"{manufacturer} {part_number} UL listing",
            f"{part_number} safety certifications datasheet",
            f"{manufacturer} {part_number} CE declaration",
        ],
        manufacturer_uncertain=manufacturer_uncertain,
    )


def _mock_llm(cert_payload: list[dict] | None = None) -> MagicMock:
    """Return a mock LLM that returns *cert_payload* as a JSON certifications list."""
    mock = MagicMock()
    payload = cert_payload if cert_payload is not None else []
    mock.complete.return_value = json.dumps({"certifications": payload})
    return mock


def _mock_provider(results: list[dict] | None = None) -> MagicMock:
    """Return a mock SearchProvider that yields *results* for any query."""
    mock = MagicMock()
    mock.query.return_value = results or []
    return mock


# ---------------------------------------------------------------------------
# search_certifications – top-level behaviour
# ---------------------------------------------------------------------------


class TestSearchCertificationsTopLevel:
    def test_empty_list_returns_empty(self):
        """Empty input returns [] immediately without touching provider or LLM."""
        from pipeline.search import search_certifications

        provider = _mock_provider()
        llm = _mock_llm()

        result = search_certifications([], provider=provider, llm=llm)

        assert result == []
        provider.query.assert_not_called()
        llm.complete.assert_not_called()

    def test_result_count_matches_input(self):
        """One CertificationResult is returned per input component."""
        from pipeline.search import search_certifications

        components = [_make_enriched(f"Component {i}") for i in range(3)]
        provider = _mock_provider()
        llm = _mock_llm()

        results = search_certifications(components, provider=provider, llm=llm)

        assert len(results) == 3

    def test_result_order_matches_input(self):
        """Results are returned in the same order as the input components."""
        from pipeline.search import search_certifications

        names = ["Alpha", "Beta", "Gamma"]
        components = [_make_enriched(n) for n in names]
        provider = _mock_provider()
        llm = _mock_llm()

        results = search_certifications(components, provider=provider, llm=llm)

        assert [r.enriched_component.name for r in results] == names

    def test_provider_queried_once_per_search_query(self):
        """The provider is called once for each search query in the component."""
        from pipeline.search import search_certifications

        queries = ["q1", "q2", "q3"]
        component = _make_enriched(search_queries=queries)
        provider = _mock_provider()
        llm = _mock_llm()

        with patch("pipeline.search.time.sleep"):  # skip rate-limit delay
            search_certifications([component], provider=provider, llm=llm)

        assert provider.query.call_count == 3
        provider.query.assert_any_call("q1")
        provider.query.assert_any_call("q2")
        provider.query.assert_any_call("q3")


# ---------------------------------------------------------------------------
# Certification extraction from search results
# ---------------------------------------------------------------------------


class TestCertificationExtraction:
    def test_certs_extracted_from_page_content(self):
        """When the LLM finds certs on a page they appear in the result."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["HDR-100-12 UL listing"])
        provider = _mock_provider([{"url": "https://meanwell.com/HDR-100-12"}])
        llm = _mock_llm(
            [
                {
                    "standard": "UL 508",
                    "cert_number": "UL E171376",
                    "scope": "Power supply unit for industrial use",
                }
            ]
        )

        with patch("pipeline.search._fetch_page", return_value="<html>UL listed</html>"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert len(results[0].certifications) == 1
        cert = results[0].certifications[0]
        assert cert.standard == "UL 508"
        assert cert.cert_number == "UL E171376"
        assert cert.source_url == "https://meanwell.com/HDR-100-12"

    def test_certifications_from_multiple_pages_are_aggregated(self):
        """Certs found on different result pages for one query are all collected."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["HDR-100-12 certifications"])
        urls = [
            {"url": "https://meanwell.com/HDR-100-12"},
            {"url": "https://ul.com/listing/E171376"},
        ]
        provider = _mock_provider(urls)

        def llm_side_effect(prompt: str, system: str = "") -> str:
            if "meanwell.com" in prompt:
                return json.dumps(
                    {"certifications": [{"standard": "CE", "cert_number": None, "scope": "EU directive"}]}
                )
            return json.dumps(
                {"certifications": [{"standard": "UL 508", "cert_number": "E171376", "scope": "Power supply"}]}
            )

        llm = MagicMock()
        llm.complete.side_effect = llm_side_effect

        with patch("pipeline.search._fetch_page", return_value="page content"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert len(results[0].certifications) == 2

    def test_empty_search_results_produces_not_found(self):
        """When the provider returns no URLs the result has no certifications."""
        from pipeline.search import search_certifications
        from pipeline.models import Confidence

        component = _make_enriched(search_queries=["obscure part XYZ"])
        provider = _mock_provider([])  # no results
        llm = _mock_llm()

        results = search_certifications([component], provider=provider, llm=llm)

        assert results[0].certifications == []
        assert results[0].confidence == Confidence.NOT_FOUND

    def test_cert_with_null_number_is_included(self):
        """A certification without a cert_number is still recorded."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["CE declaration"])
        provider = _mock_provider([{"url": "https://meanwell.com/ce"}])
        llm = _mock_llm(
            [{"standard": "CE", "cert_number": None, "scope": "EU low voltage directive"}]
        )

        with patch("pipeline.search._fetch_page", return_value="CE content"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert len(results[0].certifications) == 1
        assert results[0].certifications[0].cert_number is None


# ---------------------------------------------------------------------------
# Search log
# ---------------------------------------------------------------------------


class TestSearchLog:
    def test_search_log_entry_per_query(self):
        """One log entry is appended per search query executed."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["q1", "q2"])
        provider = _mock_provider()
        llm = _mock_llm()

        with patch("pipeline.search.time.sleep"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert len(results[0].search_log) == 2

    def test_search_log_records_query_text(self):
        """Each log entry records the query that was run."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["unique query string"])
        provider = _mock_provider()
        llm = _mock_llm()

        results = search_certifications([component], provider=provider, llm=llm)

        assert results[0].search_log[0]["query"] == "unique query string"

    def test_search_log_records_certs_found_count(self):
        """The log entry records how many certifications were found for the query."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["HDR-100-12 cert"])
        provider = _mock_provider([{"url": "https://meanwell.com/page"}])
        llm = _mock_llm(
            [
                {"standard": "UL 508", "cert_number": "E171376", "scope": "PSU"},
                {"standard": "CE", "cert_number": None, "scope": "EU directive"},
            ]
        )

        with patch("pipeline.search._fetch_page", return_value="content"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert results[0].search_log[0]["certs_found"] == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_provider_exception_does_not_crash_pipeline(self):
        """If the search provider throws, the component gets NOT_FOUND and pipeline continues."""
        from pipeline.search import search_certifications
        from pipeline.models import Confidence

        component = _make_enriched(search_queries=["q1"])
        provider = MagicMock()
        provider.query.side_effect = RuntimeError("API limit exceeded")
        llm = _mock_llm()

        results = search_certifications([component], provider=provider, llm=llm)

        assert len(results) == 1
        assert results[0].certifications == []
        assert results[0].confidence == Confidence.NOT_FOUND

    def test_page_fetch_failure_does_not_crash_pipeline(self):
        """If fetching a page fails, that URL is skipped and the pipeline continues."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["q1"])
        provider = _mock_provider([{"url": "https://example.com/page"}])
        llm = _mock_llm()

        with patch("pipeline.search._fetch_page", return_value=None):
            results = search_certifications([component], provider=provider, llm=llm)

        # LLM should not be called for pages that failed to load
        llm.complete.assert_not_called()
        assert results[0].certifications == []

    def test_llm_extraction_exception_does_not_crash_pipeline(self):
        """If the LLM raises during extraction, that page is skipped gracefully."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["q1"])
        provider = _mock_provider([{"url": "https://example.com/page"}])
        llm = MagicMock()
        llm.complete.side_effect = RuntimeError("LLM unavailable")

        with patch("pipeline.search._fetch_page", return_value="page content"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert results[0].certifications == []

    def test_malformed_llm_json_does_not_crash_pipeline(self):
        """Garbage JSON from the LLM is handled gracefully."""
        from pipeline.search import search_certifications

        component = _make_enriched(search_queries=["q1"])
        provider = _mock_provider([{"url": "https://example.com/page"}])
        llm = MagicMock()
        llm.complete.return_value = "I cannot process this request."

        with patch("pipeline.search._fetch_page", return_value="page content"):
            results = search_certifications([component], provider=provider, llm=llm)

        assert results[0].certifications == []

    def test_multiple_components_one_fails_rest_succeed(self):
        """A failure on one component does not prevent others from being processed."""
        from pipeline.search import search_certifications

        components = [_make_enriched(f"Component {i}") for i in range(3)]

        call_count = 0

        def provider_side_effect(query: str) -> list[dict]:
            nonlocal call_count
            call_count += 1
            # Fail only the first component's queries
            if call_count <= len(components[0].search_queries):
                raise RuntimeError("Transient error")
            return []

        provider = MagicMock()
        provider.query.side_effect = provider_side_effect
        llm = _mock_llm()

        with patch("pipeline.search.time.sleep"):
            results = search_certifications(components, provider=provider, llm=llm)

        assert len(results) == 3


# ---------------------------------------------------------------------------
# Page fetch retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    def test_successful_fetch_on_second_attempt(self):
        """_fetch_page retries after the first failure and returns content on success."""
        from pipeline.search import _fetch_page

        mock_response = MagicMock()
        mock_response.text = "page content"
        mock_response.raise_for_status.return_value = None

        with patch("pipeline.search.requests.get") as mock_get, \
             patch("pipeline.search.time.sleep"):
            mock_get.side_effect = [ConnectionError("timeout"), mock_response]
            result = _fetch_page("https://example.com")

        assert result == "page content"
        assert mock_get.call_count == 2

    def test_returns_none_after_all_retries_exhausted(self):
        """_fetch_page returns None once all retry attempts are used up."""
        from pipeline.search import _fetch_page, _MAX_RETRIES

        with patch("pipeline.search.requests.get", side_effect=ConnectionError("down")), \
             patch("pipeline.search.time.sleep"):
            result = _fetch_page("https://example.com")

        assert result is None

    def test_retry_count_is_correct(self):
        """_fetch_page makes exactly 1 + _MAX_RETRIES attempts total."""
        from pipeline.search import _fetch_page, _MAX_RETRIES

        with patch("pipeline.search.requests.get", side_effect=ConnectionError("down")) as mock_get, \
             patch("pipeline.search.time.sleep"):
            _fetch_page("https://example.com")

        assert mock_get.call_count == _MAX_RETRIES + 1

    def test_page_content_truncated_to_max_chars(self):
        """Page content longer than _MAX_PAGE_CHARS is truncated."""
        from pipeline.search import _fetch_page, _MAX_PAGE_CHARS

        long_content = "x" * (_MAX_PAGE_CHARS + 1000)
        mock_response = MagicMock()
        mock_response.text = long_content
        mock_response.raise_for_status.return_value = None

        with patch("pipeline.search.requests.get", return_value=mock_response):
            result = _fetch_page("https://example.com")

        assert len(result) == _MAX_PAGE_CHARS
