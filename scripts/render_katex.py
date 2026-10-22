#!/usr/bin/env python3
"""mdBook preprocessor: render every formula with the pinned KaTeX package under Node.

It replaces mdbook-katex, whose embedded QuickJS runtime overflows its 256 KiB stack
on deeply nested formulas and then keeps the source text, which the release gate
refuses. The spans come from ``math_scan`` — the same scanner the verifier counts
with — and every formula is rendered in one Node process.

A formula KaTeX cannot render does not stop the site: it is shown as its TeX source in
a ``katex-fallback`` code element, which the verifier counts in place of a KaTeX node,
and the build log carries a GitHub warning naming the chapter and formula. A renderer
process that cannot run or answers inconsistently still fails the build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from build_warnings import workflow_warning
    from math_scan import math_spans
except ModuleNotFoundError:
    from scripts.build_warnings import workflow_warning
    from scripts.math_scan import math_spans

KATEX_VERSION = "0.16.4"
# One stylesheet link per chapter, as mdbook-katex emitted it.
STYLESHEET_HEADER = (
    f'<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.css">\n\n'
)
RENDERER = Path(__file__).resolve().with_name("katex_render.js")
FALLBACK_CLASS = "katex-fallback"


class RenderError(RuntimeError):
    """A formula did not render, or the renderer could not run."""


def chapters(book: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            chapter = item.get("Chapter") if isinstance(item, dict) else None
            if isinstance(chapter, dict):
                if isinstance(chapter.get("content"), str):
                    found.append(chapter)
                visit(chapter.get("sub_items"))

    visit(book.get("items"))
    return found


def fallback_html(tex: str, display: bool) -> str:
    """The TeX source as one line of inert HTML.

    mdBook parses Markdown after this preprocessor, and text between raw HTML tags is
    still Markdown, so every ASCII punctuation character is written as a numeric
    character reference; references are decoded, never re-parsed as Markdown.
    """
    text = "".join(
        " " if character in "\r\n\t"
        else f"&#{ord(character)};" if character.isascii() and not character.isalnum() and character != " "
        else character
        for character in tex
    )
    attribute = ' data-display="true"' if display else ""
    return f'<code class="{FALLBACK_CLASS}"{attribute}>{text}</code>'


def warning_line(where: str, formula_index: int, message: str) -> str:
    return workflow_warning("KaTeX fallback", f"{where}: formula {formula_index}: {message}")


def render_formulas(
    formulas: list[tuple[str, bool]],
) -> tuple[list[str | None], list[dict[str, Any]], int]:
    """Return KaTeX HTML per formula (``None`` where KaTeX could not render it), the
    failures, and how many formulas render with an unknown macro's name shown in red."""
    if not formulas:
        return [], [], 0
    payload = json.dumps([{"tex": tex, "display": display} for tex, display in formulas])
    try:
        result = subprocess.run(
            ["node", os.fspath(RENDERER)], input=payload, capture_output=True, text=True,
            cwd=RENDERER.parent.parent,
        )
    except OSError as exc:
        raise RenderError(f"cannot start node: {exc}") from exc
    if result.returncode != 0:
        raise RenderError(result.stderr.strip() or f"node exited with {result.returncode}")
    # Split on "\n" only: JSON leaves U+2028 and other line separators unescaped.
    try:
        header, *lines = (json.loads(line) for line in result.stdout.split("\n")[:-1])
    except ValueError as exc:  # JSONDecodeError, or no header line at all
        raise RenderError(f"renderer returned malformed output: {exc}") from None
    if not isinstance(header, dict):
        raise RenderError("renderer returned malformed output: no version header")
    if header.get("version") != KATEX_VERSION:
        raise RenderError(f"katex {header.get('version')!r} is installed; {KATEX_VERSION} is pinned")
    if len(lines) != len(formulas):
        raise RenderError("renderer returned a different number of formulas")
    html: list[str | None] = []
    failures: list[dict[str, Any]] = []
    lenient = 0
    for index, line in enumerate(lines):
        rendered = line.get("html") if isinstance(line, dict) else None
        if isinstance(rendered, str) and "failure" not in line:
            html.append(rendered)
            lenient += line.get("lenient") is True
        elif rendered is None and isinstance(line, dict) and "failure" in line:
            html.append(None)
            failures.append({"index": index, "message": str(line["failure"])})
        else:
            raise RenderError("renderer returned inconsistent failures")
    return html, failures, lenient


def render_chapter(content: str, rendered: list[str]) -> str:
    """Splice rendered HTML over the spans of ``content``; ``rendered`` is in span order."""
    raw = content.encode("utf-8")
    pieces: list[bytes] = []
    cursor = 0
    for (start, end, _display), html in zip(math_spans(raw), rendered, strict=True):
        pieces.append(raw[cursor:start])
        # One line of HTML, so no blank line can end the HTML block early.
        pieces.append(html.replace("\n", " ").encode("utf-8"))
        cursor = end
    pieces.append(raw[cursor:])
    return STYLESHEET_HEADER + b"".join(pieces).decode("utf-8")


def preprocess_book(book: dict[str, Any]) -> dict[str, Any]:
    found = chapters(book)
    formulas: list[tuple[str, bool]] = []
    owners: list[tuple[int, int]] = []
    counts: list[int] = []
    for chapter_index, chapter in enumerate(found):
        raw = chapter["content"].encode("utf-8")
        spans = math_spans(raw)
        counts.append(len(spans))
        for formula_index, (start, end, display) in enumerate(spans):
            width = 2 if display or raw[start:start + 2] == b"$`" else 1
            formulas.append((raw[start + width:end - width].decode("utf-8"), display))
            owners.append((chapter_index, formula_index))
    try:
        rendered, failures, lenient = render_formulas(formulas)
    except RenderError as exc:
        message = str(exc)
        prefix = "katex-render: formula "
        if message.startswith(prefix):
            index = int(message[len(prefix):].split(":", 1)[0])
            chapter_index, formula_index = owners[index]
            chapter = found[chapter_index]
            where = chapter.get("path") or chapter.get("name") or f"chapter {chapter_index}"
            message = f"{where}: formula {formula_index}: {message.split(':', 2)[2].strip()}"
        raise RenderError(message) from None
    for failure in failures:
        index = failure["index"]
        chapter_index, formula_index = owners[index]
        chapter = found[chapter_index]
        where = chapter.get("path") or chapter.get("name") or f"chapter {chapter_index}"
        print(warning_line(where, formula_index, str(failure.get("message", ""))), file=sys.stderr)
        rendered[index] = fallback_html(*formulas[index])
    if lenient:
        # A paper-local macro is not a defect of the page; the formula renders around it.
        print(f"render-katex: {lenient} formulas show an undefined macro's name in red",
              file=sys.stderr)
    position = 0
    for chapter, count in zip(found, counts, strict=True):
        chapter["content"] = render_chapter(chapter["content"], rendered[position:position + count])
        position += count
    return book


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["supports"]:
        return 0 if len(arguments) == 2 and arguments[1] == "html" else 1
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[1], dict):
            raise ValueError("expected mdBook [context, book] input")
        book = preprocess_book(payload[1])
    except (json.JSONDecodeError, OSError, ValueError, RenderError) as exc:
        print(f"render-katex: {exc}", file=sys.stderr)
        return 1
    json.dump(book, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
