# Identify-AI MVP Implementation

## Current state

Identify-AI is now a local, review-first file-naming tool. It validates its Ollama setup at startup, analyzes supported files locally, lets users edit and approve proposed filenames, and creates collision-safe renamed copies in `Staging`. It never changes the original source files.

Supported MVP inputs are PDFs, images, text/code files, DOCX, XLSX, and PPTX. Searchable PDFs are processed as document text; scanned PDFs use the configured vision model.

## Outputs and audit trail

Each completed batch produces a Markdown report and JSON manifest in `Archive\Reports`. The manifest includes source paths, final names, categories, approval status, staged paths, model names, and per-file outcomes.

## Validation

Run the regression suite from the project virtual environment:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Deferred work

Destructive rename/move operations, legacy Office files, email support, cloud sync, dashboards, and visual polish remain outside the MVP.
