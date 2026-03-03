"""
CLI entry point for the cert-checker pipeline.

Usage
-----
::

    python cli.py --input path/to/spec.docx --project "My Product"

This runs all five pipeline stages in sequence and writes the report to
``reports/<project_name>.docx``.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the cert-checker pipeline.")
    parser.add_argument("--input", required=True, help="Path to the source .docx file")
    parser.add_argument("--project", required=True, help="Project/product name")
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for the report (default: reports/<project>.docx)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    project_name = args.project
    output_path = Path(args.output) if args.output else Path("reports") / f"{project_name}.docx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    from pipeline.ingest import extract_components
    from pipeline.analyse import enrich_components
    from pipeline.search import search_certifications
    from pipeline.compile import compile_results
    from pipeline.output import generate_report

    log.info("Stage 1 – ingesting %s", input_path)
    components = extract_components(input_path)
    log.info("  Found %d components", len(components))

    log.info("Stage 2 – enriching components via LLM")
    enriched = enrich_components(components)

    log.info("Stage 3 – searching for certifications")
    results = search_certifications(enriched)

    log.info("Stage 4 – compiling results")
    pipeline_output = compile_results(
        results=results,
        project_name=project_name,
        source_document=input_path.name,
    )

    log.info("Stage 5 – generating report")
    report_path = generate_report(pipeline_output, output_path)
    log.info("Report written to %s", report_path)


if __name__ == "__main__":
    main()
