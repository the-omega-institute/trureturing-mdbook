#!/usr/bin/env python3
"""Extract native rendered titles into a snapshot-qualified presentation map."""

from __future__ import annotations

import argparse
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

try:
    from site_freshness import generator_revision, provenance_is_usable
except ModuleNotFoundError:
    from scripts.site_freshness import generator_revision, provenance_is_usable


def title_identity(sha: str, revision: str, built_at: str) -> str:
    return hashlib.sha256("\0".join((sha, revision, built_at)).encode()).hexdigest()


def map_filename(identity: str) -> str:
    return f"search-titles-{identity}.json"


def search_bootstrap(sha: str, revision: str, built_at: str) -> str:
    script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
    identity = title_identity(sha, revision, built_at)
    return script + f'\nstartSearchTitles("{identity}");'


class HeadEnded(Exception):
    pass


class NativeTitle(HTMLParser):
    # HTML title is RCDATA: literal markup is text. HTMLParser otherwise treats
    # it as tags. Collect raw text here, then decode character references once.
    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, "title")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.in_head = False
        self.in_title = False
        self.titles: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        elif tag == "title" and self.in_head:
            self.in_title = True
            self.parts = []

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "title" and self.in_title:
            self.titles.append(unescape("".join(self.parts)))
            self.in_title = False
        elif tag == "head":
            raise HeadEnded


def native_title(page: Path) -> str | None:
    parser = NativeTitle()
    with page.open(encoding="utf-8") as stream:
        try:
            while chunk := stream.read(8192):
                parser.feed(chunk)
            parser.close()
        except HeadEnded:
            pass
    if len(parser.titles) == 1 and parser.titles[0].strip():
        return parser.titles[0]
    return None


def generate(source: Path, book: Path) -> dict[str, object]:
    raw = (source / "provenance.json").read_bytes()
    provenance = json.loads(raw)
    if not provenance_is_usable(provenance):
        raise ValueError("invalid source provenance")
    if provenance["generator_revision"] != generator_revision():
        raise ValueError("provenance generator_revision does not match the build inputs")
    if source.resolve() == book.resolve():
        raise ValueError("title map belongs in the rendered book, not source")
    if (book / "provenance.json").read_bytes() != raw:
        raise ValueError("rendered provenance does not match the source snapshot")
    identity = title_identity(*(provenance[key] for key in
                                ("upstream_sha", "generator_revision", "built_at")))
    titles = {}
    for page in sorted(book.rglob("*.html")):
        if page.is_symlink() or not page.is_file() or not page.resolve().is_relative_to(book.resolve()):
            raise ValueError(f"HTML page is not a regular book file: {page}")
        title = native_title(page)
        if title is not None:
            titles[page.relative_to(book).as_posix()] = title
    value = {"version": 1, "identity": identity, "titles": titles}
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    destination = book / map_filename(identity)
    if destination.is_symlink():
        raise ValueError("title map must not be a symlink")
    destination.write_bytes(content)
    return {"map": destination.name, "titles": len(titles), "bytes": len(content), "identity": identity}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("book", type=Path)
    args = parser.parse_args(argv)
    try:
        result = generate(args.source, args.book)
    except (OSError, ValueError) as exc:
        print(f"search-titles: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
