#!/usr/bin/env python3
"""Link every published page back to its upstream source on GitHub.

Runs after mdBook. Each page projected from an upstream Markdown file gets a source
panel at the end of its main content: the file at the captured snapshot, its history
and blame at that snapshot, the file on ``dev``, and the newest and oldest upstream
commits that touched it. Inline code that is exactly a Lean declaration ID, such as
``D5/S0/Certificates/Example.result``, becomes a link to its module's ``.lean`` file at
the snapshot when that exact regular file exists.

Every inserted URL is absolute, so the release gate's relative-resource check is
unaffected, and the panel is excluded from the search index. Copied upstream Markdown
never changes; in the HTML only the panel and the anchors around linked code are added.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote_from_bytes

try:
    from open_problems import GID_RE
    from render_source_links import (
        UPSTREAM_REPOSITORY, SourceLinkError, captured_sha, regular_source_paths,
    )
    from source_tree import PUBLISHED_ROOTS, is_published_path
except ModuleNotFoundError:
    from scripts.open_problems import GID_RE
    from scripts.render_source_links import (
        UPSTREAM_REPOSITORY, SourceLinkError, captured_sha, regular_source_paths,
    )
    from scripts.source_tree import PUBLISHED_ROOTS, is_published_path


UPSTREAM_BRANCH = "dev"
PANEL_MARKER = '<aside class="upstream-source"'
COMMIT_RE = re.compile(rb"([0-9a-f]{40,64})\x1f([0-9]{4}-[0-9]{2}-[0-9]{2})\x1f([^\x1f]*)\x1f(.*)", re.S)
# Cheap test before parsing a page: mdBook renders inline code as a bare <code> element.
DECLARATION_HINT = re.compile(r"<code>D[0-9]+/S[0-9]+/")


class PageSourceError(RuntimeError):
    """The pages cannot be linked to a valid immutable snapshot."""


@dataclass(frozen=True)
class Change:
    sha: str
    date: str
    author: str
    subject: str


@dataclass(frozen=True)
class FileHistory:
    newest: Change
    oldest: Change


def file_history(upstream: Path, sha: str) -> dict[bytes, FileHistory]:
    """The newest and oldest mainline commit that changed each published path, in one pass.

    Like the changelog, this follows first parents and compares each commit, merges
    included, with its first parent: the newest entry is exactly the commit at which the
    snapshot's content of the file last changed on the mainline, usually a pull request
    merge. Side-branch commits whose change a merge discarded never appear. Tree
    comparisons with rename detection off need no blobs, which the partial CI clone lacks.
    """
    try:
        raw = subprocess.run(
            ["git", "-C", os.fspath(upstream), "log", "-z", "--no-renames", "--name-only",
             "--first-parent", "--diff-merges=first-parent",
             "--format=%x1e%H%x1f%cs%x1f%an%x1f%s", sha, "--",
             *(os.fsdecode(root) + "/" for root in PUBLISHED_ROOTS)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise PageSourceError(exc.stderr.decode("utf-8", "replace").strip()) from exc
    newest: dict[bytes, Change] = {}
    oldest: dict[bytes, Change] = {}
    change: Change | None = None
    # Every path starts with a published root, so only a commit header starts with \x1e.
    for token in raw.split(b"\0"):
        if token.startswith(b"\x1e"):
            match = COMMIT_RE.fullmatch(token[1:])
            if match is None:
                raise PageSourceError("git log returned malformed commit metadata")
            change = Change(*(field.decode("utf-8", "replace") for field in match.groups()))
            continue
        path = token.removeprefix(b"\n")
        if change is not None and is_published_path(path):
            newest.setdefault(path, change)
            oldest[path] = change
    return {path: FileHistory(newest[path], oldest[path]) for path in newest}


def text(value: str) -> str:
    # `$$` left in a page is reported as unrendered math by the release gate.
    return escape(value).replace("$", "&#36;")


def link(url: str, label: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{label}</a>'


def commit_link(change: Change) -> str:
    return link(f"{UPSTREAM_REPOSITORY}/commit/{change.sha}", f"<code>{change.sha[:7]}</code>")


def source_panel(path: bytes, sha: str, history: FileHistory | None) -> str:
    quoted = quote_from_bytes(path, safe="/")
    shown = text(path.decode("utf-8", "replace"))
    lines = [
        f'{PANEL_MARKER} data-pagefind-ignore="all" aria-label="Source on GitHub" '
        'style="margin-top:3em;font-size:0.875em">',
        "<hr>",
        "<p><strong>Source on GitHub:</strong> "
        + link(f"{UPSTREAM_REPOSITORY}/blob/{sha}/{quoted}", f"<code>{shown}</code>")
        + " at snapshot "
        + link(f"{UPSTREAM_REPOSITORY}/commit/{sha}", f"<code>{sha[:7]}</code>") + "</p>",
    ]
    if history is not None:
        newest, oldest = history.newest, history.oldest
        added = ("" if oldest == newest else
                 f' · first added <time datetime="{oldest.date}">{oldest.date}</time> in {commit_link(oldest)}')
        lines.append(
            f'<p>Last changed <time datetime="{newest.date}">{newest.date}</time> by {text(newest.author)} '
            f"in {commit_link(newest)}: {text(newest.subject)}{added}</p>"
        )
    lines.extend([
        "<p>" + " · ".join((
            link(f"{UPSTREAM_REPOSITORY}/commits/{sha}/{quoted}", "History"),
            link(f"{UPSTREAM_REPOSITORY}/blame/{sha}/{quoted}", "Blame"),
            link(f"{UPSTREAM_REPOSITORY}/blob/{UPSTREAM_BRANCH}/{quoted}", f"Latest on <code>{UPSTREAM_BRANCH}</code>"),
            link(f"{UPSTREAM_REPOSITORY}/edit/{UPSTREAM_BRANCH}/{quoted}", "Edit on GitHub"),
        )) + "</p>",
        "</aside>",
    ])
    return "\n".join(lines) + "\n"


class DeclarationFinder(HTMLParser):
    """Inline ``<code>`` elements whose whole text is a declaration ID, outside links and ``<pre>``."""

    CDATA_CONTENT_ELEMENTS = HTMLParser.CDATA_CONTENT_ELEMENTS + ("textarea", "title")
    BLOCKING = {"a", "pre", "textarea", "title"}

    def __init__(self, content: str):
        super().__init__(convert_charrefs=False)
        self.content = content
        self.line_offsets = [0] + [match.end() for match in re.finditer("\n", content)]
        self.blocking = 0
        self.code: tuple[int, list[str]] | None = None
        self.found: list[tuple[int, int, str]] = []

    def position(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "code" and not attrs and not self.blocking and self.get_starttag_text() == "<code>":
            self.code = (self.position(), [])
            return
        self.code = None  # markup inside the code element: not a bare ID
        if tag in self.BLOCKING:
            self.blocking += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.code = None

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKING:
            self.blocking = max(0, self.blocking - 1)
        if tag == "code" and self.code is not None:
            start, parts = self.code
            end = self.position()
            identifier = "".join(parts)
            if self.content.startswith("</code>", end) and GID_RE.fullmatch(identifier):
                self.found.append((start, end + len("</code>"), identifier))
        self.code = None

    def handle_data(self, data: str) -> None:
        if self.code is not None:
            self.code[1].append(data)

    def unknown(self, *_args: object) -> None:
        self.code = None

    handle_entityref = handle_charref = handle_comment = handle_decl = handle_pi = unknown


def link_declarations(content: str, paths: set[bytes], sha: str) -> tuple[str, int]:
    """Wrap declaration IDs in ``<main>`` with links to their Lean module at ``sha``."""
    start = content.find("<main>")
    end = content.rfind("</main>")
    if start < 0 or end < start or not DECLARATION_HINT.search(content, start, end):
        return content, 0
    finder = DeclarationFinder(content[start:end])
    finder.feed(finder.content)
    finder.close()
    replacements = []
    for code_start, code_end, identifier in finder.found:
        module = identifier.split(".", 1)[0] + ".lean"
        if module.encode("ascii") in paths:
            url = escape(f"{UPSTREAM_REPOSITORY}/blob/{sha}/{module}", quote=True)
            replacements.append((start + code_start, start + code_end, url))
    for code_start, code_end, url in reversed(replacements):
        content = (content[:code_start] + f'<a href="{url}" title="Lean source on GitHub">'
                   + content[code_start:code_end] + "</a>" + content[code_end:])
    return content, len(replacements)


def render_page_sources(upstream: Path, source: Path, book: Path) -> dict[str, object]:
    try:
        sha = captured_sha(source, book)
        paths = regular_source_paths(upstream, sha)
    except SourceLinkError as exc:
        raise PageSourceError(str(exc)) from exc
    history = file_history(upstream, sha)
    published = {path for path in paths if is_published_path(path)}
    panels, linked = 0, 0
    for page in sorted(book.rglob("*.html")):
        if page.is_symlink() or not page.is_file():
            raise PageSourceError(f"HTML page is not a regular file: {page}")
        relative = os.fsencode(page.relative_to(book).as_posix())
        original = page.read_bytes().decode("utf-8")
        content, count = link_declarations(original, paths, sha)
        linked += count
        markdown = relative[:-5] + b".md"
        if markdown in published and PANEL_MARKER not in content:
            end = content.rfind("</main>")
            if end < 0:
                raise PageSourceError(f"published page has no <main> element: {page}")
            content = content[:end] + source_panel(markdown, sha, history.get(markdown)) + content[end:]
            panels += 1
        if content != original:
            page.write_bytes(content.encode("utf-8"))
    return {"upstream_sha": sha, "source_panels": panels, "linked_declarations": linked,
            "pages_without_history": len(published - set(history))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("book", type=Path)
    args = parser.parse_args(argv)
    try:
        result = render_page_sources(args.upstream, args.source, args.book)
    except (PageSourceError, OSError, ValueError) as exc:
        print(f"render-page-sources: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
