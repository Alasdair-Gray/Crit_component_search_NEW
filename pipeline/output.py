"""
Stage 5 – Report Generation
==============================
Renders a formatted .docx certification report from the compiled pipeline
output.

Responsibilities
----------------
- Accept a ``PipelineOutput`` object and an output path.
- Generate a well-structured .docx report using python-docx, including:
    - Cover page with project name, source document, and generation date.
    - Per-assembly sections with a certification table for each component.
    - A "Needs Review" section listing flagged components.
    - A search log appendix for traceability.
- Write the file to the specified output path.
- Return the output path for downstream use (e.g. serving via the web layer).

API contract
------------
Input  : ``PipelineOutput``, ``output_path: str | Path``
Output : ``Path`` (resolved path to the written file)

Example usage
-------------
::

    from pipeline.output import generate_report

    report_path = generate_report(pipeline_output, "reports/acme_widget_v2.docx")
    print(f"Report written to {report_path}")
"""

from __future__ import annotations

from pathlib import Path

from pipeline.models import PipelineOutput


def generate_report(pipeline_output: PipelineOutput, output_path: str | Path) -> Path:
    """Render *pipeline_output* as a formatted ``.docx`` file.

    Parameters
    ----------
    pipeline_output:
        The structured result from :mod:`pipeline.compile`.
    output_path:
        Destination path for the generated report.

    Returns
    -------
    Path
        Resolved, absolute path to the written ``.docx`` file.

    Raises
    ------
    OSError
        If the output directory does not exist or is not writable.
    """
    raise NotImplementedError("Stage 5 (output) is not yet implemented.")
