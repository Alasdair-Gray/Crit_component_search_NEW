"""
Stage 2 – Component Analysis & Normalisation
=============================================
Uses the LLM to normalise incomplete component data and generate targeted
web search queries for each component.

Approach
--------
Components are processed in batches (``BATCH_SIZE`` per LLM call) to keep
latency predictable and avoid excessive API round-trips.  For each batch the
LLM receives the raw component data and returns a parallel JSON array of
enriched fields.  If the LLM fails for a batch (network error, bad JSON, wrong
entry count), every component in that batch falls back to rule-based defaults
so the pipeline can continue.

API contract
------------
Input  : ``list[Component]``
Output : ``list[EnrichedComponent]``  (same length, same order)

The optional *llm* and *batch_size* parameters are exposed for testing and
for callers that want to tune throughput.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pipeline.llm import LLMProvider, get_default_provider
from pipeline.models import Component, EnrichedComponent

log = logging.getLogger(__name__)

BATCH_SIZE = 8  # components per LLM call

VALID_COMPONENT_TYPES = frozenset(
    {
        "PSU",
        "connector",
        "cable",
        "terminal_block",
        "fuse",
        "relay",
        "circuit_breaker",
        "switch",
        "sensor",
        "resistor",
        "capacitor",
        "transformer",
        "motor_controller",
        "other",
    }
)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a safety engineering research assistant specialising in electrical "
    "component certification. Analyse product components and generate optimal web "
    "search queries for finding safety certifications (UL, CE, IEC, EN, VDE, BASEC, "
    "HAR, etc.). Return valid JSON only — no markdown fences, no explanation."
)

_BATCH_PROMPT = """\
Analyse the following electrical/electronic components. For each component provide:

1. confirmed_manufacturer — identify or confirm the manufacturer. Look for clues in
   part numbers (e.g. "HDR" prefix → Mean Well, "6100-42" → Schurter, "WAGO" in name,
   "AWM" → UL-listed wire). Use the authoritative commercial name.

2. standardised_part_number — the core model/catalogue number, stripped of colour
   codes, packaging variants, or quantity suffixes (e.g. "HDR-100-12" not
   "HDR-100-12/PV"). If no part number is identifiable, use the component name.

3. component_type — exactly one of: PSU, connector, cable, terminal_block, fuse,
   relay, circuit_breaker, switch, sensor, resistor, capacitor, transformer,
   motor_controller, other

4. search_queries — 3 to 5 queries that will find safety certification pages.
   Tailor by type:
   - PSU: "[mfr] [part] UL listing", "[part] safety certifications datasheet",
     "[mfr] [part] CE declaration of conformity"
   - Cable: "[designation] BASEC approval", "[HAR standard] [designation]",
     "[mfr] [designation] approval certificate"
   - IEC inlet/connector: "[mfr] [part] UL approval", "[part] IEC 60320 certification",
     "[mfr] [part] safety approvals"
   - Wire marking (UL style, AWM): "[marking] UL wire listing",
     "UL style [number] AWM approval", "[marking] recognised component"
   - Terminal block: "[mfr] [part] VDE approval", "[part] IEC 60947 certification"
   - Fuse: "[mfr] [part] UL248 approval", "[part] IEC 60269 certification"

5. manufacturer_uncertain — true if you are not confident about the manufacturer.

Components to analyse:
{components_json}

Return a JSON object with exactly {count} entries in the same order as the input:
{{
  "enriched": [
    {{
      "confirmed_manufacturer": "<manufacturer>",
      "standardised_part_number": "<part number>",
      "component_type": "<type>",
      "search_queries": ["<q1>", "<q2>", "<q3>"],
      "manufacturer_uncertain": false
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_llm_response(response_text: str) -> list[dict[str, Any]]:
    """Parse the LLM's JSON response and return the ``enriched`` list."""
    text = response_text.strip()
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start + 3 : end]
            if inner.lower().startswith("json"):
                inner = inner[4:]
            text = inner.strip()
    data = json.loads(text)
    return data.get("enriched", [])


def _fallback_queries(component: Component) -> list[str]:
    """Generate basic search queries from whatever data is available."""
    queries: list[str] = []
    name = component.name
    mfr = component.manufacturer
    part = component.part_number

    if mfr and part:
        queries.append(f"{mfr} {part} safety certification")
        queries.append(f"{part} UL listing")
        queries.append(f"{mfr} {part} CE declaration")
    elif part:
        queries.append(f"{part} safety certification")
        queries.append(f"{part} UL listing")
        queries.append(f"{name} safety certification")
    else:
        queries.append(f"{name} safety certification")
        queries.append(f"{name} IEC standard approval")

    queries.append(f"{name} datasheet certifications")

    # Deduplicate preserving insertion order
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique[:5]


def _fallback_enriched(component: Component) -> EnrichedComponent:
    """Build a best-effort ``EnrichedComponent`` without LLM help."""
    return EnrichedComponent(
        **component.model_dump(),
        confirmed_manufacturer=component.manufacturer or "Unknown",
        standardised_part_number=component.part_number or component.name,
        component_type="other",
        search_queries=_fallback_queries(component),
        manufacturer_uncertain=True,
    )


def _build_enriched(component: Component, entry: dict[str, Any]) -> EnrichedComponent:
    """Construct an ``EnrichedComponent`` from a parsed LLM entry dict.

    Falls back to safe defaults for any field that is missing or invalid.
    """
    mfr = (entry.get("confirmed_manufacturer") or "").strip() or (
        component.manufacturer or "Unknown"
    )
    part = (entry.get("standardised_part_number") or "").strip() or (
        component.part_number or component.name
    )

    ctype = (entry.get("component_type") or "other").strip()
    if ctype not in VALID_COMPONENT_TYPES:
        log.debug("Unknown component_type %r – defaulting to 'other'", ctype)
        ctype = "other"

    queries: list[str] = [q for q in entry.get("search_queries", []) if q]
    if len(queries) < 3:
        # Supplement with rule-based fallbacks and deduplicate
        extra = _fallback_queries(component)
        seen = set(queries)
        for q in extra:
            if q not in seen:
                seen.add(q)
                queries.append(q)
            if len(queries) >= 5:
                break
    queries = queries[:5]

    return EnrichedComponent(
        **component.model_dump(),
        confirmed_manufacturer=mfr,
        standardised_part_number=part,
        component_type=ctype,
        search_queries=queries,
        manufacturer_uncertain=bool(entry.get("manufacturer_uncertain", False)),
    )


def _enrich_batch(batch: list[Component], llm: LLMProvider) -> list[EnrichedComponent]:
    """Call the LLM once for *batch* and return one ``EnrichedComponent`` per entry.

    If anything goes wrong (network, bad JSON, wrong entry count), the entire
    batch falls back to rule-based defaults so the pipeline always continues.
    """
    components_json = json.dumps(
        [
            {
                "name": c.name,
                "part_number": c.part_number,
                "manufacturer": c.manufacturer,
                "description": c.description,
                "assembly": c.assembly,
                "raw_text": c.raw_text,
            }
            for c in batch
        ],
        indent=2,
    )
    prompt = _BATCH_PROMPT.format(
        components_json=components_json,
        count=len(batch),
    )

    try:
        response = llm.complete(prompt, system=_SYSTEM_PROMPT)
        raw_entries = _parse_llm_response(response)

        if len(raw_entries) != len(batch):
            raise ValueError(
                f"LLM returned {len(raw_entries)} entries for {len(batch)} components"
            )

        results: list[EnrichedComponent] = []
        for component, entry in zip(batch, raw_entries):
            try:
                results.append(_build_enriched(component, entry))
            except Exception as exc:
                log.warning(
                    "Could not build EnrichedComponent for %r: %s – using defaults",
                    component.name,
                    exc,
                )
                results.append(_fallback_enriched(component))
        return results

    except Exception as exc:
        log.error(
            "LLM batch call failed (%s) – using defaults for %d component(s)",
            exc,
            len(batch),
        )
        return [_fallback_enriched(c) for c in batch]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enrich_components(
    components: list[Component],
    llm: LLMProvider | None = None,
    batch_size: int = BATCH_SIZE,
) -> list[EnrichedComponent]:
    """Normalise and enrich each :class:`~pipeline.models.Component` via LLM.

    Parameters
    ----------
    components:
        Raw components extracted by :mod:`pipeline.ingest`.
    llm:
        LLM provider to use.  Defaults to
        :func:`~pipeline.llm.get_default_provider` (Anthropic Claude).
    batch_size:
        Maximum number of components per LLM call.

    Returns
    -------
    list[EnrichedComponent]
        One enriched entry per input component, in the same order.
        Never raises — LLM failures fall back to rule-based defaults.
    """
    if not components:
        return []

    if llm is None:
        llm = get_default_provider()

    results: list[EnrichedComponent] = []
    total = len(components)
    for i in range(0, total, batch_size):
        batch = components[i : i + batch_size]
        log.info(
            "Enriching components %d–%d of %d",
            i + 1,
            i + len(batch),
            total,
        )
        results.extend(_enrich_batch(batch, llm))

    log.info("Enrichment complete: %d/%d components processed", len(results), total)
    return results
