"""
Unit tests for pipeline/compile.py.

No real API calls or LLM calls are made.
"""

from __future__ import annotations

import pytest

from pipeline.models import (
    CertificationFound,
    CertificationResult,
    Confidence,
    EnrichedComponent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_enriched(
    name: str = "Mean Well HDR-100-12",
    assembly: str = "Main Assembly",
    manufacturer_uncertain: bool = False,
) -> EnrichedComponent:
    return EnrichedComponent(
        name=name,
        assembly=assembly,
        raw_text=name,
        part_number="HDR-100-12",
        manufacturer="Mean Well",
        confirmed_manufacturer="Mean Well",
        standardised_part_number="HDR-100-12",
        component_type="PSU",
        search_queries=["q1", "q2", "q3"],
        manufacturer_uncertain=manufacturer_uncertain,
    )


def _make_cert(
    standard: str = "UL 508",
    source_url: str = "https://ul.com/listing/E171376",
    cert_number: str | None = "E171376",
    scope: str = "Power supply unit",
) -> CertificationFound:
    from urllib.parse import urlparse

    domain = urlparse(source_url).netloc.lstrip("www.")
    return CertificationFound(
        standard=standard,
        cert_number=cert_number,
        scope=scope,
        source_url=source_url,
        source_name=domain,
    )


def _make_result(
    component: EnrichedComponent | None = None,
    certifications: list[CertificationFound] | None = None,
) -> CertificationResult:
    return CertificationResult(
        enriched_component=component or _make_enriched(),
        certifications=certifications or [],
        confidence=Confidence.NOT_FOUND,
        search_log=[],
    )


# ---------------------------------------------------------------------------
# compile_results – basic contract
# ---------------------------------------------------------------------------


class TestCompileResultsContract:
    def test_empty_results_returns_empty_output(self):
        """An empty results list produces a PipelineOutput with no assemblies."""
        from pipeline.compile import compile_results

        output = compile_results([], project_name="Test", source_document="test.docx")

        assert output.project_name == "Test"
        assert output.source_document == "test.docx"
        assert output.results_by_assembly == {}
        assert output.components_needing_review == []

    def test_project_name_and_source_document_preserved(self):
        """project_name and source_document are passed through unchanged."""
        from pipeline.compile import compile_results

        output = compile_results(
            [_make_result()],
            project_name="My Product v2",
            source_document="spec_v2.docx",
        )

        assert output.project_name == "My Product v2"
        assert output.source_document == "spec_v2.docx"

    def test_generated_at_is_set(self):
        """generated_at is populated automatically."""
        from pipeline.compile import compile_results
        from datetime import datetime

        output = compile_results([], project_name="P", source_document="s.docx")

        assert isinstance(output.generated_at, datetime)


# ---------------------------------------------------------------------------
# Grouping by assembly
# ---------------------------------------------------------------------------


class TestGroupingByAssembly:
    def test_single_assembly(self):
        """All components in one assembly appear in a single group."""
        from pipeline.compile import compile_results

        components = [
            _make_enriched(f"Component {i}", assembly="Power Board")
            for i in range(3)
        ]
        results = [_make_result(c) for c in components]

        output = compile_results(results, project_name="P", source_document="s.docx")

        assert list(output.results_by_assembly.keys()) == ["Power Board"]
        assert len(output.results_by_assembly["Power Board"]) == 3

    def test_multiple_assemblies_grouped_correctly(self):
        """Components from different assemblies are placed in separate groups."""
        from pipeline.compile import compile_results

        results = [
            _make_result(_make_enriched("A1", assembly="Power Board")),
            _make_result(_make_enriched("A2", assembly="Power Board")),
            _make_result(_make_enriched("B1", assembly="Control Panel")),
        ]

        output = compile_results(results, project_name="P", source_document="s.docx")

        assert set(output.results_by_assembly.keys()) == {"Power Board", "Control Panel"}
        assert len(output.results_by_assembly["Power Board"]) == 2
        assert len(output.results_by_assembly["Control Panel"]) == 1

    def test_assembly_order_preserved(self):
        """Components within an assembly appear in input order."""
        from pipeline.compile import compile_results

        names = ["Alpha", "Beta", "Gamma"]
        results = [_make_result(_make_enriched(n, assembly="Board A")) for n in names]

        output = compile_results(results, project_name="P", source_document="s.docx")

        result_names = [
            r.enriched_component.name for r in output.results_by_assembly["Board A"]
        ]
        assert result_names == names


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_duplicate_standards_are_collapsed(self):
        """Two certifications for the same standard are reduced to one."""
        from pipeline.compile import compile_results

        certs = [
            _make_cert("UL 508", "https://ul.com/a", cert_number=None),
            _make_cert("UL 508", "https://ul.com/b", cert_number=None),
        ]
        result = _make_result(certifications=certs)

        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert len(compiled.certifications) == 1

    def test_dedup_prefers_entry_with_cert_number(self):
        """When deduplicating, the entry with a cert_number is kept."""
        from pipeline.compile import compile_results

        certs = [
            _make_cert("UL 508", cert_number=None),       # no number
            _make_cert("UL 508", cert_number="E171376"),  # has number
        ]
        result = _make_result(certifications=certs)

        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.certifications[0].cert_number == "E171376"

    def test_different_standards_are_not_deduplicated(self):
        """Certifications for distinct standards are all kept."""
        from pipeline.compile import compile_results

        certs = [
            _make_cert("UL 508", "https://ul.com/a"),
            _make_cert("IEC 60320-1", "https://schurter.com/b"),
            _make_cert("CE", "https://meanwell.com/c"),
        ]
        result = _make_result(certifications=certs)

        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert len(compiled.certifications) == 3

    def test_case_insensitive_deduplication(self):
        """Standards differing only in case are treated as duplicates."""
        from pipeline.compile import compile_results

        certs = [
            _make_cert("ul 508", "https://ul.com/a"),
            _make_cert("UL 508", "https://ul.com/b"),
        ]
        result = _make_result(certifications=certs)

        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert len(compiled.certifications) == 1


# ---------------------------------------------------------------------------
# Confidence assignment
# ---------------------------------------------------------------------------


class TestConfidenceAssignment:
    def test_high_confidence_for_manufacturer_domain(self):
        """Certification from a manufacturer domain gets HIGH confidence."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[_make_cert("UL 508", "https://ul.com/listing")]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.HIGH

    def test_high_confidence_for_subdomain_of_manufacturer(self):
        """A subdomain of a known high-confidence domain also gets HIGH confidence."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[_make_cert("UL 508", "https://certifications.ul.com/E171376")]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.HIGH

    def test_medium_confidence_for_distributor_domain(self):
        """Certification from a distributor domain gets MEDIUM confidence."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[_make_cert("UL 508", "https://mouser.com/datasheet", cert_number=None)]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.MEDIUM

    def test_low_confidence_for_unknown_domain(self):
        """Certification from an unrecognised domain gets LOW confidence."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[
                _make_cert("UL 508", "https://random-blog.example.com/post", cert_number=None)
            ]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.LOW

    def test_not_found_when_no_certifications(self):
        """A component with no certifications is assigned NOT_FOUND confidence."""
        from pipeline.compile import compile_results

        result = _make_result(certifications=[])
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.NOT_FOUND

    def test_high_confidence_wins_over_medium(self):
        """When certs come from both high and medium domains, HIGH confidence wins."""
        from pipeline.compile import compile_results

        certs = [
            _make_cert("UL 508", "https://mouser.com/datasheet", cert_number=None),
            _make_cert("IEC 60320-1", "https://schurter.com/product"),
        ]
        result = _make_result(certifications=certs)
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.HIGH

    def test_www_prefix_stripped_before_domain_lookup(self):
        """www.mouser.com is treated the same as mouser.com."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[
                _make_cert("UL 508", "https://www.mouser.com/datasheet", cert_number=None)
            ]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        compiled = output.results_by_assembly["Main Assembly"][0]
        assert compiled.confidence == Confidence.MEDIUM


# ---------------------------------------------------------------------------
# Manual review flagging
# ---------------------------------------------------------------------------


class TestManualReviewFlagging:
    def test_not_found_component_flagged_for_review(self):
        """A component with no certifications is added to components_needing_review."""
        from pipeline.compile import compile_results

        result = _make_result(_make_enriched("Fuse 5A"), certifications=[])
        output = compile_results([result], project_name="P", source_document="s.docx")

        assert any("Fuse 5A" in entry for entry in output.components_needing_review)

    def test_uncertain_manufacturer_flagged_for_review(self):
        """A component with manufacturer_uncertain=True is always flagged."""
        from pipeline.compile import compile_results

        component = _make_enriched("Unknown Part", manufacturer_uncertain=True)
        # Even with a high-confidence cert, manufacturer uncertainty still flags
        result = _make_result(
            component,
            certifications=[_make_cert("UL 508", "https://ul.com/E171376")],
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        assert any("Unknown Part" in entry for entry in output.components_needing_review)

    def test_uncertain_manufacturer_gets_question_mark(self):
        """The review entry for an uncertain manufacturer includes the '?' marker."""
        from pipeline.compile import compile_results

        component = _make_enriched("Mystery Component", manufacturer_uncertain=True)
        result = _make_result(component)
        output = compile_results([result], project_name="P", source_document="s.docx")

        matching = [e for e in output.components_needing_review if "Mystery Component" in e]
        assert matching
        assert "?" in matching[0]

    def test_low_confidence_flagged_for_review(self):
        """A component with LOW confidence certifications is flagged for review."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[
                _make_cert("UL 508", "https://random-blog.example.com/post", cert_number=None)
            ]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        assert len(output.components_needing_review) == 1

    def test_high_confidence_not_flagged_for_review(self):
        """A component with HIGH confidence is not added to the review list."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[_make_cert("UL 508", "https://ul.com/listing")]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        assert output.components_needing_review == []

    def test_medium_confidence_not_flagged_for_review(self):
        """A component with MEDIUM confidence is not added to the review list."""
        from pipeline.compile import compile_results

        result = _make_result(
            certifications=[
                _make_cert("UL 508", "https://mouser.com/datasheet", cert_number=None)
            ]
        )
        output = compile_results([result], project_name="P", source_document="s.docx")

        assert output.components_needing_review == []

    def test_multiple_components_only_problematic_ones_flagged(self):
        """Only components with issues appear in the review list."""
        from pipeline.compile import compile_results

        results = [
            _make_result(
                _make_enriched("Good Part", assembly="Board A"),
                certifications=[_make_cert("UL 508", "https://ul.com/listing")],
            ),
            _make_result(
                _make_enriched("Bad Part", assembly="Board A"),
                certifications=[],
            ),
        ]
        output = compile_results(results, project_name="P", source_document="s.docx")

        assert len(output.components_needing_review) == 1
        assert "Bad Part" in output.components_needing_review[0]
