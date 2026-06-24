"""HTML -> readable main text for extraction  [Cursor task P3-T01].

Implement `clean_html(html, url) -> CleanedPage` per SCRAPING.md §1 using trafilatura for
main-content/boilerplate stripping (Docling path only when PREFER_DOCLING is truthy). Returns
{url, title, text, anchors, chunks}. PURE + deterministic: no network, no DB, no clock, no script
execution, no link-following. HTML content (incl. any injected instructions) is treated as DATA.

Forbidden: importing clients/* or any HTTP lib; network/DB access.
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser

# Tags whose textual content must never reach the cleaned output / anchors.
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Deterministic chunk sizing for the downstream LLM. Paragraph-aligned where possible.
_MAX_CHUNK_CHARS = 1500


def use_docling() -> bool:
    return (os.getenv("PREFER_DOCLING") or "").strip().lower() in {"1", "true", "yes"}


def clean_html(html: str, url: str) -> dict:
    """Return a CleanedPage dict (SCRAPING.md §1). trafilatura main-content extraction; chunked text."""
    html = html or ""

    parsed = _parse_html(html)

    if use_docling():
        text = _docling_text(html) or _trafilatura_text(html)
    else:
        text = _trafilatura_text(html)
    text = _normalize_text(text)

    return {
        "url": url,
        "title": parsed["title"],
        "text": text,
        "anchors": parsed["anchors"],
        "chunks": _chunk_text(text),
    }


def _trafilatura_text(html: str) -> str:
    """Main-content text with boilerplate/scripts/nav stripped. Deterministic over the input."""
    if not html.strip():
        return ""

    import trafilatura  # declared dep; lazy so importing this module never hard-fails

    try:
        extracted = trafilatura.extract(
            html,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            include_links=True,   # preserve member/people links
            favor_recall=True,    # keep lists/members (favor_precision dropped them on lab pages)
        )
    except TypeError:
        # Older/newer trafilatura with a different kwarg surface — fall back to defaults.
        extracted = trafilatura.extract(html)

    return extracted or ""


def _docling_text(html: str) -> str | None:
    """Optional Docling path (PREFER_DOCLING). Best-effort + guarded: Docling is not a hard
    dependency, and it must convert the in-memory HTML only (no fetching). Returns None to fall
    back to trafilatura when Docling is unavailable or errors."""
    if not html.strip():
        return ""

    try:
        from io import BytesIO

        from docling.datamodel.base_models import DocumentStream
        from docling.document_converter import DocumentConverter
    except Exception:
        return None

    try:
        stream = DocumentStream(name="page.html", stream=BytesIO(html.encode("utf-8")))
        result = DocumentConverter().convert(stream)
        return result.document.export_to_markdown() or None
    except Exception:
        return None


class _PageParser(HTMLParser):
    """Pure, script-free pass over the HTML to collect the <title> and candidate anchors
    (headings + links) usable as a `source_anchor`. Never executes or follows anything."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._in_title = False
        self._capture: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _HEADING_TAGS:
            self._capture.append({"tag": tag, "selector": tag, "parts": []})
        elif tag == "a":
            href = next((v for k, v in attrs if k.lower() == "href"), None)
            selector = f'a[href="{href}"]' if href else "a"
            self._capture.append({"tag": tag, "selector": selector, "parts": []})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            return
        if (tag in _HEADING_TAGS or tag == "a") and self._capture:
            node = self._capture.pop()
            text = _collapse_ws("".join(node["parts"]))
            if text:
                self.anchors.append((text, node["selector"]))

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        for node in self._capture:
            node["parts"].append(data)


def _parse_html(html: str) -> dict:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed markup must not crash the pure transform; degrade to what we parsed so far.
        pass

    title = _collapse_ws("".join(parser.title_parts)) or None

    anchors: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for text, selector in parser.anchors:
        key = (text, selector)
        if key in seen:
            continue
        seen.add(key)
        anchors.append({"text": text, "selector": selector})

    return {"title": title, "anchors": anchors}


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    # Normalize line endings + trim trailing spaces per line; keep paragraph breaks for chunking.
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def _chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Deterministically split cleaned text into LLM-sized chunks, paragraph-aligned. A paragraph
    longer than `max_chars` is hard-split; chunks never overlap and depend only on the input."""
    text = text.strip()
    if not text:
        return []

    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start : start + max_chars])
            continue
        if not current:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= max_chars:
            current = f"{current}\n\n{paragraph}"
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks
