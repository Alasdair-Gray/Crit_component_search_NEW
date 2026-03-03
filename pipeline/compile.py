"""
Stage 4 – Compilation & Aggregation
======================================
Aggregates raw search results into a clean, deduplicated, structured output
ready for report generation.

Responsibilities
----------------
- Accept a list of ``CertificationResult`` objects and pipeline metadata.
- Deduplicate certifications found via multiple queries for the same component.
- Assign a final ``Confidence`` level to each result.
- Group results by assembly.
- Build the list of components needing manual review (no certs found,
  manufacturer uncertain, etc.).
- Return a ``PipelineOutput`` object.

API contract
------------
Input  : ``list[CertificationResult]``, ``project_name: str``, ``source_document: str``
Output : ``PipelineOutput``

Example usage
-------------
::

    from pipeline.compile import compile_results

    output = compile_results(
        results=cert_results,
        project_name="Acme Widget v2",
        source_document="acme_widget_v2_spec.docx",
    )
"""

from __future__ import annotations

from pipeline.models import CertificationResult, PipelineOutput


def compile_results(
    results: list[CertificationResult],
    project_name: str,
    source_document: str,
) -> PipelineOutput:
    """Aggregate and structure *results* into a :class:`~pipeline.models.PipelineOutput`.

    Parameters
    ----------
    results:
        Raw certification results from :mod:`pipeline.search`.
    project_name:
        Human-readable name for the project/product being checked.
    source_document:
        Original filename of the uploaded ``.docx`` (for traceability).

    Returns
    -------
    PipelineOutput
        Fully structured output, grouped by assembly, ready for
        :mod:`pipeline.output`.
    """
    raise NotImplementedError("Stage 4 (compile) is not yet implemented.")
