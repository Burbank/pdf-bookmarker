# Changelog

## 2025-05-12 — Initial Release

### Core
- AI-powered bookmark generation using font-size analysis + LLM structuring
- Two-strategy approach: font-size headings (fast, cheap) with text extraction fallback
- Supports OpenAI, Anthropic, xAI/Grok, and local models (Ollama, LM Studio)
- Batch processing: multiple files, `--dir`, `--recursive`
- Auto-loads API key from `.env` file in project directory

### TOC Page Hyperlinks
- Detects Contents/TOC pages and adds clickable in-page links to target pages
- Multi-pattern search handles split titles ("Chapter 1" on one line, title on next)
- Strips generic prefixes (Chapter N:, Part II —) and leading numbers for flexible matching

### Smart Chapter Titles
- Merges generic labels with descriptive titles: "Chapter 2" + "God's Purpose" → "2 God's Purpose"
- Prompt instructs AI to drop the word "Chapter" from bookmark titles

### Redundant Bookmark Collapsing
- When parent and child bookmarks share the same page AND similar titles (≥70% word overlap), merges into one entry
- Generic labels (Chapter N, Part N, Section N) always collapse with their same-page child
- Runs iteratively to handle chains of 3+ redundant levels

### Excessive Bold Text Handling
- When heading candidates exceed 500 (textbooks with bold vocabulary), retries with font-size-only detection
- Falls back to text mode if still too many candidates (>1,500)

### Password-Protected PDF Detection
- Early check for `needs_pass` with clear error message in notification

### macOS Integration
- Finder Quick Action (right-click service) installed as "PDF Bookmark"
- Notifications show specific errors; long errors link to `/tmp/pdf_bookmarker.log`
- Error log includes timestamps and filenames

### Compatibility
- Python 3.7+ via `from __future__ import annotations`
- Single dependency: `pymupdf>=1.24.0`
- API calls use stdlib `urllib` — no extra HTTP packages
