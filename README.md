# Identify-AI

A **local-first** desktop application that analyses documents, images, and text files using local LLMs via [Ollama](https://ollama.com) and proposes intelligent, standardised filenames based on file content.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| [Ollama](https://ollama.com/download) | Must be running before launching the app |
| `qwen3-vl:4b` model | Vision — for images & PDFs |
| `llama3.2:3b` model | Text — for documents & code |

Pull the required models:
```bash
ollama pull qwen3-vl:4b
ollama pull llama3.2:3b
```

---

## Installation

```bash
# 1. Clone / place files in your project folder
cd D:\_identify

# 2. Create and activate a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the required directories
mkdir inbox staging archive
```

---

## Project Structure

```
D:\_identify\
├── config.json        ← paths, model names, naming schema
├── utils.py           ← thumbnail generation & filename builder
├── analyzer.py        ← Ollama async analysis logic
├── main.py            ← Flet GUI
├── requirements.txt
├── inbox\             ← drop files here to analyse
├── staging\           ← renamed files land here
└── archive\           ← long-term storage (manual)
```

---

## Running

```bash
python main.py
```

---

## How It Works

1. **Pick Files** — choose individual files via the file picker.
2. **Pick Folder** — choose a folder; all top-level files are queued.
3. Each file is analysed concurrently (max 2 at a time to avoid saturating Ollama).
4. The vision model (`qwen3-vl:4b`) handles images and PDFs; the text model (`llama3.2:3b`) handles everything else.
5. Each file card shows a live status, the suggested filename (colour-coded green on success), and the model's reasoning.
6. Hit **copy** to copy the suggested name to your clipboard, or **rename** to copy the file to the `staging` directory with the new name.

---

## Configuration (`config.json`)

```jsonc
{
  "paths": {
    "inbox":   "D:\\_identify\\inbox",
    "staging": "D:\\_identify\\staging",
    "archive": "D:\\_identify\\archive"
  },
  "models": {
    "vision": "qwen3-vl:4b",
    "text":   "llama3.2:3b"
  },
  "naming_schema": {
    "default_format": "{YYYY-MM-DD}_{category}_{suggested_name}",
    "max_length": 120,
    "replace_spaces_with": "_",
    "lowercase": true
  }
}
```

Change `default_format` to any combination of `{YYYY-MM-DD}`, `{category}`, and `{suggested_name}`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ollama.ResponseError` | Ensure Ollama is running (`ollama serve`) and models are pulled |
| Blank thumbnails for PDFs | `pip install PyMuPDF` |
| `AttributeError: module 'flet.controls.material.icons'` | Do not pass `icon=` to `FilledButton` — already fixed in this codebase |
| GUI freezes | All analysis runs via `page.run_task()` — if freezing occurs, check Ollama isn't overloaded |