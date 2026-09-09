#!/usr/bin/env python3
"""Fail-closed verification gate for the generated mdBook site."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

try:
    from site_freshness import generator_revision, provenance_is_usable
    from open_problems import PAGE_PATH, OpenProblemError, derive_open_problems
    from source_tree import is_published_path, list_source_entries
except ModuleNotFoundError:
    from scripts.site_freshness import generator_revision, provenance_is_usable
    from scripts.open_problems import PAGE_PATH, OpenProblemError, derive_open_problems
    from scripts.source_tree import is_published_path, list_source_entries


SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
ARTIFACT_LIMIT_BYTES = 1_000_000_000
KATEX_MARKER = b'class="katex"'


class VerificationError(RuntimeError):
    """Raised when the generated site violates a release invariant."""


class ResourceCollector(HTMLParser):
    RESOURCE_ATTRIBUTES = {
        "a": "href",
        "img": "src",
        "link": "href",
        "script": "src",
        "source": "src",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute = self.RESOURCE_ATTRIBUTES.get(tag)
        if attribute is None:
            return
        for name, value in attrs:
            if name == attribute and value is not None:
                self.resources.append(value.strip())


def run_git(upstream: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(upstream), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise VerificationError(f"git command failed ({' '.join(args)}): {detail}") from exc


def load_provenance(source: Path) -> dict[str, object]:
    path = source / "provenance.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read valid provenance: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError("provenance must be a JSON object")
    sha = value.get("upstream_sha")
    file_count = value.get("file_count")
    tool_version = value.get("tool_version")
    built_at = value.get("built_at")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise VerificationError("provenance upstream_sha is invalid")
    if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count <= 0:
        raise VerificationError("provenance file_count is invalid")
    if not isinstance(tool_version, str) or not tool_version:
        raise VerificationError("provenance tool_version is invalid")
    if not isinstance(built_at, str) or not built_at:
        raise VerificationError("provenance built_at is invalid")
    if not provenance_is_usable(value):
        raise VerificationError("provenance generator_revision or built_at is invalid")
    if value["generator_revision"] != generator_revision():
        raise VerificationError("provenance generator_revision does not match the build inputs")
    return value


def expected_source_blobs(upstream: Path, sha: str) -> dict[bytes, bytes]:
    run_git(upstream, "cat-file", "-e", f"{sha}^{{commit}}")
    return {
        entry.path: entry.oid
        for entry in list_source_entries(upstream, sha, VerificationError)
    }


def expected_source_paths(upstream: Path, sha: str) -> set[bytes]:
    """Compatibility helper returning the publication path set."""
    return set(expected_source_blobs(upstream, sha))


def walk_regular_files(root: Path) -> list[bytes]:
    if not root.is_dir() or root.is_symlink():
        raise VerificationError(f"required directory is missing or unsafe: {root}")
    root_bytes = os.fsencode(root)
    files: list[bytes] = []
    for current, directories, names in os.walk(root_bytes, followlinks=False):
        for directory in directories:
            path = os.path.join(current, directory)
            if os.path.islink(path):
                raise VerificationError(f"symlink directory in output: {os.fsdecode(path)}")
        for name in names:
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                raise VerificationError(f"non-regular file in output: {os.fsdecode(path)}")
            relative = os.path.relpath(path, root_bytes).replace(os.sep.encode(), b"/")
            files.append(relative)
    return sorted(files)


def blob_oid(content: bytes) -> bytes:
    digest = hashlib.sha1()
    digest.update(b"blob ")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)
    return digest.hexdigest().encode("ascii")


def projected_source_blobs(source: Path) -> dict[bytes, bytes]:
    return {
        path: blob_oid(bytes_path(source, path).read_bytes())
        for path in walk_regular_files(source)
        if is_published_path(path)
    }


def projected_source_paths(source: Path) -> set[bytes]:
    """Compatibility helper returning the projected path set."""
    return set(projected_source_blobs(source))


def bytes_path(root: Path, relative: bytes) -> Path:
    return root / os.fsdecode(relative)


def unescaped_dollar_count(content: bytes) -> int:
    count = 0
    for index, value in enumerate(content):
        if value != ord("$"):
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and content[cursor] == ord("\\"):
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            count += 1
    return count


def _without_inline_code(line: bytes) -> bytes:
    result = bytearray()
    index = 0
    while index < len(line):
        if line[index] != ord("`"):
            result.append(line[index])
            index += 1
            continue
        end = index
        while end < len(line) and line[end] == ord("`"):
            end += 1
        fence = line[index:end]
        close = line.find(fence, end)
        if close < 0:
            return bytes(result)
        index = close + len(fence)
    return bytes(result)


def markdown_math_token_count(content: bytes) -> int:
    """Count paired dollar-delimited formulas outside Markdown code fences."""
    count = 0
    in_fence: tuple[bytes, int] | None = None
    math_text: list[bytes] = []
    for line in content.splitlines():
        stripped = line.lstrip(b" ")
        indent = len(line) - len(stripped)
        fence_match = re.match(rb"(`{3,}|~{3,})(.*)$", stripped) if indent <= 3 else None
        if fence_match:
            marker = fence_match.group(1)
            if in_fence is None:
                in_fence = (marker[:1], len(marker))
            elif (
                marker[:1] == in_fence[0]
                and len(marker) >= in_fence[1]
                and not fence_match.group(2).strip()
            ):
                in_fence = None
            continue
        if in_fence is not None:
            continue
        math_text.append(_without_inline_code(line))

    line = b"\n".join(math_text)
    index = 0
    while index < len(line):
        if line[index] != ord("$"):
            index += 1
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == ord("\\"):
            backslashes += 1
            cursor -= 1
        if backslashes % 2:
            index += 1
            continue
        delimiter = b"$$" if line[index : index + 2] == b"$$" else b"$"
        close = index + len(delimiter)
        line_end = line.find(b"\n", close) if delimiter == b"$" else -1
        search_end = len(line) if line_end < 0 else line_end
        while close < search_end:
            if line[close : close + len(delimiter)] == delimiter:
                escaped = 0
                cursor = close - 1
                while cursor >= 0 and line[cursor] == ord("\\"):
                    escaped += 1
                    cursor -= 1
                if escaped % 2 == 0:
                    count += 1
                    index = close + len(delimiter)
                    break
            close += 1
        else:
            index += len(delimiter)
    return count


def expected_math_pages(source: Path, paths: set[bytes]) -> set[bytes]:
    """Compatibility helper returning pages with at least one formula token."""
    return {
        path[:-3] + b".html"
        for path in paths
        if markdown_math_token_count(bytes_path(source, path).read_bytes()) > 0
    }


def html_files(book: Path) -> list[bytes]:
    return [path for path in walk_regular_files(book) if path.endswith(b".html")]


def validate_page_mapping(book: Path, source_paths: set[bytes]) -> set[bytes]:
    mapped: set[bytes] = set()
    missing: list[str] = []
    for source_path in sorted(source_paths):
        html_path = source_path[:-3] + b".html"
        destination = bytes_path(book, html_path)
        if not destination.is_file() or destination.is_symlink():
            missing.append(os.fsdecode(html_path))
        else:
            mapped.add(html_path)
    if missing:
        sample = ", ".join(missing[:20])
        raise VerificationError(f"missing HTML page mappings ({len(missing)}): {sample}")
    return mapped


def validate_math(
    source: Path,
    book: Path,
    source_paths: set[bytes],
    all_html: list[bytes],
) -> dict[str, int]:
    residual = 0
    all_katex_pages = 0
    for html_path in all_html:
        content = bytes_path(book, html_path).read_bytes()
        residual += content.count(b"$$")
        if KATEX_MARKER in content:
            all_katex_pages += 1
    if residual:
        raise VerificationError(f"rendered HTML contains {residual} residual '$$' delimiters")

    expected_tokens: dict[bytes, int] = {
        path: markdown_math_token_count(bytes_path(source, path).read_bytes())
        for path in source_paths
    }
    rendered_tokens: dict[bytes, int] = {}
    mismatches: list[str] = []
    for source_path, expected_count in expected_tokens.items():
        html_path = source_path[:-3] + b".html"
        actual_count = bytes_path(book, html_path).read_bytes().count(KATEX_MARKER)
        rendered_tokens[html_path] = actual_count
        if actual_count != expected_count:
            mismatches.append(
                f"{os.fsdecode(source_path)} expected {expected_count} KaTeX nodes, "
                f"found {actual_count}"
            )
    if mismatches:
        raise VerificationError("math token mismatch; " + "; ".join(mismatches[:20]))
    return {
        "expected_math_pages": sum(count > 0 for count in expected_tokens.values()),
        "mapped_katex_pages": sum(count > 0 for count in rendered_tokens.values()),
        "expected_math_tokens": sum(expected_tokens.values()),
        "rendered_katex_nodes": sum(rendered_tokens.values()),
        "all_html_katex_pages": all_katex_pages,
        "residual_double_dollars": residual,
    }


def parse_resources(path: Path) -> list[str]:
    parser = ResourceCollector()
    try:
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
    except (OSError, UnicodeError) as exc:
        raise VerificationError(f"cannot parse HTML resources in {path}: {exc}") from exc
    return parser.resources


def resolve_relative_resource(book: Path, page: bytes, value: str) -> bytes | None:
    if not value or value.startswith("#") or value.startswith("//"):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise VerificationError(f"malformed resource URL in {os.fsdecode(page)}: {value}") from exc
    if parsed.scheme or parsed.netloc or parsed.path.startswith("/") or not parsed.path:
        return None
    raw_path = unquote_to_bytes(parsed.path)
    base = posixpath.dirname(page)
    target = posixpath.normpath(posixpath.join(base, raw_path))
    if target == b".." or target.startswith(b"../"):
        raise VerificationError(
            f"relative resource escapes site in {os.fsdecode(page)}: {value}"
        )
    candidate = bytes_path(book, target)
    if candidate.is_dir():
        target = posixpath.join(target, b"index.html")
    return target


def validate_links(book: Path, all_html: list[bytes]) -> int:
    return _validate_links(book, all_html, None)


def _validate_links(
    book: Path,
    all_html: list[bytes],
    pseudo_links: list[dict[str, str]] | None,
) -> int:
    checked = 0
    broken: list[str] = []
    for page in all_html:
        for value in parse_resources(bytes_path(book, page)):
            target = resolve_relative_resource(book, page, value)
            if target is None:
                continue
            checked += 1
            destination = bytes_path(book, target)
            if not destination.is_file() or destination.is_symlink():
                parsed = urlsplit(value)
                raw_path = unquote_to_bytes(parsed.path)
                basename = posixpath.basename(raw_path)
                raw_target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(page), raw_path)
                )
                raw_destination = bytes_path(book, raw_target)
                is_pseudo = (
                    not parsed.path.endswith("/") and b"." not in basename
                    and not raw_destination.exists()
                    and not raw_destination.is_symlink()
                )
                if is_pseudo and pseudo_links is not None:
                    pseudo_links.append({"source": os.fsdecode(page), "href": value})
                broken.append(f"{os.fsdecode(page)} -> {value}")
    if broken:
        sample = "; ".join(broken[:20])
        raise VerificationError(f"broken relative resources ({len(broken)}): {sample}")
    return checked


def artifact_size(book: Path) -> int:
    total = 0
    for relative in walk_regular_files(book):
        total += bytes_path(book, relative).stat().st_size
    if total >= ARTIFACT_LIMIT_BYTES:
        raise VerificationError(
            f"artifact is {total} bytes; limit is less than {ARTIFACT_LIMIT_BYTES}"
        )
    return total


def verify(upstream: Path, source: Path, book: Path) -> dict[str, object]:
    upstream = upstream.resolve()
    source = source.resolve()
    book = book.resolve()
    if not upstream.is_dir():
        raise VerificationError(f"upstream checkout is not a directory: {upstream}")

    provenance = load_provenance(source)
    sha = str(provenance["upstream_sha"])
    expected = expected_source_blobs(upstream, sha)
    actual = projected_source_blobs(source)
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(os.fsdecode(path) for path in missing[:10]))
        if extra:
            detail.append("extra: " + ", ".join(os.fsdecode(path) for path in extra[:10]))
        raise VerificationError("projected source set mismatch; " + "; ".join(detail))
    mismatched_blobs = [
        path for path in sorted(expected) if actual[path] != expected[path]
    ]
    if mismatched_blobs:
        sample = ", ".join(os.fsdecode(path) for path in mismatched_blobs[:20])
        raise VerificationError(f"projected source blob mismatch ({len(mismatched_blobs)}): {sample}")
    if provenance["file_count"] != len(expected):
        raise VerificationError("provenance file_count does not match the projected source set")

    try:
        problem_page = derive_open_problems(upstream, sha, str(provenance["built_at"]))
    except OpenProblemError as exc:
        raise VerificationError(str(exc)) from exc
    problem_source = bytes_path(source, PAGE_PATH)
    if problem_source.is_symlink() or not problem_source.is_file():
        raise VerificationError("open-problems.md is missing or unsafe")
    if problem_source.read_bytes() != problem_page.markdown.encode("utf-8"):
        raise VerificationError("open-problems.md generated page mismatch")

    copied_provenance = book / "provenance.json"
    if not copied_provenance.is_file() or copied_provenance.read_bytes() != (
        source / "provenance.json"
    ).read_bytes():
        raise VerificationError("provenance.json is missing or changed in the site artifact")

    mapped = validate_page_mapping(book, set(expected) | {PAGE_PATH})
    all_html = html_files(book)
    if not all_html:
        raise VerificationError("the site artifact contains no HTML")
    math_stats = validate_math(source, book, expected, all_html)
    pseudo_links: list[dict[str, str]] = []
    checked_links = _validate_links(book, all_html, pseudo_links)
    size = artifact_size(book)

    result: dict[str, object] = {
        "status": "ok",
        "upstream_sha": sha,
        "generator_revision": provenance["generator_revision"],
        "built_at": provenance["built_at"],
        "source_files": len(expected),
        "open_problem_dossiers": problem_page.dossier_count,
        "open_problem_markers": problem_page.marker_count,
        "mapped_pages": len(mapped),
        "html_files": len(all_html),
        "relative_resources_checked": checked_links,
        "broken_relative_resources": 0,
        "prose_pseudo_links": {
            "count": len(pseudo_links),
            "items": pseudo_links,
        },
        "artifact_bytes": size,
        "artifact_limit_bytes": ARTIFACT_LIMIT_BYTES,
    }
    result.update(math_stats)
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path, help="path to the upstream Git checkout")
    parser.add_argument("source", type=Path, help="generated mdBook source directory")
    parser.add_argument("book", type=Path, help="rendered mdBook output directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = verify(args.upstream, args.source, args.book)
    except VerificationError as exc:
        print(f"verify-site: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
