#!/usr/bin/env python3
"""mdBook preprocessor: check every formula with the pinned KaTeX package under Node and
leave its TeX source for the browser to render.

Pre-rendered KaTeX HTML averaged about 2.9 KB per formula, more than half of a Pages
artifact that GitHub caps at 1 GB; the TeX source is about a twenty-fifth of that. Every
formula is still rendered once here, in one Node process with the options the page uses
(scripts/katex_page.js), so the build decides which formulas render and its log names
those that do not. The spans come from ``math_scan`` — the same scanner the verifier
counts with. This replaced mdbook-katex, whose embedded QuickJS runtime overflows its
256 KiB stack on deeply nested formulas.

A formula that renders becomes ``<span class="katex-source">`` holding its TeX. A page
with one loads the KaTeX stylesheet, ``katex.min.js`` and ``katex-page.js`` from the
book's ``katex/`` directory (build-site.py copies them there from the pinned package),
which render every such span as the page loads.

A formula KaTeX cannot render does not stop the site: it is shown as its TeX source in
a ``katex-fallback`` code element, which the verifier counts in place of a source element,
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
RENDERER = Path(__file__).resolve().with_name("katex_render.js")
PAGE_SCRIPT_SOURCE = RENDERER.with_name("katex_page.js")
KATEX_DIST = RENDERER.parent.parent / "node_modules" / "katex" / "dist"
# The book directory build-site.py publishes the assets under, and their names in it.
ASSET_DIRECTORY = "katex"
STYLESHEET = "katex.min.css"
LIBRARY = "katex.min.js"
PAGE_SCRIPT = "katex-page.js"
SOURCE_CLASS = "katex-source"
FALLBACK_CLASS = "katex-fallback"


class RenderError(RuntimeError):
    """A formula did not render, or the renderer could not run."""


def katex_assets() -> dict[str, Path]:
    """Each file published under ``ASSET_DIRECTORY``, mapped to its source: the pinned
    package's stylesheet, library and fonts (the stylesheet names them), and the page
    script."""
    if not (KATEX_DIST / LIBRARY).is_file():
        raise RenderError("the pinned katex package is not installed; run `npm ci` in the repository root")
    assets = {
        STYLESHEET: KATEX_DIST / STYLESHEET,
        LIBRARY: KATEX_DIST / LIBRARY,
        PAGE_SCRIPT: PAGE_SCRIPT_SOURCE,
    }
    for font in sorted((KATEX_DIST / "fonts").iterdir()):
        if font.is_file():
            assets[f"fonts/{font.name}"] = font
    return assets


def asset_header(chapter_path: str | None) -> str:
    """The KaTeX assets for a chapter's page, relative to it as mdBook's own links are.

    One HTML block: Markdown ends it at the blank line. Deferred scripts run in order
    once the page is parsed.
    """
    root = "../" * (chapter_path or "").count("/") + ASSET_DIRECTORY + "/"
    return (
        f'<link rel="stylesheet" href="{root}{STYLESHEET}">\n'
        f'<script defer src="{root}{LIBRARY}"></script>\n'
        f'<script defer src="{root}{PAGE_SCRIPT}"></script>\n\n'
    )


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


def formula_texts(content: bytes) -> list[tuple[str, bool]]:
    """Each formula's TeX between its delimiters, and whether it is display math."""
    formulas: list[tuple[str, bool]] = []
    for start, end, display in math_spans(content):
        width = 2 if display or content[start:start + 2] == b"$`" else 1
        formulas.append((content[start + width:end - width].decode("utf-8"), display))
    return formulas


def inert_text(tex: str, *, keep_lines: bool) -> str:
    """TeX as one line of text that Markdown passes through unchanged.

    mdBook parses Markdown after this preprocessor, and text between raw HTML tags is
    still Markdown, so every ASCII punctuation character is written as a numeric
    character reference; references are decoded, never re-parsed as Markdown, and the
    page holds the decoded text. ``keep_lines`` writes line breaks as references too, so
    a ``%`` comment still ends at its line; otherwise they become spaces.
    """
    if keep_lines:
        tex = tex.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        (f"&#{ord(character)};" if keep_lines else " ") if character in "\n\t"
        else " " if character == "\r"
        else f"&#{ord(character)};" if character.isascii() and not character.isalnum() and character != " "
        else character
        for character in tex
    )


def source_html(tex: str, display: bool, lenient: bool) -> str:
    """A formula KaTeX rendered at build time, as the TeX the page script renders."""
    attributes = (' data-display="true"' if display else "") + (' data-lenient="true"' if lenient else "")
    return f'<span class="{SOURCE_CLASS}"{attributes}>{inert_text(tex, keep_lines=True)}</span>'


def fallback_html(tex: str, display: bool) -> str:
    """A formula KaTeX cannot render, shown as its TeX source in inert HTML."""
    attribute = ' data-display="true"' if display else ""
    return f'<code class="{FALLBACK_CLASS}"{attribute}>{inert_text(tex, keep_lines=False)}</code>'


def warning_line(where: str, formula_index: int, message: str) -> str:
    return workflow_warning("KaTeX fallback", f"{where}: formula {formula_index}: {message}")


def render_formulas(
    formulas: list[tuple[str, bool]],
) -> tuple[list[tuple[str, bool] | None], list[dict[str, Any]], int]:
    """Return, per formula, the TeX that renders and whether it needs lenient mode
    (``None`` where KaTeX could not render it), the failures, and how many formulas
    render with an unknown macro's name shown in red."""
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
    checked: list[tuple[str, bool] | None] = []
    failures: list[dict[str, Any]] = []
    lenient = 0
    for index, line in enumerate(lines):
        tex = line.get("tex") if isinstance(line, dict) else None
        if isinstance(tex, str) and "failure" not in line:
            checked.append((tex, line.get("lenient") is True))
            lenient += line.get("lenient") is True
        elif tex is None and isinstance(line, dict) and "failure" in line:
            checked.append(None)
            failures.append({"index": index, "message": str(line["failure"])})
        else:
            raise RenderError("renderer returned inconsistent failures")
    return checked, failures, lenient


def render_chapter(content: str, replacements: list[str], header: str) -> str:
    """Splice ``replacements`` over the spans of ``content`` (in span order) and prepend
    ``header``."""
    raw = content.encode("utf-8")
    pieces: list[bytes] = []
    cursor = 0
    for (start, end, _display), html in zip(math_spans(raw), replacements, strict=True):
        pieces.append(raw[cursor:start])
        # One line of HTML, so no blank line can end the HTML block early.
        pieces.append(html.replace("\n", " ").encode("utf-8"))
        cursor = end
    pieces.append(raw[cursor:])
    return header + b"".join(pieces).decode("utf-8")


def preprocess_book(book: dict[str, Any]) -> dict[str, Any]:
    found = chapters(book)
    formulas: list[tuple[str, bool]] = []
    owners: list[tuple[int, int]] = []
    counts: list[int] = []
    for chapter_index, chapter in enumerate(found):
        texts = formula_texts(chapter["content"].encode("utf-8"))
        counts.append(len(texts))
        formulas.extend(texts)
        owners.extend((chapter_index, formula_index) for formula_index in range(len(texts)))
    try:
        checked, failures, lenient = render_formulas(formulas)
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
        chapter_index, formula_index = owners[failure["index"]]
        chapter = found[chapter_index]
        where = chapter.get("path") or chapter.get("name") or f"chapter {chapter_index}"
        print(warning_line(where, formula_index, str(failure.get("message", ""))), file=sys.stderr)
    if lenient:
        # A paper-local macro is not a defect of the page; the formula renders around it.
        print(f"render-katex: {lenient} formulas show an undefined macro's name in red",
              file=sys.stderr)
    position = 0
    for chapter, count in zip(found, counts, strict=True):
        replacements = [
            fallback_html(*formulas[index]) if result is None
            else source_html(result[0], formulas[index][1], result[1])
            for index, result in enumerate(checked[position:position + count], start=position)
        ]
        header = asset_header(chapter.get("path")) if any(result is not None for result in
                                                          checked[position:position + count]) else ""
        chapter["content"] = render_chapter(chapter["content"], replacements, header)
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
