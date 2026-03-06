# Architecture Guide

> **Audience:** The dev team rebuilding this PoC on production infrastructure.

---

## Overview

Critical Component Checker is a five-stage pipeline wrapped in a thin web layer. The core
pipeline is **completely independent of Flask** (or any other web framework).
Every stage communicates exclusively through the Pydantic models defined in
`pipeline/models.py`. No stage imports from `web/`.

```
.docx file
    │
    ▼
┌──────────┐   list[Component]       ┌──────────┐   list[EnrichedComponent]
│  ingest  │ ───────────────────────▶│ analyse  │ ──────────────────────────▶
└──────────┘                         └──────────┘
                                                             │
                                          list[CertificationResult]
                                                             ▼
                                                      ┌──────────┐
                                                      │  search  │
                                                      └──────────┘
                                                             │
                                          list[CertificationResult]
                                                             ▼
                                                      ┌──────────┐   PipelineOutput
                                                      │ compile  │ ──────────────────▶
                                                      └──────────┘
                                                                         │
                                                                         ▼
                                                                    ┌────────┐
                                                                    │ output │
                                                                    └────────┘
                                                                         │
                                                                         ▼
                                                                   report.docx
```

---

## Stage contracts

### Stage 1 – `pipeline/ingest.py`

| | |
|---|---|
| **Function** | `extract_components(docx_path)` |
| **Input** | `str \| Path` – path to the source `.docx` |
| **Output** | `list[Component]` |
| **Swap trigger** | Different input format (PDF, Excel, API response) |
| **How to swap** | Replace `extract_components` with a function that reads your new source and returns `list[Component]`. The rest of the pipeline is unaffected. |

### Stage 2 – `pipeline/analyse.py`

| | |
|---|---|
| **Function** | `enrich_components(components)` |
| **Input** | `list[Component]` |
| **Output** | `list[EnrichedComponent]` |
| **Swap trigger** | Different LLM provider, fine-tuned model, or rules-based enrichment |
| **How to swap** | Replace the Anthropic API calls. The function signature and return type must not change. If you add a new field to `EnrichedComponent`, update `models.py` and the downstream compile stage. |

### Stage 3 – `pipeline/search.py`

| | |
|---|---|
| **Function** | `search_certifications(enriched_components)` |
| **Input** | `list[EnrichedComponent]` |
| **Output** | `list[CertificationResult]` |
| **Swap trigger** | Different search provider, internal cert database, or scraper |
| **How to swap** | Implement the `SearchProvider` protocol and register it in `_PROVIDERS`. Set `SEARCH_PROVIDER` in `.env`. |

### Stage 4 – `pipeline/compile.py`

| | |
|---|---|
| **Function** | `compile_results(results, project_name, source_document)` |
| **Input** | `list[CertificationResult]`, two `str` metadata fields |
| **Output** | `PipelineOutput` |
| **Swap trigger** | New deduplication strategy, confidence scoring, or database persistence |
| **How to swap** | Replace the function body. Return type must remain `PipelineOutput`. |

### Stage 5 – `pipeline/output.py`

| | |
|---|---|
| **Function** | `generate_report(pipeline_output, output_path)` |
| **Input** | `PipelineOutput`, `str \| Path` |
| **Output** | `Path` (resolved path to the written file) |
| **Swap trigger** | Different output format (PDF, XLSX, HTML, API push) |
| **How to swap** | Replace `generate_report`. Return a `Path` or adapt the web layer to handle your new return type. |

---

## Swapping the LLM

1. Install the new provider's SDK and add it to `requirements.txt`.
2. Add a new env var (e.g. `OPENAI_API_KEY`) to `.env.example`.
3. Update `pipeline/analyse.py` – replace the Anthropic client with the new
   SDK. The function signature stays the same.
4. No other files need to change.

---

## Swapping the search provider

`pipeline/search.py` defines a `SearchProvider` protocol:

```python
class SearchProvider(Protocol):
    def query(self, search_string: str) -> list[dict]: ...
```

Steps:

1. Create a class that implements `query(...)`.
2. Add it to the `_PROVIDERS` dict keyed by its name string.
3. Set `SEARCH_PROVIDER=your_provider_name` in `.env`.

---

## Swapping the web framework

The `web/` layer is a thin adapter. It:

- Accepts file uploads.
- Calls the pipeline stages in sequence.
- Serves the generated `.docx` back to the user.

To replace Flask with FastAPI (or any other framework):

1. Rewrite `web/app.py` using the new framework.
2. Keep all pipeline imports unchanged.
3. The pipeline stages do not need to know anything about HTTP.

---

## Introducing a database

There is currently no persistence layer. When the dev team adds one, the
recommended insertion point is **between Stage 4 and Stage 5**:

```
compile_results(...)  →  [persist to DB]  →  generate_report(...)
```

Suggested schema (add to `pipeline/models.py` or a new `db/schema.py`):

| Table | Key columns |
|---|---|
| `projects` | `id`, `name`, `source_document`, `created_at` |
| `components` | `id`, `project_id`, `assembly`, `name`, `part_number`, `manufacturer` |
| `certifications` | `id`, `component_id`, `standard`, `cert_number`, `source_url`, `confidence` |
| `search_log_entries` | `id`, `component_id`, `query`, `provider`, `result_count`, `timestamp` |

The `PipelineOutput` model maps cleanly onto this schema. A new
`pipeline/persist.py` module should handle DB writes, taking a
`PipelineOutput` as input and returning its database ID.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API credential for Stage 2 |
| `SEARCH_API_KEY` | Yes | API key for the configured search provider |
| `SEARCH_PROVIDER` | Yes | One of `brave` (default) |
| `FLASK_SECRET_KEY` | No | Flask session secret — change from default in production |
| `LOG_LEVEL` | No | Python logging level (default: `INFO`) |

---

## Web layer routes

| Method | Route | Description |
|---|---|---|
| GET | `/` | Upload form |
| POST | `/upload` | Accept `.docx`, start background pipeline, redirect to status |
| GET | `/status/<job_id>` | Pipeline progress (auto-refreshes every 3 s) |
| GET | `/results/<job_id>` | Editable certification results table |
| POST | `/generate/<job_id>` | Generate `.docx` from (possibly edited) results |
| GET | `/log/<job_id>` | Full per-component search log |

Job state is stored in an in-memory dict keyed by UUID. For production, replace
this with a database or a task queue (Celery, RQ, etc.).

---

## Running the pipeline locally (CLI)

```bash
cp .env.example .env
# fill in your keys
pip install -r requirements.txt
python cli.py input_document.docx --project "My Product"
# with explicit output path:
python cli.py input_document.docx --project "My Product" --output results.docx
```

---

## Running the web app

```bash
python web/app.py
# visit http://localhost:5000
```
