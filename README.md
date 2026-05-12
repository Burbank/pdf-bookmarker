# PDF Bookmarker — AI-Powered Outline Generator

Add accurate, hierarchical bookmarks (table of contents / outline) to any PDF using AI. Designed for batch-processing large collections of PDFs on your Mac.

## How It Works

1. **Font-size analysis** — PyMuPDF extracts text with font metadata; lines with larger-than-body fonts are identified as heading candidates (fast, minimal tokens)
2. **AI structuring** — heading candidates are sent to an LLM which cleans, deduplicates, and assigns hierarchy levels (chapter → section → subsection)
3. **Fallback** — if font analysis finds nothing (scanned/uniform-font PDFs), raw page text is sent instead
4. **Write** — bookmarks are written back into the PDF via PyMuPDF

The font-analysis strategy keeps AI costs very low (typically <1 cent per PDF) while producing highly accurate results.

## Installation

```bash
pip3 install pymupdf
```

No other dependencies — API calls use Python's built-in `urllib`.

## Quick Start

```bash
# Single file (creates document_bookmarked.pdf)
export OPENAI_API_KEY="sk-..."
python3 pdf_bookmarker.py document.pdf

# Preview without writing
python3 pdf_bookmarker.py --dry-run document.pdf

# Modify in place
python3 pdf_bookmarker.py --in-place document.pdf

# Whole directory, recursive
python3 pdf_bookmarker.py --dir ~/PDFs -r -v

# Replace existing bookmarks
python3 pdf_bookmarker.py --in-place --replace textbook.pdf
```

## Provider Options

### OpenAI (default)

```bash
export OPENAI_API_KEY="sk-..."
python3 pdf_bookmarker.py document.pdf
python3 pdf_bookmarker.py --model gpt-4o document.pdf  # more accurate, higher cost
```

### Anthropic

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python3 pdf_bookmarker.py --provider anthropic document.pdf
python3 pdf_bookmarker.py --provider anthropic --model claude-sonnet-4-20250514 document.pdf
```

### xAI / Grok

```bash
export OPENAI_API_KEY="xai-..."
python3 pdf_bookmarker.py --base-url https://api.x.ai/v1 --model grok-3-mini-fast document.pdf
```

### Local models (Ollama, LM Studio)

```bash
python3 pdf_bookmarker.py --base-url http://localhost:11434/v1 --model llama3 --api-key none document.pdf
```

## All Options

```
usage: pdf_bookmarker.py [options] [files ...]

positional:
  files                 PDF file(s) to process

options:
  --dir DIR             process all PDFs in a directory
  -r, --recursive       recurse into subdirectories (with --dir)
  --api-key KEY         API key (or set OPENAI_API_KEY / ANTHROPIC_API_KEY)
  --provider NAME       openai (default) or anthropic
  --model NAME          model name (default: gpt-4o-mini / claude-3-5-haiku)
  --base-url URL        custom API base URL
  --in-place            modify PDF in place (default: create _bookmarked copy)
  --replace             replace existing bookmarks
  --dry-run             print bookmarks without writing
  --text-mode           skip font analysis, use raw text extraction
  --chars N             max chars per page in text mode (default: 500)
  -v, --verbose         show progress details
```

## Output

- **Default**: creates `OriginalName_bookmarked.pdf` in the same folder
- **`--in-place`**: modifies the original PDF (uses fast incremental save)
- **`--dry-run`**: prints the bookmark tree to the terminal
- Prints the output file path to **stdout** (one per line) for easy piping
- Progress and errors go to **stderr**

## Alfred Workflow Setup

1. Open Alfred Preferences → Workflows → click **+** → **Blank Workflow**
2. Add a **File Action** trigger (accepted types: `com.adobe.pdf`)
3. Connect it to a **Run Script** action:
   - Language: `/bin/bash`
   - Script:

```bash
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
export OPENAI_API_KEY="sk-..."
PYTHON=$(command -v python3 || command -v python)
for f in "$@"; do
  "$PYTHON" "/Users/DuniaMBP/Documents/pdf-bookmarker/pdf_bookmarker.py" --in-place "$f"
done
```

4. Optionally add a **Post Notification** action

## macOS Quick Action (Finder)

1. Open **Automator** → **New Document** → **Quick Action**
2. Set "Workflow receives current **PDF files** in **Finder**"
3. Add a **Run Shell Script** action (pass input as arguments):

```bash
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
export OPENAI_API_KEY="sk-..."
PYTHON=$(command -v python3 || command -v python)
for f in "$@"; do
  "$PYTHON" "/Users/DuniaMBP/Documents/pdf-bookmarker/pdf_bookmarker.py" --in-place "$f"
done
```

4. Save as "Add PDF Bookmarks"
5. Right-click any PDF in Finder → Quick Actions → **Add PDF Bookmarks**

## Tips

- **Dry-run first** on a sample PDF to check quality before batch-processing
- Use `--verbose` to see token estimates and progress
- PDFs already having bookmarks are **skipped** by default (use `--replace` to overwrite)
- For scanned PDFs without OCR text, run OCR first (e.g. `ocrmypdf`)
- `gpt-4o-mini` is the best balance of speed/cost/accuracy; use `gpt-4o` for difficult documents
