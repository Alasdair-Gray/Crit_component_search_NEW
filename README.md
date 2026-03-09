# Critical Component Checker

A pipeline application that automates safety-certification lookup for electrical and electronic component inventories. Engineers upload a `.docx` specification file; the app extracts the component list, queries the web for safety certifications, and produces a formatted `.docx` report. Results can be reviewed and edited in the browser before the report is generated.

---

## Table of contents

1. [What it does](#what-it-does)
2. [How it works](#how-it-works)
3. [Quick start](#quick-start)
4. [Configuration](#configuration)
5. [Usage](#usage)
6. [Developer guide](#developer-guide)
7. [Running tests](#running-tests)
8. [Project structure](#project-structure)

---

## What it does

When a product is being designed or certified, engineers must verify that every component in the bill of materials holds the necessary safety certifications — UL listings, CE declarations, IEC standard compliance, VDE approvals, and so on. Doing this manually is time-consuming: each component may need several targeted searches across manufacturer sites, distributor listings, and official certification databases.

Critical Component Checker automates that process end-to-end:

1. It reads a product specification document and identifies every component.
2. It uses an LLM to normalise component names, confirm manufacturers, and generate targeted search queries.
3. It searches the web for safety certification evidence and extracts structured results.
4. It compiles the results, deduplicates them, and assigns a confidence level based on source quality.
5. It presents the results in an editable browser interface so engineers can correct, supplement, or override anything before generating the final report.
6. It produces a formatted, landscape A4 `.docx` certification register ready for inclusion in a technical file.

---

## How it works

The application is split into two independent layers:

| Layer | Location | Description |
|---|---|---|
| **Pipeline** | `pipeline/` | Core logic — portable, framework-independent |
| **Web** | `web/` | Flask adapter — file upload, job dispatch, editable results |

The pipeline is completely independent of Flask (or any other web framework). Every stage communicates exclusively through the Pydantic models defined in `pipeline/models.py`. No pipeline stage imports from `web/`. This means each stage can be extracted, replaced, or tested in isolation without touching anything else.

### Pipeline stages

```
.docx file
    │
    ▼
┌──────────┐   list[Component]        ┌──────────┐   list[EnrichedComponent]
│  ingest  │ ────────────────────────▶│ analyse  │ ─────────────────────────▶
└──────────┘                          └──────────┘
  Stage 1                               Stage 2
  pipeline/ingest.py                    pipeline/analyse.py
                                                    │
                                   list[EnrichedComponent]
                                                    ▼
                                             ┌──────────┐   list[CertificationResult]
                                             │  search  │ ─────────────────────────▶
                                             └──────────┘
                                               Stage 3
                                               pipeline/search.py
                                                                  │
                                              list[CertificationResult]
                                                                  ▼
                                                           ┌──────────┐   PipelineOutput
                                                           │ compile  │ ──────────────▶
                                                           └──────────┘
                                                             Stage 4
                                                             pipeline/compile.py
                                                                           │
                                                                           ▼
                                                                      ┌────────┐
                                                                      │ output │
                                                                      └────────┘
                                                                        Stage 5
                                                                        pipeline/output.py
                                                                           │
                                                                           ▼
                                                                     report.docx
```

#### Stage 1 — Document ingestion (`pipeline/ingest.py`)

Reads the uploaded `.docx` file using `python-docx`, walks the document body in reading order (preserving heading → table relationships), and converts it to a structured text representation. Sends that text to the LLM with a prompt asking for a JSON array of components. Returns a `list[Component]`.

Each `Component` captures: name, assembly (the containing section), part number, manufacturer, and the original verbatim text (for traceability).

#### Stage 2 — Component analysis and normalisation (`pipeline/analyse.py`)

Takes the raw `list[Component]` and uses the LLM to enrich each entry: confirming the manufacturer name, standardising the part number, categorising the component type, and generating 3–5 targeted web search queries optimised for finding safety certifications. Components are processed in batches to keep latency predictable. Returns a `list[EnrichedComponent]`.

If the LLM fails for any batch, that batch falls back to rule-based defaults so the pipeline always continues.

#### Stage 3 — Web search for certifications (`pipeline/search.py`)

For each `EnrichedComponent`, submits its search queries to the configured search provider (Brave Search by default). For each result URL, fetches the page content and asks the LLM to extract structured certification information — distinguishing between *standards* (technical specifications the component complies with) and *certificates* (issued compliance documents with a certificate number). Records a full search log for auditability. Returns a `list[CertificationResult]`.

The search provider is swappable via a `SearchProvider` protocol — see [Developer guide](#developer-guide).

#### Stage 4 — Compilation and aggregation (`pipeline/compile.py`)

Takes the raw `list[CertificationResult]`, deduplicates certifications found via multiple queries for the same component, assigns a final confidence level based on source domain quality (high: manufacturer sites and official databases; medium: distributor listings; low: unrecognised domains), groups results by assembly, and flags components that need manual review. Returns a `PipelineOutput`.

#### Stage 5 — Report generation (`pipeline/output.py`)

Renders the `PipelineOutput` as a formatted landscape A4 `.docx` file: title block, a certification table grouped by assembly with alternating row shading and clickable source hyperlinks, and a footer note listing components flagged for review.

### Web layer

The web layer (`web/app.py`) is a thin Flask adapter that:

- Accepts `.docx` file uploads via a browser form.
- Saves the file to a temp directory and starts the pipeline in a background thread.
- Serves a status page that auto-refreshes every 3 seconds while the pipeline runs.
- Presents an editable results table once the pipeline completes — engineers can correct manufacturer names, edit or remove certification entries, add manual entries, and override confidence levels.
- Generates and streams the `.docx` report when the engineer clicks *Generate Report*.
- Provides a search log view (`/log/<job_id>`) for full auditability.

**Job state is currently stored in an in-memory Python dict.** This is intentional for this prototype. For production deployment, replace this with a database or task queue — see [Developer guide](#developer-guide).

---

## Quick start

```bash
git clone <repo-url>
cd Critical_Component_Checker
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in your API keys (see Configuration below)
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Description |
|---|---|---|
| `AZURE_LLM_ENDPOINT` | Yes | Azure cloud LLM endpoint URL (OpenAI-compatible). Used by Stage 2 (analysis) and Stage 3 (cert extraction). |
| `AZURE_LLM_API_KEY` | Yes | API key/token for the Azure LLM endpoint. |
| `AZURE_LLM_MODEL` | No | Deployment/model name (defaults to `gpt-4` if not set). |
| `SEARCH_API_KEY` | Yes | API key for the configured search provider. For Brave Search, get one at [brave.com/search/api](https://brave.com/search/api/). |
| `SEARCH_PROVIDER` | Yes | Which search backend to use. Currently supported: `brave`. Defaults to `brave`. |
| `FLASK_SECRET_KEY` | No | Flask session secret. **Change this from the default in any non-local deployment.** |
| `LOG_LEVEL` | No | Python logging level. One of `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. |

---

## Usage

### Web app

```bash
python web/app.py
# Open http://localhost:5000
```

**Workflow:**

1. **Upload** — choose a `.docx` specification file and enter a project name.
2. **Status** — the pipeline runs in a background thread. The page auto-refreshes every 3 seconds showing which stage is active.
3. **Results** — review the certification table once the pipeline completes. Engineers can:
   - Correct manufacturer names
   - Edit or remove certification entries
   - Add manual certification entries
   - Override confidence levels
4. **Generate** — click *Generate Report* to download the formatted `.docx` certification register.
5. **Log** — view the full per-component search log at `/log/<job_id>` for audit purposes.

### CLI

```bash
python cli.py input_document.docx --project "My Product"
python cli.py input_document.docx --project "My Product" --output results.docx
```

| Argument | Description |
|---|---|
| `input` | Path to the source `.docx` file (positional) |
| `--project` | Human-readable project / product name (required) |
| `--output` | Output path for the report (default: `reports/<project>.docx`) |

The CLI runs all five stages in sequence and writes the report directly to disk without a review step.

---

## Developer guide

This section is for the team rebuilding Critical Component Checker on production infrastructure. The application is deliberately structured so that each external dependency (LLM, search provider, persistence, web framework) can be swapped independently without touching the core pipeline logic.

### Swapping the LLM

The LLM abstraction lives in `pipeline/llm.py`. All pipeline stages accept an optional `llm` parameter of type `LLMProvider`:

```python
class LLMProvider(Protocol):
    def complete(self, prompt: str, system: str = "") -> str: ...
```

**To use a different LLM provider:**

1. Install the new provider's SDK and add it to `requirements.txt`.
2. Add a new environment variable (e.g. `OPENAI_API_KEY`) to `.env.example`.
3. Write a class that implements `complete(prompt, system) -> str` and instantiate it in `pipeline/llm.py` (mirroring `AzureProvider`).
4. Update `get_default_provider()` to return your new class, or pass an instance directly when calling `enrich_components(components, llm=my_provider)`.

No other files need to change. The function signatures and Pydantic model contracts between stages are unaffected.

**To use a different Azure deployment/model**, pass the model name to `AzureProvider` or set `AZURE_LLM_MODEL` in `.env`:

```python
from pipeline.llm import AzureProvider
provider = AzureProvider(model="my-deployment-name")
```

### Swapping the search provider

`pipeline/search.py` defines a `SearchProvider` protocol and a `_PROVIDERS` registry:

```python
class SearchProvider(Protocol):
    def query(self, search_string: str) -> list[dict]: ...

_PROVIDERS: dict[str, type] = {
    "brave": BraveSearchProvider,
}
```

**To add a new search provider:**

1. Write a class that implements `query(search_string) -> list[dict]`. Each dict must contain at minimum a `url` key; `title` and `description` are used for logging.
2. Add the class to `_PROVIDERS` keyed by a name string.
3. Set `SEARCH_PROVIDER=your_provider_name` in `.env`.

This is the right approach for connecting to an internal certification database, a different web search API, or a custom scraper.

### Adding a persistence layer (database)

There is currently no database. Job state is stored in an in-memory Python dict in `web/app.py` — this is intentional for the prototype but is not suitable for production.

**Recommended approach:**

Add a new `pipeline/persist.py` module. The natural insertion point is between Stage 4 and Stage 5:

```
compile_results(...)  →  [persist to DB]  →  generate_report(...)
```

The `persist` function should accept a `PipelineOutput` and write it to the database, returning the new record's ID. The `PipelineOutput` model maps cleanly onto a relational schema:

| Table | Key columns |
|---|---|
| `projects` | `id`, `name`, `source_document`, `created_at` |
| `components` | `id`, `project_id`, `assembly`, `name`, `part_number`, `manufacturer` |
| `certifications` | `id`, `component_id`, `standard`, `cert_number`, `source_url`, `confidence` |
| `search_log_entries` | `id`, `component_id`, `query`, `provider`, `result_count`, `timestamp` |

**For the web job queue**, replace the `_jobs` dict in `web/app.py` with a proper task queue (Celery, RQ, or similar) backed by Redis or a database. The `_run_pipeline` function in `web/app.py` is a clean target for this — it already encapsulates the full pipeline execution and job state updates. See the marked swap point comment in `web/app.py` for details.

### Swapping the web framework

`web/app.py` is a thin adapter. The pipeline stages have no knowledge of HTTP. To replace Flask:

1. Rewrite `web/app.py` using the new framework (FastAPI, Django, etc.).
2. Keep all pipeline imports unchanged.
3. Adapt the job dispatch pattern to your framework's background task mechanism (e.g. FastAPI's `BackgroundTasks`, or a proper task queue).

### Swapping the input format

Stage 1 (`pipeline/ingest.py`) reads `.docx` files. To accept a different input format (PDF, Excel, API response):

1. Replace or supplement `_extract_document_text()` to read your new format.
2. The `extract_components()` function signature and return type (`list[Component]`) must not change — the rest of the pipeline is unaffected.

### Swapping the output format

Stage 5 (`pipeline/output.py`) generates a `.docx` report. To produce a different output (PDF, Excel, HTML, database push):

1. Replace `generate_report()` with your new renderer.
2. The function must accept `(pipeline_output: PipelineOutput, output_path: str | Path)` and return a `Path`, or adapt the web layer's `/generate` route to handle your new return type.

### Stage contracts at a glance

| Stage | Module | Input | Output | Swap trigger |
|---|---|---|---|---|
| 1 – Ingest | `pipeline/ingest.py` | `str \| Path` | `list[Component]` | Different input format |
| 2 – Analyse | `pipeline/analyse.py` | `list[Component]` | `list[EnrichedComponent]` | Different LLM or rules-based enrichment |
| 3 – Search | `pipeline/search.py` | `list[EnrichedComponent]` | `list[CertificationResult]` | Different search provider or internal DB |
| 4 – Compile | `pipeline/compile.py` | `list[CertificationResult]` | `PipelineOutput` | New deduplication strategy or confidence scoring |
| 5 – Output | `pipeline/output.py` | `PipelineOutput` | `Path` | Different output format |

---

## Running tests

```bash
pytest tests/
```

Each pipeline stage has a corresponding test module. All tests use injected mock LLM providers and mock search providers — no real API calls are made during testing.

---

## Project structure

```
Critical_Component_Checker/
  README.md                         # This file
  requirements.txt                  # Python dependencies
  .env.example                      # Environment variable template
  cli.py                            # CLI entry point (runs all 5 stages end-to-end)
  pipeline/
    __init__.py
    models.py                       # Shared Pydantic data models (stage contracts)
    llm.py                          # LLM provider abstraction and AzureProvider
    ingest.py                       # Stage 1: parse .docx, extract components
    analyse.py                      # Stage 2: LLM normalisation and query generation
    search.py                       # Stage 3: web search and cert extraction
    compile.py                      # Stage 4: deduplicate, score confidence, group
    output.py                       # Stage 5: generate .docx report
  web/
    app.py                          # Flask routes and background job management
    templates/
      upload.html                   # Upload form
      status.html                   # Pipeline progress (auto-refreshes every 3s)
      results.html                  # Editable certification results table
      log.html                      # Per-component search log viewer
    static/
      style.css                     # Minimal stylesheet
  tests/
    test_ingest.py
    test_analyse.py
    test_search.py
    test_compile.py
    test_output.py
  docs/
    ARCHITECTURE.md                 # Detailed architecture reference for the dev team
```
