# Identify-AI

Identify-AI is a local-first Windows desktop app for reviewing, standardizing, and safely staging filenames. It analyzes files with local Ollama models, lets you edit and approve the proposed filenames, then creates renamed copies in a Staging folder. Original files are never renamed, moved, or deleted.

## Supported files

- PDFs: selectable text is analyzed across the document; scanned PDFs use the local vision model.
- Images: PNG, JPG, JPEG, WEBP, BMP, and GIF.
- Office documents: DOCX, XLSX, and PPTX, using local text extraction.
- Plain text, structured data, configuration, and supported source-code files listed in `config.json`.

Legacy Office files (`.doc`, `.xls`, `.ppt`), password-protected documents, email files, and cloud synchronization are not part of this MVP.

## Setup

1. Install Python 3.10+ and [Ollama](https://ollama.com/download).
2. Pull the configured models:

   ```powershell
   ollama pull qwen3-vl:4b
   ollama pull llama3.2:3b
   ```

3. Create a virtual environment and install dependencies:

   ```powershell
   cd D:\_identify
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. Start the app:

   ```powershell
   python main.py
   ```

At startup, the app verifies Ollama, the configured models, and its output folders. The default `Inbox`, `Staging`, and `Archive` paths in `config.json` are relative to the project folder, so the project can be moved without changing drive-specific paths.

## Workflow

1. Pick files or a folder.
2. Review each category, explanation, and editable filename suggestion.
3. Select **Approve** for the files to keep, then use **Stage Copy** or **Stage Approved**.
4. Identify-AI creates a renamed copy in `Staging`; collision-safe suffixes are applied if needed.
5. Find a Markdown report and JSON audit manifest under `Archive\Reports`.

The manifest records the original path, final filename, category, approval status, staged path, model names, and outcome for every item in the batch.

## Configuration

`config.json` controls output paths, models, naming schemas, concurrency, extraction limits, and recursive scanning. Relative `paths` values are resolved from the application folder. Use `active_schema` to select one of the configured filename patterns; all generated or edited filenames are normalized for Windows and keep the source file's extension.

## Validation

Run the MVP regression suite with:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
