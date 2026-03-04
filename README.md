# cert-checker

A web application that automates safety-certification lookup for product
component inventories. Engineers upload a `.docx` specification file; the app
extracts the component list, queries the web for safety certifications, and
produces a formatted `.docx` report. Results can be reviewed and edited in
the browser before the report is generated.

---

## Architecture

The application is split into two independent layers:

| Layer | Location | Description |
|---|---|---|
| **Pipeline** | `pipeline/` | Core logic — portable, framework-independent |
| **Web** | `web/` | Flask adapter — file upload, job dispatch, editable results |

The pipeline stages communicate exclusively through the Pydantic models in
`pipeline/models.py`. No stage imports from `web/`. Every stage can be
extracted and replaced without touching the others.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design guide,
swap instructions, and database schema recommendations.

### Pipeline stages

```
ingest → analyse → search → compile → output
```

1. **ingest** – parse `.docx`, extract component tables → `list[Component]`
2. **analyse** – LLM normalisation & search-query generation → `list[EnrichedComponent]`
3. **search** – web search + LLM cert extraction → `list[CertificationResult]`
4. **compile** – deduplicate, assign confidence, structure results → `PipelineOutput`
5. **output** – render landscape A4 `.docx` report → `Path`

---

## Setup

```bash
git clone <repo-url>
cd cert-checker
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in your API keys (see Environment variables below)
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Stage 2 (component normalisation) |
| `SEARCH_API_KEY` | Yes | Search provider API key for Stage 3 |
| `SEARCH_PROVIDER` | Yes | Search backend: `brave` (default) |
| `FLASK_SECRET_KEY` | No | Flask session secret — change in production |
| `LOG_LEVEL` | No | Python log level: `DEBUG`, `INFO` (default), `WARNING` |

---

## Usage

### Web app

```bash
python web/app.py
# Open http://localhost:5000
```

**Workflow:**

1. **Upload** — choose a `.docx` specification and enter a project name.
2. **Status** — the pipeline runs in a background thread; the page auto-refreshes every 3 s.
3. **Results** — review the certification table. Engineers can:
   - Correct manufacturer names
   - Edit or remove certification entries
   - Add manual entries
   - Override confidence levels
4. **Generate** — click *Generate Report* to download the formatted `.docx`.
5. **Log** — view the full per-component search log at `/log/<job_id>`.

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

---

## Running tests

```bash
pytest tests/
```

---

## Project structure

```
cert-checker/
  README.md
  requirements.txt
  .env.example
  cli.py                        # CLI entry point
  pipeline/
    __init__.py
    models.py                   # Shared Pydantic data models
    llm.py                      # LLM provider abstraction
    ingest.py                   # Stage 1: parse .docx
    analyse.py                  # Stage 2: LLM enrichment
    search.py                   # Stage 3: web search
    compile.py                  # Stage 4: aggregate results
    output.py                   # Stage 5: generate report
  web/
    app.py                      # Flask routes + background job management
    templates/
      upload.html               # Upload form
      status.html               # Pipeline progress (auto-refreshes)
      results.html              # Editable results table
      log.html                  # Search log viewer
    static/
      style.css                 # Minimal stylesheet
  tests/
    test_ingest.py
    test_analyse.py
    test_search.py
    test_compile.py
    test_output.py
  docs/
    ARCHITECTURE.md             # Dev-team guide: swap components, add a DB, etc.
```
