"""
Flask web application – thin adapter over the cert-checker pipeline.

Routes
------
GET  /login             – login form (when auth enabled)
POST /login             – authenticate; redirect to next or /
GET  /logout            – clear session; redirect to login
GET  /                  – upload form
POST /upload            – accept .docx, start background pipeline, redirect
GET  /status/<job_id>   – show pipeline progress (auto-refreshes)
GET  /results/<job_id>  – editable results table
POST /generate/<job_id> – generate .docx from (possibly edited) results
GET  /log/<job_id>      – full search log for auditability

Job state is held in the module-level ``_jobs`` dict (keyed by UUID).
This is intentionally simple for an MVP; a production deployment would use
a database or a task queue (Celery, RQ, etc.).
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Static auth – require credentials unless CCS_WEBAPP_INSECURE=true
# ---------------------------------------------------------------------------
def _check_auth_config() -> tuple[bool, str | None, str | None]:
    """Return (insecure_ok, username, password). Exit if auth required but not configured."""
    insecure = os.getenv("CCS_WEBAPP_INSECURE", "").strip().lower() in ("1", "true", "yes")
    if insecure:
        return True, None, None
    user = os.getenv("CCS_WEBAPP_USER", "").strip()
    password = os.getenv("CCS_WEBAPP_PASSWORD", "").strip()
    if not user or not password:
        print(
            "CCS webapp requires authentication. Set CCS_WEBAPP_USER and CCS_WEBAPP_PASSWORD "
            "in .env, or set CCS_WEBAPP_INSECURE=true to run without auth (not for production).",
            file=sys.stderr,
        )
        sys.exit(1)
    return False, user, password


_INSECURE, _AUTH_USER, _AUTH_PASSWORD = _check_auth_config()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")

ALLOWED_EXTENSIONS = {"docx"}

# ---------------------------------------------------------------------------
# PRODUCTION SWAP POINT – Job store
# ---------------------------------------------------------------------------
# Job state is held in this module-level dict for the prototype. It is keyed
# by UUID and holds pipeline progress, results, and error state.
#
# This must be replaced before any multi-worker or persistent deployment:
#   - Replace with a database table (SQLAlchemy, SQLModel, etc.) so that job
#     state survives restarts and is visible across processes.
#   - Replace _run_pipeline with a proper task queue worker (Celery, RQ, etc.)
#     so that pipeline jobs can be distributed and retried independently.
#   - The PipelineOutput stored in job["pipeline_output"] maps cleanly onto
#     the schema described in docs/ARCHITECTURE.md.
# ---------------------------------------------------------------------------
_jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _domain_from_url(url: str) -> str:
    """Return the bare domain (no www.) from *url*, or an empty string."""
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------


def _run_pipeline(
    job_id: str,
    input_path: Path,
    project_name: str,
    source_document: str,
) -> None:
    """Run all pipeline stages in a background thread, updating job state."""
    from pipeline.ingest import extract_components
    from pipeline.analyse import enrich_components
    from pipeline.search import search_certifications
    from pipeline.compile import compile_results

    job = _jobs[job_id]

    try:
        # Stage 1 – Document ingestion
        job.update(stage=1, stage_name="Ingesting document")
        components = extract_components(input_path)
        job["components_count"] = len(components)

        # Temp file no longer needed after stage 1
        try:
            input_path.unlink(missing_ok=True)
            input_path.parent.rmdir()
        except OSError:
            pass

        # Stage 2 – Component analysis and normalisation
        job.update(stage=2, stage_name="Enriching components via LLM")
        enriched = enrich_components(components)

        # Stage 3 – Web search for certifications
        job.update(stage=3, stage_name="Searching for certifications")
        results = search_certifications(enriched)

        # Stage 4 – Compilation and aggregation
        job.update(stage=4, stage_name="Compiling results")
        pipeline_output = compile_results(
            results=results,
            project_name=project_name,
            source_document=source_document,
        )

        job["pipeline_output"] = pipeline_output
        job["status"] = "done"
        job["stage_name"] = "Complete"
        log.info("Job %s complete – %d component(s)", job_id, len(results))

    except Exception as exc:
        log.exception("Job %s failed at stage %d: %s", job_id, job.get("stage", 0), exc)
        job["status"] = "error"
        job["error"] = str(exc)


# ---------------------------------------------------------------------------
# Form parsing helper for /generate
# ---------------------------------------------------------------------------


def _parse_edited_results(form, original):
    """Reconstruct a ``PipelineOutput`` from the submitted edit form.

    Iterates the original results in the same insertion order as the template,
    reads edited manufacturer/confidence/cert values, and rebuilds the output.
    """
    from pipeline.models import (
        CertificationFound,
        CertificationResult,
        Confidence,
        EnrichedComponent,
        PipelineOutput,
    )

    # Flatten in insertion order – must match template rendering order
    flat_results: list[CertificationResult] = []
    for assembly_results in original.results_by_assembly.values():
        flat_results.extend(assembly_results)

    results_by_assembly: dict[str, list[CertificationResult]] = {}
    components_needing_review: list[str] = []

    for i, result in enumerate(flat_results):
        comp = result.enriched_component

        # --- Edited scalar fields ---
        manufacturer = (
            form.get(f"manufacturer_{i}", comp.confirmed_manufacturer).strip()
            or comp.confirmed_manufacturer
        )
        confidence_str = form.get(f"confidence_{i}", result.confidence.value)
        try:
            confidence = Confidence(confidence_str)
        except ValueError:
            confidence = result.confidence

        # --- Existing certifications (skip deleted rows) ---
        cert_count = int(form.get(f"cert_count_{i}", "0") or "0")
        updated_certs: list[CertificationFound] = []

        for j in range(cert_count):
            if form.get(f"cert_delete_{i}_{j}"):
                continue
            standard = form.get(f"cert_standard_{i}_{j}", "").strip()
            if not standard:
                continue
            source_url = form.get(f"cert_source_url_{i}_{j}", "").strip()
            updated_certs.append(
                CertificationFound(
                    kind=form.get(f"cert_kind_{i}_{j}", "standard"),
                    standard=standard,
                    cert_number=form.get(f"cert_number_{i}_{j}", "").strip() or None,
                    scope=form.get(f"cert_scope_{i}_{j}", "").strip() or standard,
                    source_url=source_url,
                    source_name=_domain_from_url(source_url) or "Manual entry",
                )
            )

        # --- New certifications added by the user ---
        new_cert_count = int(form.get(f"new_cert_count_{i}", "0") or "0")
        for k in range(new_cert_count):
            standard = form.get(f"new_cert_standard_{i}_{k}", "").strip()
            if not standard:
                continue
            source_url = form.get(f"new_cert_source_url_{i}_{k}", "").strip()
            updated_certs.append(
                CertificationFound(
                    kind=form.get(f"new_cert_kind_{i}_{k}", "standard"),
                    standard=standard,
                    cert_number=form.get(f"new_cert_number_{i}_{k}", "").strip() or None,
                    scope=form.get(f"new_cert_scope_{i}_{k}", "").strip() or standard,
                    source_url=source_url,
                    source_name=_domain_from_url(source_url) or "Manual entry",
                )
            )

        # --- Rebuild component with edited manufacturer ---
        comp_data = comp.model_dump()
        comp_data["confirmed_manufacturer"] = manufacturer
        updated_comp = EnrichedComponent(**comp_data)

        final_result = CertificationResult(
            enriched_component=updated_comp,
            certifications=updated_certs,
            confidence=confidence,
            search_log=result.search_log,
        )

        assembly = comp.assembly
        results_by_assembly.setdefault(assembly, []).append(final_result)

        # --- Manual review flags ---
        if updated_comp.manufacturer_uncertain:
            components_needing_review.append(f"{comp.name} ? (manufacturer uncertain)")
        elif not updated_certs:
            components_needing_review.append(f"{comp.name} (no certifications found)")
        elif confidence in (Confidence.LOW, Confidence.NOT_FOUND):
            components_needing_review.append(
                f"{comp.name} (low confidence – manual verification recommended)"
            )

    return PipelineOutput(
        project_name=original.project_name,
        source_document=original.source_document,
        results_by_assembly=results_by_assembly,
        components_needing_review=components_needing_review,
        generated_at=original.generated_at,
    )


# ---------------------------------------------------------------------------
# Auth – require login unless running insecure
# ---------------------------------------------------------------------------

@app.before_request
def _require_auth():
    if _INSECURE:
        return
    if request.endpoint in ("login", "static"):
        return
    if session.get("authenticated"):
        return
    return redirect(url_for("login", next=request.url))


@app.get("/login")
def login():
    if _INSECURE:
        return redirect(url_for("index"))
    return render_template("login.html", next=request.args.get("next", url_for("index")))


@app.post("/login")
def login_post():
    if _INSECURE:
        return redirect(url_for("index"))
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or request.args.get("next") or url_for("index")
    if username == _AUTH_USER and password == _AUTH_PASSWORD:
        session["authenticated"] = True
        return redirect(next_url)
    flash("Invalid username or password.")
    return render_template("login.html", next=next_url), 401


@app.get("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


@app.context_processor
def _auth_context():
    """Expose auth_required so templates can show/hide logout link."""
    return {"auth_required": not _INSECURE}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return render_template("upload.html")


@app.post("/upload")
def upload():
    if "file" not in request.files:
        flash("No file in request.")
        return redirect(url_for("index"))

    file = request.files["file"]
    project_name = request.form.get("project_name", "").strip() or "Unknown Project"

    if not file.filename or not _allowed(file.filename):
        flash("Please upload a .docx file.")
        return redirect(url_for("index"))

    # Save to a temporary directory that the background thread will clean up
    tmpdir = Path(tempfile.mkdtemp())
    input_path = tmpdir / file.filename
    file.save(input_path)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "stage": 0,
        "stage_name": "Starting",
        "project_name": project_name,
        "source_document": file.filename,
        "components_count": 0,
        "error": None,
        "pipeline_output": None,
    }

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, input_path, project_name, file.filename),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("status", job_id=job_id))


@app.get("/status/<job_id>")
def status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        return "Job not found.", 404
    return render_template("status.html", job=job, job_id=job_id)


@app.get("/results/<job_id>")
def results(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        return "Job not found.", 404
    if job["status"] != "done":
        return redirect(url_for("status", job_id=job_id))
    return render_template(
        "results.html",
        job=job,
        job_id=job_id,
        output=job["pipeline_output"],
    )


@app.post("/generate/<job_id>")
def generate(job_id: str):
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        return "Job not found or pipeline not complete.", 404

    from pipeline.output import generate_report

    edited_output = _parse_edited_results(request.form, job["pipeline_output"])

    # Write to a temp file, read bytes, then clean up (avoids file-handle issues)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        generate_report(edited_output, tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    download_name = f"{job['project_name'].replace(' ', '_')}_cert_report.docx"
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=download_name,
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
    )


@app.get("/log/<job_id>")
def log_view(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        return "Job not found.", 404
    return render_template("log.html", job=job, job_id=job_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
