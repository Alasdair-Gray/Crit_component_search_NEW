"""
Stage 2 – LLM Analysis & Normalisation
========================================
Uses an LLM (Anthropic Claude) to normalise incomplete component data and
generate targeted search queries for each component.

Responsibilities
----------------
- Accept a list of ``Component`` objects.
- For each component, call the LLM to:
    - Confirm or infer the manufacturer from part numbers, descriptions, etc.
    - Standardise the part number format.
    - Classify the component type (PSU, connector, cable, etc.).
    - Generate 3–5 search queries optimised for finding safety certifications.
    - Flag cases where the manufacturer is uncertain.
- Return a list of ``EnrichedComponent`` objects.

API contract
------------
Input  : ``list[Component]``
Output : ``list[EnrichedComponent]``

Environment variables used
--------------------------
- ``ANTHROPIC_API_KEY`` – Anthropic API credential.

Example usage
-------------
::

    from pipeline.analyse import enrich_components

    enriched = enrich_components(components)
    # [EnrichedComponent(confirmed_manufacturer='Schurter', ...), ...]
"""

from __future__ import annotations

from pipeline.models import Component, EnrichedComponent


def enrich_components(components: list[Component]) -> list[EnrichedComponent]:
    """Normalise and enrich each :class:`~pipeline.models.Component` via LLM.

    Parameters
    ----------
    components:
        Raw components extracted by :mod:`pipeline.ingest`.

    Returns
    -------
    list[EnrichedComponent]
        One enriched entry per input component, in the same order.

    Raises
    ------
    RuntimeError
        If the LLM API call fails after retries.
    """
    raise NotImplementedError("Stage 2 (analyse) is not yet implemented.")
