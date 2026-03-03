"""
Unit tests for pipeline/analyse.py.

The LLM is always mocked so no real API calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call

import pytest

from pipeline.models import Component


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_component(
    name: str,
    assembly: str = "Main Assembly",
    part_number: str | None = None,
    manufacturer: str | None = None,
    description: str | None = None,
) -> Component:
    return Component(
        name=name,
        assembly=assembly,
        raw_text=name,
        part_number=part_number,
        manufacturer=manufacturer,
        description=description,
    )


def _mock_llm(enriched_payload: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.complete.return_value = json.dumps({"enriched": enriched_payload})
    return mock


def _std_entry(
    mfr: str = "Mean Well",
    part: str = "HDR-100-12",
    ctype: str = "PSU",
    queries: list[str] | None = None,
    uncertain: bool = False,
) -> dict:
    return {
        "confirmed_manufacturer": mfr,
        "standardised_part_number": part,
        "component_type": ctype,
        "search_queries": queries
        or [
            f"{mfr} {part} UL listing",
            f"{part} safety certifications",
            f"{mfr} {part} CE declaration",
        ],
        "manufacturer_uncertain": uncertain,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnrichComponents:
    def test_empty_list_returns_empty(self):
        """Empty input returns [] immediately without calling the LLM."""
        from pipeline.analyse import enrich_components

        llm = MagicMock()
        result = enrich_components([], llm=llm)

        assert result == []
        llm.complete.assert_not_called()

    def test_single_component_parsed_correctly(self):
        """A single component is enriched with all expected fields."""
        from pipeline.analyse import enrich_components

        component = _make_component(
            "Mean Well HDR-100-12",
            part_number="HDR-100-12",
            manufacturer="Mean Well",
        )
        llm = _mock_llm(
            [
                _std_entry(
                    mfr="Mean Well",
                    part="HDR-100-12",
                    ctype="PSU",
                    queries=[
                        "Mean Well HDR-100-12 UL listing",
                        "HDR-100-12 safety certifications",
                        "Mean Well HDR-100-12 CE declaration",
                    ],
                )
            ]
        )

        result = enrich_components([component], llm=llm)

        assert len(result) == 1
        ec = result[0]
        assert ec.confirmed_manufacturer == "Mean Well"
        assert ec.standardised_part_number == "HDR-100-12"
        assert ec.component_type == "PSU"
        assert len(ec.search_queries) == 3
        assert ec.manufacturer_uncertain is False
        # Component fields pass through unchanged
        assert ec.name == "Mean Well HDR-100-12"
        assert ec.assembly == "Main Assembly"
        assert ec.raw_text == "Mean Well HDR-100-12"

    def test_manufacturer_uncertain_flag_preserved(self):
        """manufacturer_uncertain=True from LLM is kept on the output."""
        from pipeline.analyse import enrich_components

        component = _make_component("E254552 AWM 1015")
        llm = _mock_llm(
            [
                {
                    "confirmed_manufacturer": "Unknown",
                    "standardised_part_number": "AWM 1015",
                    "component_type": "cable",
                    "search_queries": [
                        "E254552 UL wire listing",
                        "AWM 1015 UL approval",
                        "UL style 1015 recognised component",
                    ],
                    "manufacturer_uncertain": True,
                }
            ]
        )

        result = enrich_components([component], llm=llm)

        assert result[0].manufacturer_uncertain is True

    def test_unknown_component_type_normalised_to_other(self):
        """A component_type not in VALID_COMPONENT_TYPES becomes 'other'."""
        from pipeline.analyse import enrich_components

        component = _make_component("Widget X")
        llm = _mock_llm(
            [
                {
                    "confirmed_manufacturer": "Acme",
                    "standardised_part_number": "X-1",
                    "component_type": "gizmo",  # not valid
                    "search_queries": ["Acme X-1 cert", "X-1 approval", "Acme X-1 safety"],
                    "manufacturer_uncertain": False,
                }
            ]
        )

        result = enrich_components([component], llm=llm)

        assert result[0].component_type == "other"

    def test_malformed_json_falls_back_to_defaults(self):
        """If the LLM returns garbage, every component gets fallback enrichment."""
        from pipeline.analyse import enrich_components

        components = [
            _make_component("Schurter 6100-42", part_number="6100-42", manufacturer="Schurter"),
            _make_component("WAGO 2002-1401", part_number="2002-1401", manufacturer="WAGO"),
        ]
        llm = MagicMock()
        llm.complete.return_value = "I cannot process this request."

        result = enrich_components(components, llm=llm)

        assert len(result) == 2
        for ec in result:
            assert ec.manufacturer_uncertain is True
            assert len(ec.search_queries) >= 3
        assert result[0].confirmed_manufacturer == "Schurter"
        assert result[1].confirmed_manufacturer == "WAGO"

    def test_llm_exception_falls_back_to_defaults(self):
        """A hard LLM exception triggers fallback; enrichment never raises."""
        from pipeline.analyse import enrich_components

        components = [_make_component("H07V2-K BASEC")]
        llm = MagicMock()
        llm.complete.side_effect = RuntimeError("API unavailable")

        result = enrich_components(components, llm=llm)

        assert len(result) == 1
        ec = result[0]
        assert ec.manufacturer_uncertain is True
        assert ec.component_type == "other"
        assert "H07V2-K BASEC" in ec.search_queries[0]

    def test_wrong_entry_count_falls_back_to_defaults(self):
        """If the LLM returns fewer entries than requested, fall back for the batch."""
        from pipeline.analyse import enrich_components

        components = [
            _make_component("Component A"),
            _make_component("Component B"),
        ]
        # Only 1 entry returned for 2 components
        llm = _mock_llm([_std_entry(mfr="Acme", part="A-1", ctype="other")])

        result = enrich_components(components, llm=llm)

        assert len(result) == 2
        for ec in result:
            assert ec.manufacturer_uncertain is True

    def test_batching_splits_into_multiple_llm_calls(self):
        """With batch_size=2, 5 components require 3 LLM calls."""
        from pipeline.analyse import enrich_components

        components = [_make_component(f"Component {i}") for i in range(5)]

        def _side_effect(prompt: str, system: str = "") -> str:
            # Count how many entries the prompt asks for
            count = prompt.count('"name"')
            entries = [
                {
                    "confirmed_manufacturer": "Mfr",
                    "standardised_part_number": f"PART-{i}",
                    "component_type": "other",
                    "search_queries": ["q1", "q2", "q3"],
                    "manufacturer_uncertain": False,
                }
                for i in range(count)
            ]
            return json.dumps({"enriched": entries})

        llm = MagicMock()
        llm.complete.side_effect = _side_effect

        result = enrich_components(components, llm=llm, batch_size=2)

        assert len(result) == 5
        assert llm.complete.call_count == 3  # batches of 2, 2, 1

    def test_queries_supplemented_when_too_few_returned(self):
        """If the LLM returns fewer than 3 queries, fallback queries pad to at least 3."""
        from pipeline.analyse import enrich_components

        component = _make_component(
            "Schurter 6100-42",
            part_number="6100-42",
            manufacturer="Schurter",
        )
        llm = _mock_llm(
            [
                {
                    "confirmed_manufacturer": "Schurter",
                    "standardised_part_number": "6100-42",
                    "component_type": "connector",
                    "search_queries": ["Schurter 6100-42 UL approval"],  # only 1
                    "manufacturer_uncertain": False,
                }
            ]
        )

        result = enrich_components([component], llm=llm)

        assert len(result[0].search_queries) >= 3

    def test_component_fields_pass_through_unchanged(self):
        """All original Component fields are preserved on EnrichedComponent."""
        from pipeline.analyse import enrich_components

        component = Component(
            name="Fuse 5A",
            assembly="Protection Board",
            raw_text="5A glass fuse",
            part_number="F5A",
            manufacturer="Littelfuse",
            description="5 amp glass fuse",
        )
        llm = _mock_llm(
            [
                {
                    "confirmed_manufacturer": "Littelfuse",
                    "standardised_part_number": "F5A",
                    "component_type": "fuse",
                    "search_queries": [
                        "Littelfuse F5A UL248 approval",
                        "F5A IEC 60269 certification",
                        "Littelfuse 5A fuse safety",
                    ],
                    "manufacturer_uncertain": False,
                }
            ]
        )

        result = enrich_components([component], llm=llm)

        ec = result[0]
        assert ec.assembly == "Protection Board"
        assert ec.raw_text == "5A glass fuse"
        assert ec.part_number == "F5A"
        assert ec.manufacturer == "Littelfuse"
        assert ec.description == "5 amp glass fuse"

    def test_null_manufacturer_in_llm_response_falls_back_to_component_manufacturer(self):
        """A null confirmed_manufacturer uses the Component's manufacturer field."""
        from pipeline.analyse import enrich_components

        component = _make_component("IEC Inlet", manufacturer="Schurter", part_number="6100-42")
        llm = _mock_llm(
            [
                {
                    "confirmed_manufacturer": None,  # LLM returned null
                    "standardised_part_number": "6100-42",
                    "component_type": "connector",
                    "search_queries": ["Schurter 6100-42 cert", "6100-42 IEC 60320", "Schurter inlet UL"],
                    "manufacturer_uncertain": False,
                }
            ]
        )

        result = enrich_components([component], llm=llm)

        assert result[0].confirmed_manufacturer == "Schurter"

    def test_no_part_number_uses_component_name_as_fallback(self):
        """When part_number is None, standardised_part_number falls back to name."""
        from pipeline.analyse import enrich_components

        component = _make_component("H07V2-K BASEC")
        llm = _mock_llm(
            [
                {
                    "confirmed_manufacturer": "Unknown",
                    "standardised_part_number": None,  # LLM returned null
                    "component_type": "cable",
                    "search_queries": ["H07V2-K BASEC approval", "H07V2-K certificate", "H07V2-K HAR"],
                    "manufacturer_uncertain": True,
                }
            ]
        )

        result = enrich_components([component], llm=llm)

        assert result[0].standardised_part_number == "H07V2-K BASEC"
