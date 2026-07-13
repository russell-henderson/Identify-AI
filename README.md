# Identify-AI

A local-first desktop application that analyses documents, images, and text files using local LLMs via [Ollama](https://ollama.com) and proposes standardized filenames based on file content.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| [Ollama](https://ollama.com/download) | Must be running before launching the app |
| `qwen3-vl:4b` model | Vision - for images and PDFs |
| `llama3.2:3b` model | Text - for documents and code |

Pull the required models:

```bash
ollama pull qwen3-vl:4b
ollama pull llama3.2:3b
```

---

## Installation

```bash
# 1. Clone or place files in your project folder
cd D:\_identify

# 2. Create and activate a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate
# source .venv/bin/activate     # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the required directories
mkdir Inbox Staging Archive
```

---

## Project Structure

```
D:\_identify\
├── config.json        <- paths, model names, naming settings
├── utils.py           <- thumbnail generation and filename helpers
├── analyzer.py        <- Ollama async analysis logic
├── main.py            <- Flet GUI
├── requirements.txt
├── Inbox\             <- drop files here to analyse
├── Staging\           <- future copy-to-staging workflow
└── Archive\           <- long-term storage (manual)
```

---

## Running

```bash
python main.py
```

---

## How It Works

1. Pick Files - choose individual files via the file picker.
2. Pick Folder - choose a folder; files are scanned according to the recursive setting in `config.json`.
3. Analysis runs sequentially in the current baseline. Thumbnail generation is concurrent.
4. The vision model (`qwen3-vl:4b`) handles images and PDFs; the text model (`llama3.2:3b`) handles everything else.
5. Each file card shows a live status, the suggested filename, the category, and the model's reasoning.
6. Copy, rename, and apply actions are planned for a later pass; the current baseline is read-only.
7. After each batch, the app writes a Markdown report to `Archive\Reports` with the file-level results and category summary.

---

## Configuration (`config.json`)

```jsonc
{
  "paths": {
    "root": "D:\\_identify",
    "inbox": "D:\\_identify\\Inbox",
    "staging": "D:\\_identify\\Staging",
    "archive": "D:\\_identify\\Archive"
  },
  "ollama": {
    "host": "http://localhost:11434",
    "vision_model": "qwen3-vl:4b",
    "text_model": "llama3.2:3b"
  },
  "naming": {
    "pattern": "{suggested_name}",
    "max_length": 120,
    "replace_spaces_with": "_",
    "lowercase": true
  },
  "processing": {
    "analysis_workers": 2,
    "supported_extensions": [
      ".pdf",
      ".png",
      ".jpg",
      ".jpeg",
      ".webp",
      ".bmp",
      ".gif",
      ".txt",
      ".md",
      ".csv",
      ".json",
      ".xml",
      ".yaml",
      ".yml",
      ".ini",
      ".cfg",
      ".log",
      ".py",
      ".js",
      ".ts",
      ".tsx",
      ".jsx",
      ".html",
      ".css",
      ".scss",
      ".sql",
      ".sh",
      ".ps1",
      ".bat",
      ".cmd"
    ],
    "recursive_folder_scan": true
  }
}
```

The code reads the `paths`, `ollama`, `naming`, and `processing` sections above.
`analysis_workers` defaults to 2 and caps how many files are analyzed at once.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ollama.ResponseError` | Ensure Ollama is running (`ollama serve`) and models are pulled |
| Blank thumbnails for PDFs | `pip install PyMuPDF` |
| `AttributeError: module 'flet.controls.material.icons'` | Do not pass `icon=` to `FilledButton` - already fixed in this codebase |
| GUI freezes | The current baseline refreshes from background tasks; the new safety fix prevents destroyed-session errors when closing mid-batch |
