"""
CLI entry point for the cert-checker pipeline.

Usage
-----
::

    python cli.py input_document.docx --project "My Product"
    python cli.py input_document.docx --project "My Product" --output results.docx

This runs all five pipeline stages in sequence and writes the report to
``reports/<project_name>.docx`` by default.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the cert-checker pipeline end-to-end.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        help="Path to the source .docx specification file",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Human-readable project / product name",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for the report (default: reports/<project>.docx)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    project_name = args.project
    output_path = (
        Path(args.output)
        if args.output
        else Path("reports") / f"{project_name}.docx"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 1 – Document ingestion
    # ------------------------------------------------------------------
    from pipeline.ingest import extract_components

    log.info("Stage 1/5 – Ingesting %s", input_path)
    try:
        components = extract_components(input_path)
    except Exception as exc:
        log.error("Stage 1 (ingest) failed: %s", exc)
        sys.exit(1)
    log.info("  Found %d component(s)", len(components))

    # ------------------------------------------------------------------
    # Stage 2 – Component analysis and normalisation
    # ------------------------------------------------------------------
    from pipeline.analyse import enrich_components

    log.info("Stage 2/5 – Enriching components via LLM")
    try:
        enriched = enrich_components(components)
    except Exception as exc:
        log.error("Stage 2 (analyse) failed: %s", exc)
        sys.exit(1)
    log.info("  Enriched %d component(s)", len(enriched))

    # ------------------------------------------------------------------
    # Stage 3 – Web search for certifications
    # ------------------------------------------------------------------
    from pipeline.search import search_certifications

    log.info("Stage 3/5 – Searching for certifications")
    try:
        results = search_certifications(enriched)
    except Exception as exc:
        log.error("Stage 3 (search) failed: %s", exc)
        sys.exit(1)
    found = sum(1 for r in results if r.certifications)
    log.info("  Certifications found for %d/%d component(s)", found, len(results))

    # ------------------------------------------------------------------
    # Stage 4 – Compilation and aggregation
    # ------------------------------------------------------------------
    from pipeline.compile import compile_results

    log.info("Stage 4/5 – Compiling results")
    try:
        pipeline_output = compile_results(
            results=results,
            project_name=project_name,
            source_document=input_path.name,
        )
    except Exception as exc:
        log.error("Stage 4 (compile) failed: %s", exc)
        sys.exit(1)
    if pipeline_output.components_needing_review:
        log.warning(
            "  %d component(s) flagged for manual review",
            len(pipeline_output.components_needing_review),
        )

    # ------------------------------------------------------------------
    # Stage 5 – Report generation
    # ------------------------------------------------------------------
    from pipeline.output import generate_report

    log.info("Stage 5/5 – Generating report → %s", output_path)
    try:
        report_path = generate_report(pipeline_output, output_path)
    except Exception as exc:
        log.error("Stage 5 (output) failed: %s", exc)
        sys.exit(1)

    log.info("Done. Report written to %s", report_path)


if __name__ == "__main__":
    main()
