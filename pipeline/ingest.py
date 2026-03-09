"""
Stage 1 – Document Ingestion
=============================
Parses a .docx file and extracts the component inventory using python-docx
to pull out the raw text and an LLM to interpret the structure.

Approach
--------
1. Walk the document body in element order (preserving heading → table
   relationships) and build a structured text representation.
2. Send that text to the LLM with a prompt asking for a JSON array of
   components.
3. Parse the JSON into ``Component`` objects and return the list.

API contract
------------
Input  : ``str | Path`` – path to the source ``.docx``
Output : ``list[Component]``

The optional *llm* parameter accepts any :class:`~pipeline.llm.LLMProvider`
implementation, making the stage trivially testable without real API calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.llm import LLMProvider, get_default_provider
from pipeline.models import Component

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a technical document parser specialising in safety engineering "
    "documentation. Your task is to extract component inventory information "
    "from product specification documents. Return valid JSON only, with no "
    "additional text, explanation, or markdown fences."
)

_EXTRACTION_PROMPT = """\
Analyse the following product specification document and extract every component.

Rules:
- A "component" is any discrete electrical or mechanical part (PSU, connector,
  cable, fuse, terminal block, switch, relay, etc.).
- The "assembly" is the containing section or sub-assembly (infer from the
  nearest heading above the component if not explicit).
- Include ALL components, even if they only have a wire marking or cable
  designation (e.g. "E254552 AWM 1015", "H07V2-K BASEC").
- For each component extract as much as possible: name, part number, and
  manufacturer. Leave a field null if it cannot be determined.
- "raw_text" must be the exact original text from the document that describes
  the component (copy it verbatim).

Return ONLY a JSON object in this exact schema:
{{
  "components": [
    {{
      "name": "<descriptive component name>",
      "assembly": "<assembly or sub-assembly name>",
      "raw_text": "<verbatim original text from the document>",
      "part_number": "<part/model number or null>",
      "manufacturer": "<manufacturer name or null>",
      "description": "<additional description or null>"
    }}
  ]
}}

If no components are found return: {{"components": []}}

Document content:
---
{document_content}
---"""


# ---------------------------------------------------------------------------
# Document text extraction
# ---------------------------------------------------------------------------


def _extract_document_text(docx_path: Path) -> str:
    """Walk *docx_path* body in element order and return a structured string.

    Headings are rendered as Markdown-style ``# Heading``.
    Tables are rendered as pipe-separated rows.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(docx_path))
    parts: list[str] = []

    for child in doc.element.body:
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            style_name = para.style.name if para.style else ""
            if "Heading" in style_name:
                digits = "".join(c for c in style_name if c.isdigit())
                level = int(digits) if digits else 1
                parts.append("#" * level + " " + text)
            else:
                parts.append(text)

        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                parts.append("| " + " | ".join(cells) + " |")
            parts.append("")  # blank line after each table

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------


def _parse_llm_response(response_text: str) -> list[dict]:
    """Extract the ``components`` list from the LLM's JSON response.

    Handles the common case where the model wraps its answer in a
    triple-backtick ``json`` fence despite being asked not to.
    """
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
    return data.get("components", [])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_components(
    docx_path: str | Path,
    llm: LLMProvider | None = None,
) -> list[Component]:
    """Parse *docx_path* and return a flat list of :class:`~pipeline.models.Component`.

    Parameters
    ----------
    docx_path:
        Absolute or relative path to the source ``.docx`` file.
    llm:
        LLM provider to use for component interpretation.  Defaults to
        :func:`~pipeline.llm.get_default_provider` (Azure LLM).

    Returns
    -------
    list[Component]
        One entry per component found.  Order matches document reading order.

    Raises
    ------
    FileNotFoundError
        If *docx_path* does not exist.
    ValueError
        If the LLM returns a response that cannot be parsed as JSON.
    """
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f"Document not found: {docx_path}")

    if llm is None:
        llm = get_default_provider()

    log.info("Extracting text from %s", docx_path.name)
    document_content = _extract_document_text(docx_path)

    if not document_content.strip():
        log.warning("Document appears to be empty: %s", docx_path.name)
        return []

    log.info("Sending %d chars to LLM for component extraction", len(document_content))
    prompt = _EXTRACTION_PROMPT.format(document_content=document_content)
    response = llm.complete(prompt, system=_SYSTEM_PROMPT)

    try:
        raw_components = _parse_llm_response(response)
    except (json.JSONDecodeError, KeyError) as exc:
        log.error("Failed to parse LLM response: %s", exc)
        log.debug("LLM response was:\n%s", response)
        raise ValueError(f"LLM returned unparseable response: {exc}") from exc

    components: list[Component] = []
    for item in raw_components:
        try:
            components.append(
                Component(
                    name=item["name"],
                    assembly=item.get("assembly") or "Unknown",
                    raw_text=item.get("raw_text") or item["name"],
                    part_number=item.get("part_number") or None,
                    manufacturer=item.get("manufacturer") or None,
                    description=item.get("description") or None,
                )
            )
        except (KeyError, ValueError) as exc:
            log.warning("Skipping malformed component entry %s: %s", item, exc)

    log.info("Extracted %d component(s) from %s", len(components), docx_path.name)
    return components
