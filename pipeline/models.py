"""
Shared Pydantic data models for the cert-checker pipeline.

These models define the contracts between pipeline stages. Every stage must
consume and produce these types — never raw dicts or HTTP objects. This allows
each stage to be extracted, tested, or replaced independently.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stage 1 → Stage 2
# ---------------------------------------------------------------------------


class Component(BaseModel):
    """A component as extracted directly from the source document."""

    name: str
    assembly: str
    raw_text: str = Field(
        description="Original, unmodified text from the document for traceability"
    )
    part_number: Optional[str] = None
    manufacturer: Optional[str] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Stage 2 → Stage 3
# ---------------------------------------------------------------------------


class EnrichedComponent(Component):
    """A component after LLM normalisation and query generation."""

    confirmed_manufacturer: str
    standardised_part_number: str
    component_type: str = Field(
        description=(
            "Normalised component category, e.g. PSU, connector, cable, "
            "terminal_block, fuse, relay, circuit_breaker, switch, sensor, other"
        )
    )
    search_queries: list[str] = Field(
        description="3–5 search strings ready for submission to the search provider"
    )
    manufacturer_uncertain: bool = Field(
        default=False,
        description="True when the LLM had low confidence identifying the manufacturer",
    )


# ---------------------------------------------------------------------------
# Stage 3 → Stage 4
# ---------------------------------------------------------------------------


class CertificationFound(BaseModel):
    """A single certification discovered during a web search."""

    standard: str = Field(description='E.g. "IEC 60320-1", "UL 508A"')
    scope: str = Field(description="Short description of what the cert covers")
    source_url: str
    source_name: str = Field(description='Human-readable source, e.g. "UL Product iQ"')
    cert_number: Optional[str] = Field(
        default=None,
        description='Official certificate/file number, e.g. "UL E215312"',
    )


# ---------------------------------------------------------------------------
# Stage 4 → Stage 5
# ---------------------------------------------------------------------------


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOT_FOUND = "not_found"


class CertificationResult(BaseModel):
    """Aggregated search results for a single enriched component."""

    enriched_component: EnrichedComponent
    certifications: list[CertificationFound] = Field(default_factory=list)
    confidence: Confidence = Confidence.NOT_FOUND
    search_log: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Ordered log of each search performed: query, provider, result count, "
            "and a brief summary of what was found"
        ),
    )


# ---------------------------------------------------------------------------
# Final pipeline output
# ---------------------------------------------------------------------------


class PipelineOutput(BaseModel):
    """Complete output of the cert-checker pipeline for one source document."""

    project_name: str
    source_document: str = Field(description="Original filename of the uploaded .docx")
    results_by_assembly: dict[str, list[CertificationResult]] = Field(
        description="Assembly name → list of certification results for that assembly"
    )
    components_needing_review: list[str] = Field(
        default_factory=list,
        description=(
            "Component names flagged for manual review "
            "(manufacturer uncertain, no certs found, etc.)"
        ),
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)
