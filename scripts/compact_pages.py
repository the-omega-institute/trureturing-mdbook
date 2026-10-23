#!/usr/bin/env python3
"""Shrink mdBook's per-page template without changing what a page shows.

Every page repeats about 18 KB of template around its content, a fifth of the book.
Outside ``<main>`` this step replaces each inline Font Awesome icon with a reference
into one shared sprite, ``fa-icons.svg``, which carries the icons' license comment once;
drops the template's HTML comments except license comments (``<!--! …-->``); and removes
line indentation, keeping every line break. Content inside ``<main>`` is untouched, and
the inline scripts hold no string that spans a line.
"""

from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys


SPRITE = "fa-icons.svg"
ICON_RE = re.compile(
    r'<svg xmlns="http://www\.w3\.org/2000/svg" viewBox="([^"]*)">(<!--!.*?-->)?<path d="([^"]*)"/></svg>',
    re.S,
)
COMMENT_RE = re.compile(r"<!--(?!!).*?-->", re.S)
INDENT_RE = re.compile(r"\n[ \t]+")


class Sprite:
    def __init__(self) -> None:
        self.icons: dict[str, tuple[str, str]] = {}
        self.license: str | None = None

    def reference(self, match: re.Match[str], root: str) -> str:
        view_box, notice, path = match.groups()
        name = "i" + hashlib.sha256(f"{view_box}\0{path}".encode()).hexdigest()[:10]
        self.icons.setdefault(name, (view_box, path))
        self.license = self.license or notice
        return f'<svg viewBox="{view_box}"><use href="{root}{SPRITE}#{name}"/></svg>'

    def svg(self) -> str:
        symbols = "".join(
            f'<symbol id="{name}" viewBox="{escape(view_box)}"><path d="{escape(path)}"/></symbol>'
            for name, (view_box, path) in sorted(self.icons.items())
        )
        return f'<svg xmlns="http://www.w3.org/2000/svg">{self.license or ""}{symbols}</svg>\n'


def compact_template(region: str, root: str, sprite: Sprite) -> str:
    region = ICON_RE.sub(lambda match: sprite.reference(match, root), region)
    return INDENT_RE.sub("\n", COMMENT_RE.sub("", region))


def compact_page(content: str, root: str, sprite: Sprite) -> str:
    start, end = content.find("<main>"), content.rfind("</main>")
    if start < 0 or end < start:
        return content
    return (compact_template(content[:start], root, sprite) + content[start:end]
            + compact_template(content[end:], root, sprite))


def compact_pages(book: Path) -> dict[str, int]:
    sprite = Sprite()
    before = after = 0
    for page in sorted(book.rglob("*.html")):
        if page.is_symlink() or not page.is_file():
            raise OSError(f"HTML page is not a regular file: {page}")
        relative = page.relative_to(book).as_posix()
        original = page.read_bytes().decode("utf-8")
        compacted = compact_page(original, "../" * relative.count("/"), sprite)
        before += len(original.encode("utf-8"))
        after += len(compacted.encode("utf-8"))
        if compacted != original:
            page.write_bytes(compacted.encode("utf-8"))
    if sprite.icons:
        (book / SPRITE).write_text(sprite.svg(), encoding="utf-8")
    return {"html_bytes_before": before, "html_bytes_after": after, "icons": len(sprite.icons)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compact_pages(args.book)
    except (OSError, UnicodeError) as exc:
        print(f"compact-pages: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
