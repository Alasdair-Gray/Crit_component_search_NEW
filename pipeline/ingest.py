"""
Stage 1 – Document Ingestion
=============================
Parses an uploaded .docx file and extracts the component inventory.

Responsibilities
----------------
- Open and read the .docx using python-docx.
- Locate component tables within the document (by heading, style, or position).
- Parse each row into a ``Component`` model, preserving the original raw text.
- Return a list of ``Component`` objects grouped by assembly.

API contract
------------
Input  : path to a .docx file (``str | Path``)
Output : ``list[Component]``

Example usage
-------------
::

    from pipeline.ingest import extract_components

    components = extract_components("path/to/product_spec.docx")
    # [Component(name='IEC inlet', assembly='Power Assembly', ...), ...]
"""

from __future__ import annotations

from pathlib import Path

from pipeline.models import Component


def extract_components(docx_path: str | Path) -> list[Component]:
    """Parse *docx_path* and return a flat list of :class:`~pipeline.models.Component`.

    Parameters
    ----------
    docx_path:
        Absolute or relative path to the source ``.docx`` file.

    Returns
    -------
    list[Component]
        One entry per component row found in the document.  The ``assembly``
        field reflects which assembly section the component was found under.

    Raises
    ------
    FileNotFoundError
        If *docx_path* does not exist.
    ValueError
        If no component tables can be located in the document.
    """
    raise NotImplementedError("Stage 1 (ingest) is not yet implemented.")
