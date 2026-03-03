"""
Flask web application – thin adapter over the cert-checker pipeline.

Routes
------
GET  /          – upload form
POST /run       – accept .docx upload, run pipeline, return report
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, send_file, url_for

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")

ALLOWED_EXTENSIONS = {"docx"}


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/run")
def run_pipeline():
    if "file" not in request.files:
        flash("No file part in the request.")
        return redirect(url_for("index"))

    file = request.files["file"]
    project_name = request.form.get("project_name", "Unknown Project")

    if file.filename == "" or not _allowed(file.filename):
        flash("Please upload a .docx file.")
        return redirect(url_for("index"))

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / file.filename
        file.save(input_path)

        output_path = Path(tmpdir) / "report.docx"

        from pipeline.ingest import extract_components
        from pipeline.analyse import enrich_components
        from pipeline.search import search_certifications
        from pipeline.compile import compile_results
        from pipeline.output import generate_report

        components = extract_components(input_path)
        enriched = enrich_components(components)
        results = search_certifications(enriched)
        pipeline_output = compile_results(
            results=results,
            project_name=project_name,
            source_document=file.filename,
        )
        report_path = generate_report(pipeline_output, output_path)

        return send_file(
            report_path,
            as_attachment=True,
            download_name=f"{project_name}_cert_report.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


if __name__ == "__main__":
    app.run(debug=True)
