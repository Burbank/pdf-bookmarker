#!/usr/bin/env python3
"""
AI-powered PDF bookmark generator.
Scans PDF pages, uses AI to identify document structure,
and writes hierarchical bookmarks (outline/TOC) into the PDF.
"""
from __future__ import annotations

import sys
import os
import gc
import json
import tempfile
import shutil
import argparse
import urllib.request
import urllib.error
from pathlib import Path

BATCH_PAGES = 150


def _load_dotenv():
    """Load .env file from the script's directory if it exists."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


# ---------------------------------------------------------------------------
# Text / heading extraction
# ---------------------------------------------------------------------------

def extract_heading_candidates(src):
    """Use font-size analysis to find heading candidates per page.

    Returns a list of dicts: [{"page": int, "headings": [{"text", "size", "bold"}]}]
    or None if font metadata is unavailable / uniform.
    """
    import fitz

    font_size_weights: dict[float, int] = {}
    sample_pages = min(len(src), 50)

    for i in range(sample_pages):
        try:
            blocks = src[i].get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        except Exception:
            continue
        for block in blocks:
            if block.get("type", -1) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span.get("size", 0), 1)
                    text = span.get("text", "").strip()
                    if text and size > 0:
                        font_size_weights[size] = font_size_weights.get(size, 0) + len(text)

    if not font_size_weights:
        return None

    body_size = max(font_size_weights, key=font_size_weights.get)

    candidates = []
    for i in range(len(src)):
        try:
            blocks = src[i].get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        except Exception:
            continue
        page_items = []
        for block in blocks:
            if block.get("type", -1) != 0:
                continue
            for line in block.get("lines", []):
                text = ""
                max_size = 0.0
                bold = False
                for span in line.get("spans", []):
                    text += span.get("text", "")
                    max_size = max(max_size, span.get("size", 0))
                    if "bold" in span.get("font", "").lower():
                        bold = True

                text = text.strip()
                if len(text) < 2 or len(text) > 300:
                    continue

                if max_size > body_size + 0.5:
                    page_items.append({"text": text[:200], "size": round(max_size, 1), "bold": bold})
                elif bold and max_size >= body_size - 0.5 and len(text) < 100:
                    page_items.append({"text": text[:200], "size": round(max_size, 1), "bold": True})

        if page_items:
            candidates.append({"page": i + 1, "headings": page_items})

        if (i + 1) % 20 == 0:
            gc.collect()

    return candidates if candidates else None


def extract_page_texts(src, max_chars: int = 500) -> list[str]:
    """Extract truncated text from each page."""
    texts = []
    for i in range(len(src)):
        t = src[i].get_text("text").strip()
        if max_chars and len(t) > max_chars:
            t = t[:max_chars]
        texts.append(t)
        if (i + 1) % 20 == 0:
            gc.collect()
    return texts


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

RULES_COMMON = """\
- Return a JSON array: [{"level": 1, "title": "...", "page": N}, ...]
- level 1 = major divisions (Parts, Chapters), 2 = sections, 3 = subsections, 4+ = deeper
- Use numbering patterns (1, 1.1, 1.1.1 or I, A, etc.) to confirm hierarchy
- Clean titles: normalise whitespace, remove trailing dots / dashes / page numbers
- Discard running headers, footers, page numbers, figure captions
- Page numbers in output are 1-indexed (absolute)
- Return ONLY a valid JSON array — no markdown fences, no explanation"""


def build_prompt_candidates(candidates, total_pages, prev_headings=None):
    parts = [
        f"Analyse heading candidates extracted (by font size) from a {total_pages}-page PDF.",
        "Organise them into a hierarchical bookmark structure.\n",
        "Rules:",
        "- Use font size to determine hierarchy: larger size → higher level (lower number)",
        RULES_COMMON,
    ]
    if prev_headings:
        parts.append(f"\nPrevious pages contained these top-level headings: {prev_headings}")
    parts.append("\nCandidates:")
    for c in candidates:
        parts.append(f"\n--- Page {c['page']} ---")
        for h in c["headings"]:
            b = " [BOLD]" if h["bold"] else ""
            parts.append(f"  (size {h['size']}{b}) {h['text']}")
    return "\n".join(parts)


def build_prompt_text(page_texts, offset=0, total_pages=None, prev_headings=None):
    total = total_pages or len(page_texts)
    parts = [
        f"Analyse text from pages of a {total}-page PDF and identify the document structure.\n",
        "Rules:",
        "- Identify headings by: numbered sections, CAPITALISED lines, title-case short lines",
        "- If a Table of Contents page is present, use it as the primary source of truth",
        RULES_COMMON,
    ]
    if prev_headings:
        parts.append(f"\nPrevious pages contained these top-level headings: {prev_headings}")
    parts.append("\nPages:")
    for i, t in enumerate(page_texts):
        if t:
            parts.append(f"\n--- Page {offset + i + 1} ---\n{t}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# AI API calls
# ---------------------------------------------------------------------------

def call_ai(prompt: str, api_key: str, provider: str = "openai",
            model: str | None = None, base_url: str | None = None) -> str:
    """Call an LLM API and return the response text."""

    if provider == "anthropic":
        model = model or "claude-3-5-haiku-latest"
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 16384,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        model = model or "gpt-4o-mini"
        base = (base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)

    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read())

    if provider == "anthropic":
        return result["content"][0]["text"]
    return result["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_bookmarks(text: str, max_page: int) -> list[tuple[int, str, int]]:
    """Parse a JSON bookmark array from the AI response."""
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()

    # Try direct parse
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("["), text.rfind("]")
        if s != -1 and e != -1 and e > s:
            try:
                data = json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                pass

    if data is None:
        raise ValueError(f"No valid JSON in AI response:\n{text[:400]}")

    # Unwrap wrapper objects
    if isinstance(data, dict):
        for key in ("bookmarks", "toc", "outline", "items", "results"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break

    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")

    bookmarks = []
    for item in data:
        try:
            level = int(item.get("level", 1))
            title = str(item.get("title", "")).strip()
            page = int(item.get("page", 0))
        except (ValueError, AttributeError):
            continue
        if title and 1 <= page <= max_page and 1 <= level <= 6:
            bookmarks.append((level, title, page))

    return bookmarks


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def generate_bookmarks(
    input_path: str,
    api_key: str,
    provider: str = "openai",
    model: str | None = None,
    base_url: str | None = None,
    text_mode: bool = False,
    chars_per_page: int = 500,
    verbose: bool = False,
) -> list[tuple[int, str, int]]:
    """Generate bookmarks for a PDF using AI."""
    try:
        import fitz  # noqa: F811
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip3 install pymupdf", file=sys.stderr)
        sys.exit(1)

    src = fitz.open(input_path)
    total = len(src)

    if verbose:
        print(f"  Scanning {total} pages…", file=sys.stderr)

    # --- Strategy 1: font-size heading extraction (fast, cheap) ---
    if not text_mode:
        candidates = extract_heading_candidates(src)
        if candidates and len(candidates) >= 2:
            n = sum(len(c["headings"]) for c in candidates)
            if verbose:
                print(f"  {n} heading candidates on {len(candidates)} pages (font analysis)", file=sys.stderr)
            prompt = build_prompt_candidates(candidates, total)
            if verbose:
                est = len(prompt) // 4
                print(f"  Sending ~{est:,} tokens to {provider} ({model or 'default'})…", file=sys.stderr)
            resp = call_ai(prompt, api_key, provider, model, base_url)
            bookmarks = parse_bookmarks(resp, total)
            src.close()
            if verbose:
                print(f"  → {len(bookmarks)} bookmarks", file=sys.stderr)
            return bookmarks
        elif verbose:
            print("  Font analysis found too few candidates, falling back to text mode…", file=sys.stderr)

    # --- Strategy 2: raw text extraction (fallback) ---
    page_texts = extract_page_texts(src, max_chars=chars_per_page)
    src.close()

    text_pages = sum(1 for t in page_texts if t)
    if text_pages < max(2, int(total * 0.1)):
        print(
            f"  WARNING: only {text_pages}/{total} pages have extractable text — consider OCR first.",
            file=sys.stderr,
        )
        if text_pages == 0:
            return []

    all_bookmarks: list[tuple[int, str, int]] = []
    prev_headings: str | None = None

    for start in range(0, total, BATCH_PAGES):
        batch = page_texts[start : start + BATCH_PAGES]
        end = min(start + BATCH_PAGES, total)
        if verbose:
            print(f"  Text batch pages {start + 1}–{end}…", file=sys.stderr)

        prompt = build_prompt_text(batch, offset=start, total_pages=total, prev_headings=prev_headings)
        if verbose:
            est = len(prompt) // 4
            print(f"  Sending ~{est:,} tokens to {provider} ({model or 'default'})…", file=sys.stderr)

        resp = call_ai(prompt, api_key, provider, model, base_url)
        batch_bm = parse_bookmarks(resp, total)
        all_bookmarks.extend(batch_bm)

        top_level = [title for lvl, title, _ in batch_bm if lvl == 1]
        if top_level:
            prev_headings = ", ".join(top_level[-5:])

    if verbose:
        print(f"  → {len(all_bookmarks)} bookmarks total", file=sys.stderr)
    return all_bookmarks


# ---------------------------------------------------------------------------
# TOC page hyperlinks
# ---------------------------------------------------------------------------

def add_toc_links(doc, bookmarks, verbose: bool = False) -> int:
    """Find Contents/TOC pages and add clickable links from each entry to its target page."""
    import fitz

    if not bookmarks or len(doc) < 3:
        return 0

    scan_range = min(25, len(doc))
    links_added = 0

    for page_idx in range(scan_range):
        page = doc[page_idx]

        hits: list[tuple[fitz.Rect, int]] = []
        for _level, title, target_page in bookmarks:
            if len(title) < 4 or target_page - 1 == page_idx:
                continue

            search = title[:60].strip()
            rects = page.search_for(search)
            if not rects and len(search) > 20:
                rects = page.search_for(search[:25].strip())
            if rects:
                hits.append((rects[0], target_page))

        if len(hits) < 3:
            continue

        if verbose:
            print(f"  TOC page {page_idx + 1}: linking {len(hits)} entries", file=sys.stderr)

        pw = page.rect.width
        for rect, target_page in hits:
            link_rect = fitz.Rect(
                page.rect.x0 + 15,
                rect.y0 - 2,
                pw - 15,
                rect.y1 + 2,
            )
            page.insert_link({
                "kind": fitz.LINK_GOTO,
                "from": link_rect,
                "page": target_page - 1,
                "to": fitz.Point(0, 0),
                "border": [0, 0, 0],
            })
            links_added += 1

    return links_added


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_bookmarks(
    input_path: str,
    bookmarks: list[tuple[int, str, int]],
    in_place: bool = False,
    replace: bool = False,
    toc_links: bool = True,
    verbose: bool = False,
) -> str | None:
    """Write bookmarks into the PDF. Returns the output path, or None if skipped."""
    try:
        import fitz  # noqa: F811
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip3 install pymupdf", file=sys.stderr)
        sys.exit(1)

    input_path = os.path.abspath(input_path)
    doc = fitz.open(input_path)

    existing = doc.get_toc()
    if existing and not replace:
        doc.close()
        return None

    toc = [[lvl, title, page] for lvl, title, page in bookmarks]
    doc.set_toc(toc)

    if toc_links:
        n = add_toc_links(doc, bookmarks, verbose=verbose)
        if verbose and n:
            print(f"  {n} clickable links added to TOC page(s)", file=sys.stderr)

    if in_place:
        try:
            doc.saveIncr()
        except Exception:
            # Incremental save can fail on some PDFs; full-save via temp file
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                doc.save(tmp_path, garbage=4, deflate=True)
                doc.close()
                shutil.move(tmp_path, input_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            return input_path
        doc.close()
        return input_path

    # New file
    p = Path(input_path)
    output_path = str(p.parent / f"{p.stem}_bookmarked{p.suffix}")

    if len(doc) > 75:
        doc.save(output_path, garbage=4, deflate=False)
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            doc.save(tmp_path, garbage=4, deflate=True)
            shutil.move(tmp_path, output_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    doc.close()
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AI-powered PDF bookmark / outline generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s document.pdf
  %(prog)s --provider anthropic --model claude-sonnet-4-20250514 *.pdf
  %(prog)s --in-place --replace textbook.pdf
  %(prog)s --dry-run report.pdf
  %(prog)s --dir ~/PDFs -r
  %(prog)s --base-url http://localhost:11434/v1 --model llama3 book.pdf
  %(prog)s --base-url https://api.x.ai/v1 --model grok-3-mini-fast book.pdf
""",
    )
    parser.add_argument("files", nargs="*", help="PDF file(s) to process")
    parser.add_argument("--dir", metavar="DIR", help="process all PDFs in a directory")
    parser.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories (with --dir)")
    parser.add_argument("--api-key", metavar="KEY", help="AI API key (or set OPENAI_API_KEY / ANTHROPIC_API_KEY)")
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"],
                        help="AI provider (default: openai)")
    parser.add_argument("--model", metavar="NAME", help="model name (default: gpt-4o-mini / claude-3-5-haiku)")
    parser.add_argument("--base-url", metavar="URL", help="custom API base URL (Ollama, xAI, LM Studio, …)")
    parser.add_argument("--in-place", action="store_true", help="modify PDF in place (default: create _bookmarked copy)")
    parser.add_argument("--replace", action="store_true", help="replace existing bookmarks")
    parser.add_argument("--dry-run", action="store_true", help="print bookmarks without writing")
    parser.add_argument("--no-toc-links", action="store_true", help="skip adding clickable links on Contents pages")
    parser.add_argument("--text-mode", action="store_true", help="skip font analysis, use raw text extraction")
    parser.add_argument("--chars", type=int, default=500, metavar="N", help="max chars per page in text mode (default: 500)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show progress details")
    args = parser.parse_args()

    # -- Collect PDF paths --
    pdfs: list[str] = []
    for f in args.files or []:
        f = f.strip()
        if f and f.lower().endswith(".pdf"):
            pdfs.append(os.path.abspath(f))

    if args.dir:
        d = os.path.expanduser(args.dir)
        if not os.path.isdir(d):
            print(f"ERROR: not a directory: {d}", file=sys.stderr)
            sys.exit(1)
        if args.recursive:
            for root, _, files in os.walk(d):
                for f in sorted(files):
                    if f.lower().endswith(".pdf"):
                        pdfs.append(os.path.join(root, f))
        else:
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".pdf"):
                    pdfs.append(os.path.join(d, f))

    if not pdfs:
        parser.print_help()
        sys.exit(1)

    # -- Resolve API key --
    api_key = args.api_key
    if not api_key:
        env_var = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY"
        api_key = os.environ.get(env_var)
    if not api_key:
        env_var = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY"
        print(f"ERROR: No API key. Use --api-key or set {env_var}", file=sys.stderr)
        sys.exit(1)

    # -- Process --
    try:
        import fitz  # noqa: F811
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip3 install pymupdf", file=sys.stderr)
        sys.exit(1)

    done = skipped = failed = 0

    for idx, fp in enumerate(pdfs):
        name = os.path.basename(fp)
        prefix = f"[{idx + 1}/{len(pdfs)}] " if len(pdfs) > 1 else ""
        print(f"{prefix}{name}", file=sys.stderr)

        if not os.path.isfile(fp):
            print("  ERROR: file not found", file=sys.stderr)
            failed += 1
            continue
        if os.path.getsize(fp) == 0:
            print("  ERROR: empty file", file=sys.stderr)
            failed += 1
            continue

        # Skip early if bookmarks already exist (saves an API call)
        if not args.replace and not args.dry_run:
            doc = fitz.open(fp)
            existing = doc.get_toc()
            doc.close()
            if existing:
                print(f"  SKIP: already has {len(existing)} bookmarks (use --replace to overwrite)", file=sys.stderr)
                skipped += 1
                continue

        try:
            bookmarks = generate_bookmarks(
                fp, api_key,
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                text_mode=args.text_mode,
                chars_per_page=args.chars,
                verbose=args.verbose,
            )

            if not bookmarks:
                print("  No bookmarks generated", file=sys.stderr)
                skipped += 1
                continue

            if args.dry_run:
                print(f"\n{'=' * 60}")
                print(f"  Bookmarks for: {name}")
                print(f"{'=' * 60}")
                for lvl, title, page in bookmarks:
                    indent = "  " * (lvl - 1)
                    print(f"  {indent}{title}  (p.{page})")
                print()
                done += 1
            else:
                out = write_bookmarks(
                    fp, bookmarks,
                    in_place=args.in_place,
                    replace=args.replace,
                    toc_links=not args.no_toc_links,
                    verbose=args.verbose,
                )
                if out:
                    print(out)
                    done += 1
                else:
                    print(f"  SKIP: has bookmarks (use --replace)", file=sys.stderr)
                    skipped += 1

        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            print(f"  ERROR: API returned {e.code}: {body}", file=sys.stderr)
            failed += 1
            if len(pdfs) == 1:
                sys.exit(1)
        except urllib.error.URLError as e:
            print(f"  ERROR: network error: {e.reason}", file=sys.stderr)
            failed += 1
            if len(pdfs) == 1:
                sys.exit(1)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            failed += 1
            if len(pdfs) == 1:
                sys.exit(1)

    if len(pdfs) > 1:
        print(f"\nDone: {done} bookmarked, {skipped} skipped, {failed} failed", file=sys.stderr)


if __name__ == "__main__":
    main()
