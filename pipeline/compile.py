"""
Stage 4 – Compilation & Aggregation
======================================
Aggregates raw search results into a clean, deduplicated, structured output
ready for report generation.

Responsibilities
----------------
- Accept a list of ``CertificationResult`` objects and pipeline metadata.
- Deduplicate certifications found via multiple queries for the same component,
  preferring entries that include a certificate number.
- Assign a final ``Confidence`` level to each result based on the source
  domain of the certifications found:

  - **high**      – cert found on manufacturer site or official database
    (e.g. UL Product iQ, meanwell.com, basec.org.uk)
  - **medium**    – cert found on distributor or third-party site
    (e.g. Farnell, Mouser, RS Components)
  - **low**       – certifications found but from unrecognised domains
  - **not_found** – no certifications discovered

- Group results by assembly (matching the original document structure).
- Build the list of components needing manual review:
  - manufacturer identification uncertain
  - no certifications found (not_found)
  - low confidence result
- Components with uncertain manufacturer identification are marked with ``?``.
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

import logging
from urllib.parse import urlparse

from pipeline.models import (
    CertificationFound,
    CertificationResult,
    Confidence,
    PipelineOutput,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain confidence tables
# ---------------------------------------------------------------------------

# Sources that warrant HIGH confidence (manufacturer sites and official databases)
_HIGH_CONFIDENCE_DOMAINS: frozenset[str] = frozenset(
    {
        "productiq.ulprospector.com",
        "ul.com",
        "database.ul.com",
        "certifications.ul.com",
        "meanwell.com",
        "mean-well.com",
        "schurter.com",
        "wago.com",
        "bulgin.com",
        "phoenixcontact.com",
        "elandcables.com",
        "eland.co.uk",
        "basec.org.uk",
        "vde.com",
        "iec.ch",
        "cenelec.eu",
    }
)

# Sources that warrant MEDIUM confidence (distributors and third-party listings)
_MEDIUM_CONFIDENCE_DOMAINS: frozenset[str] = frozenset(
    {
        "farnell.com",
        "element14.com",
        "newark.com",
        "mouser.com",
        "mouser.co.uk",
        "rscomponents.com",
        "rs-online.com",
        "digikey.com",
        "digikey.co.uk",
        "octopart.com",
        "arrow.com",
    }
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _domain_of(url: str) -> str:
    """Return the bare domain (no ``www.`` prefix) from *url*, lower-cased."""
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _domain_confidence(domain: str) -> Confidence:
    """Return the :class:`Confidence` level implied by *domain* alone."""
    # Check exact match then subdomain match (e.g. certifications.ul.com → ul.com)
    for hd in _HIGH_CONFIDENCE_DOMAINS:
        if domain == hd or domain.endswith("." + hd):
            return Confidence.HIGH
    for md in _MEDIUM_CONFIDENCE_DOMAINS:
        if domain == md or domain.endswith("." + md):
            return Confidence.MEDIUM
    return Confidence.LOW


def _cert_dedup_key(cert: CertificationFound) -> str:
    """Return a normalised deduplication key for *cert*.

    Two certifications are considered the same if they share the same standard
    name (case-insensitive).  Certificate numbers are ignored for deduplication
    so that "UL E12345" and "UL 508A" for the same standard still collapse.
    """
    return cert.standard.strip().lower()


def _deduplicate(certs: list[CertificationFound]) -> list[CertificationFound]:
    """Remove duplicate certifications from *certs*.

    When duplicates exist the entry with a ``cert_number`` is preferred;
    otherwise the first occurrence is kept.
    """
    seen: dict[str, CertificationFound] = {}
    for cert in certs:
        key = _cert_dedup_key(cert)
        existing = seen.get(key)
        if existing is None:
            seen[key] = cert
        elif cert.cert_number and not existing.cert_number:
            # Upgrade to the entry that has a certificate number
            seen[key] = cert
    return list(seen.values())


def _assign_confidence(certifications: list[CertificationFound]) -> Confidence:
    """Determine the best :class:`Confidence` level across *certifications*.

    Iterates all certifications and returns the highest level found.
    """
    if not certifications:
        return Confidence.NOT_FOUND

    best = Confidence.LOW
    for cert in certifications:
        domain = _domain_of(cert.source_url)
        level = _domain_confidence(domain)
        if level == Confidence.HIGH:
            return Confidence.HIGH  # can't do better; short-circuit
        if level == Confidence.MEDIUM:
            best = Confidence.MEDIUM
    return best


def _review_note(result: CertificationResult) -> str | None:
    """Return a human-readable review note for *result*, or ``None`` if no review needed."""
    component = result.enriched_component
    base_name = component.name

    # Uncertain manufacturer always warrants review; append "?" marker
    if component.manufacturer_uncertain:
        note = f"{base_name} ?"
        if result.confidence == Confidence.NOT_FOUND:
            note += " (manufacturer uncertain, no certifications found)"
        else:
            note += " (manufacturer uncertain)"
        return note

    if result.confidence == Confidence.NOT_FOUND:
        return f"{base_name} (no certifications found)"

    if result.confidence == Confidence.LOW:
        return f"{base_name} (low confidence – manual verification recommended)"

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    results_by_assembly: dict[str, list[CertificationResult]] = {}
    components_needing_review: list[str] = []

    for result in results:
        deduped = _deduplicate(result.certifications)
        confidence = _assign_confidence(deduped)

        final_result = CertificationResult(
            enriched_component=result.enriched_component,
            certifications=deduped,
            confidence=confidence,
            search_log=result.search_log,
        )

        # Group by assembly
        assembly = result.enriched_component.assembly
        results_by_assembly.setdefault(assembly, []).append(final_result)

        # Flag for manual review where appropriate
        note = _review_note(final_result)
        if note is not None:
            components_needing_review.append(note)

    log.info(
        "Compilation complete: %d assemblies, %d components needing review",
        len(results_by_assembly),
        len(components_needing_review),
    )

    return PipelineOutput(
        project_name=project_name,
        source_document=source_document,
        results_by_assembly=results_by_assembly,
        components_needing_review=components_needing_review,
    )
