#!/usr/bin/env python3
"""Render links to unpublished upstream files as immutable GitHub blob URLs."""

from __future__ import annotations

import argparse
from html import escape, unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
from urllib.parse import quote_from_bytes, unquote_to_bytes, urlsplit

try:
    from site_freshness import generator_revision, provenance_is_usable
    from source_tree import PUBLISHED_MODES, is_published_path
except ModuleNotFoundError:
    from scripts.site_freshness import generator_revision, provenance_is_usable
    from scripts.source_tree import PUBLISHED_MODES, is_published_path


UPSTREAM_REPOSITORY = "https://github.com/the-omega-institute/trureturing"
# Match whole attributes, so text such as title='an href="..." example' is opaque.
ATTRIBUTE_RE = re.compile(r'''([^\s=/>]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?''')
BAD_PERCENT_RE = re.compile(r"%(?![0-9a-fA-F]{2})")


class SourceLinkError(RuntimeError):
    """A source link cannot be rendered from a valid immutable snapshot."""


def regular_source_paths(upstream: Path, sha: str) -> set[bytes]:
    try:
        raw = subprocess.run(
            ["git", "-C", os.fspath(upstream), "ls-tree", "-r", "-z", sha],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise SourceLinkError(exc.stderr.decode("utf-8", "replace").strip()) from exc
    paths = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, kind, _oid = metadata.split(b" ", 2)
        if mode in PUBLISHED_MODES and kind == b"blob":
            paths.add(path)
    return paths


def source_permalink(
    value: str, page: bytes, book: Path, paths: set[bytes], sha: str,
) -> str | None:
    value = value.strip()
    if not value or value.startswith(("#", "//", "/")):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise SourceLinkError(f"malformed anchor in {os.fsdecode(page)}: {value}") from exc
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    raw_path = unquote_to_bytes(parsed.path)
    if (BAD_PERCENT_RE.search(parsed.path) or b"\\" in raw_path
            or raw_path.startswith(b"/")
            or any(byte < 32 or byte == 127 for byte in raw_path)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SourceLinkError(f"malformed relative anchor in {os.fsdecode(page)}: {value}")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(page), raw_path))
    if target == b".." or target.startswith(b"../"):
        raise SourceLinkError(f"relative anchor escapes site in {os.fsdecode(page)}: {value}")

    # Preserve local resources and published-page navigation, even if a missing
    # page happens to share a path with an upstream HTML blob. The gate owns
    # missing resources. Do not guess extensions or reinterpret nonexistent paths.
    published_page = target[:-5] + b".md" if target.endswith(b".html") else target
    if ((published_page in paths and is_published_path(published_page))
            or (book / os.fsdecode(target)).exists()
            or raw_path.endswith(b"/") or target not in paths):
        return None
    suffix = value[len(parsed.path):]
    return f"{UPSTREAM_REPOSITORY}/blob/{sha}/{quote_from_bytes(target, safe='/')}{suffix}"


class AnchorRenderer(HTMLParser):
    # In these elements, apparent tags are displayed as text, not anchors.
    CDATA_CONTENT_ELEMENTS = HTMLParser.CDATA_CONTENT_ELEMENTS + ("textarea", "title")

    def __init__(self, content: str, page: bytes, book: Path, paths: set[bytes], sha: str):
        super().__init__(convert_charrefs=False)
        self.content, self.page, self.book = content, page, book
        self.paths, self.sha = paths, sha
        self.line_offsets = [0] + [match.end() for match in re.finditer("\n", content)]
        self.replacements: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        raw = self.get_starttag_text()
        attributes = list(ATTRIBUTE_RE.finditer(raw))
        hrefs = [attr for attr in attributes if attr.group(1).lower() == "href"]
        if len(hrefs) > 1:
            raise SourceLinkError(f"duplicate anchor href in {os.fsdecode(self.page)}")
        if not hrefs or hrefs[0].group(2) is None:
            return
        attr = hrefs[0]
        value = attr.group(2)
        if value.startswith(('"', "'")):
            value = value[1:-1]
        replacement = source_permalink(unescape(value), self.page, self.book, self.paths, self.sha)
        if replacement is not None:
            line, column = self.getpos()
            start = self.line_offsets[line - 1] + column
            self.replacements.append((
                start + attr.start(2), start + attr.end(2), f'"{escape(replacement, quote=True)}"',
            ))

    def render(self) -> str:
        self.feed(self.content)
        self.close()
        content = self.content
        for start, end, replacement in reversed(self.replacements):
            content = content[:start] + replacement + content[end:]
        return content


def render_source_links(upstream: Path, source: Path, book: Path) -> dict[str, object]:
    provenance = json.loads((source / "provenance.json").read_bytes())
    if not provenance_is_usable(provenance):
        raise SourceLinkError("invalid source provenance")
    if provenance["generator_revision"] != generator_revision():
        raise SourceLinkError("provenance generator_revision does not match the build inputs")
    if (book / "provenance.json").read_bytes() != (source / "provenance.json").read_bytes():
        raise SourceLinkError("rendered provenance does not match the source snapshot")
    sha = provenance["upstream_sha"]
    paths = regular_source_paths(upstream, sha)
    rewritten, changed_pages = 0, []
    for page in sorted(book.rglob("*.html")):
        if page.is_symlink() or not page.is_file():
            raise SourceLinkError(f"HTML page is not a regular file: {page}")
        relative = page.relative_to(book).as_posix()
        content = page.read_bytes().decode("utf-8")
        renderer = AnchorRenderer(content, os.fsencode(relative), book, paths, sha)
        rendered = renderer.render()
        if renderer.replacements:
            # Splice only attribute values; preserve all other HTML bytes,
            # including prose, code, KaTeX, comments and line endings.
            page.write_bytes(rendered.encode("utf-8"))
            rewritten += len(renderer.replacements)
            changed_pages.append(relative)
    return {"upstream_sha": sha, "rewritten_anchors": rewritten, "changed_pages": changed_pages}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("book", type=Path)
    args = parser.parse_args(argv)
    try:
        result = render_source_links(args.upstream, args.source, args.book)
    except (SourceLinkError, OSError, ValueError) as exc:
        print(f"render-source-links: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
