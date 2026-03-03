# cert-checker

A PoC web application that automates safety-certification lookup for product
component inventories. Engineers upload a `.docx` file describing a product
and its assemblies; the app extracts the component list, queries the web for
safety certifications, and produces a formatted `.docx` report.

---

## Architecture

The application is split into two layers:

| Layer | Location | Description |
|---|---|---|
| **Pipeline** | `pipeline/` | Core logic — portable, framework-independent |
| **Web** | `web/` | Flask adapter — file upload, job dispatch, file serve |

The pipeline stages communicate exclusively through the Pydantic models in
`pipeline/models.py`. No stage imports from `web/`. This means every stage
can be extracted and replaced without touching the others.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design guide,
swap instructions, and database schema recommendations.

### Pipeline stages

```
ingest → analyse → search → compile → output
```

1. **ingest** – parse `.docx`, extract component tables → `list[Component]`
2. **analyse** – LLM normalisation & search-query generation → `list[EnrichedComponent]`
3. **search** – web search for certifications → `list[CertificationResult]`
4. **compile** – deduplicate & structure results → `PipelineOutput`
5. **output** – render `.docx` report → `Path`

---

## Setup

```bash
git clone <repo-url>
cd cert-checker
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in ANTHROPIC_API_KEY and SEARCH_API_KEY
```

---

## Usage

### Web app

```bash
python web/app.py
# Open http://localhost:5000
```

### CLI

```bash
python cli.py --input path/to/spec.docx --project "My Product"
```

---

## Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key (Stage 2) |
| `SEARCH_API_KEY` | Search provider API key (Stage 3) |
| `SEARCH_PROVIDER` | `brave`, `serpapi`, or `google` |
| `LOG_LEVEL` | Python log level (default: `INFO`) |

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
    ingest.py                   # Stage 1: parse .docx
    analyse.py                  # Stage 2: LLM enrichment
    search.py                   # Stage 3: web search
    compile.py                  # Stage 4: aggregate results
    output.py                   # Stage 5: generate report
  web/
    app.py                      # Flask routes
    templates/                  # Jinja2 HTML templates
    static/                     # CSS / JS
  tests/                        # Unit tests
  docs/
    ARCHITECTURE.md             # Dev-team guide: how to swap components
```

---

## Running tests

```bash
pytest tests/
```

---

## Status

This is a **proof of concept**. Pipeline stage implementations are stubbed
out with `NotImplementedError`. See each module's docstring for the full API
contract.
