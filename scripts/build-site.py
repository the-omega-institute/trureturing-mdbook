#!/usr/bin/env python3
"""Project published upstream Markdown into an mdBook source directory."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_from_bytes

try:
    from site_freshness import generator_revision, snapshot_freshness
    from open_problems import OpenProblemError, derive_open_problems
    from source_tree import (
        PUBLISHED_ROOTS,
        SourceEntry,
        is_published_path,
        list_source_entries as read_source_entries,
    )
except ModuleNotFoundError:
    from scripts.site_freshness import generator_revision, snapshot_freshness
    from scripts.open_problems import OpenProblemError, derive_open_problems
    from scripts.source_tree import (
        PUBLISHED_ROOTS,
        SourceEntry,
        is_published_path,
        list_source_entries as read_source_entries,
    )


TOOL_VERSION = "1.0.0"
UPSTREAM_REPOSITORY = "https://github.com/the-omega-institute/trureturing"
PROJECTION_MARKER = ".trureturing-mdbook-projection"
PROJECTION_MARKER_CONTENT = b"trureturing-mdbook projection v1\n"
# Thirty change-days keeps the generated page useful despite hundreds of daily commits.
CHANGELOG_DAYS = 30
SHA_RE = re.compile(rb"^[0-9a-f]{40,64}$")
H1_RE = re.compile(r"^# (.*)\r?$", re.MULTILINE)


class BuildError(RuntimeError):
    """Raised when an immutable projection cannot be produced safely."""


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str


def run_git(upstream: Path, *args: str) -> bytes:
    command = ["git", "-C", os.fspath(upstream), *args]
    try:
        return subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise BuildError(f"git command failed ({' '.join(args)}): {detail}") from exc


def capture_upstream_sha(upstream: Path) -> str:
    sha = run_git(upstream, "rev-parse", "HEAD").strip()
    if not SHA_RE.fullmatch(sha):
        raise BuildError("git rev-parse HEAD did not return a full object ID")
    return sha.decode("ascii")


def list_source_entries(upstream: Path, sha: str) -> list[SourceEntry]:
    return read_source_entries(upstream, sha, BuildError)


def read_blobs(upstream: Path, entries: list[SourceEntry]) -> list[bytes]:
    command = ["git", "-C", os.fspath(upstream), "cat-file", "--batch"]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    request = b"".join(entry.oid + b"\n" for entry in entries)
    stdout, stderr = process.communicate(request)
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip()
        raise BuildError(f"git cat-file --batch failed: {detail}")

    blobs: list[bytes] = []
    cursor = 0
    for entry in entries:
        line_end = stdout.find(b"\n", cursor)
        if line_end < 0:
            raise BuildError("git cat-file returned a truncated header")
        header = stdout[cursor:line_end].split()
        if len(header) != 3 or header[0] != entry.oid or header[1] != b"blob":
            raise BuildError(f"unexpected cat-file response for {entry.oid.decode('ascii')}")
        try:
            size = int(header[2])
        except ValueError as exc:
            raise BuildError("git cat-file returned an invalid blob size") from exc
        start = line_end + 1
        end = start + size
        if end >= len(stdout) or stdout[end : end + 1] != b"\n":
            raise BuildError("git cat-file returned truncated blob data")
        blobs.append(stdout[start:end])
        cursor = end + 1
    if cursor != len(stdout):
        raise BuildError("git cat-file returned unexpected trailing data")
    return blobs


def decode_path(path: bytes) -> str:
    return os.fsdecode(path)


def display_path(path: bytes) -> str:
    return path.decode("utf-8", "replace")


def markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def markdown_path(path: bytes) -> str:
    try:
        text = path.decode("utf-8")
    except UnicodeDecodeError:
        return quote_from_bytes(path, safe="/-._~")
    return "".join(
        character
        if ord(character) >= 128
        else quote_from_bytes(character.encode("ascii"), safe="/-._~")
        for character in text
    )


def file_title(path: bytes, content: bytes) -> str:
    text = content.decode("utf-8", "replace")
    match = H1_RE.search(text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    name = posixpath.basename(path)
    return display_path(name[:-3])


def directory_set(entries: list[SourceEntry]) -> set[bytes]:
    directories: set[bytes] = set()
    for entry in entries:
        directory = posixpath.dirname(entry.path)
        while directory:
            directories.add(directory)
            directory = posixpath.dirname(directory)
    return directories


def nav_path(directory: bytes) -> bytes:
    # Keep every navigation page a direct child file. Hex encodes arbitrary Git
    # path bytes injectively and cannot collide with a file/dir boundary.
    return b"_nav/" + directory.hex().encode("ascii") + b".md"


def direct_children(
    directory: bytes,
    directories: set[bytes],
    entries: list[SourceEntry],
) -> list[tuple[bytes, bool]]:
    children: list[tuple[bytes, bool]] = []
    children.extend(
        (candidate, True)
        for candidate in directories
        if candidate != directory and posixpath.dirname(candidate) == directory
    )
    children.extend(
        (entry.path, False)
        for entry in entries
        if posixpath.dirname(entry.path) == directory
    )
    return sorted(children, key=lambda item: (item[0], not item[1]))


def menu_chain(
    directory: bytes,
    directories: set[bytes],
    entries: list[SourceEntry],
) -> tuple[list[bytes], bytes]:
    """Follow single-subdirectory links: ``Blueprint`` → ``D5`` → ``S0`` is one menu entry.

    Returns the directories folded into the entry and the last of them, whose
    children the entry lists. A directory that also holds a page, or more than
    one subdirectory, ends the chain.
    """
    chain = [directory]
    while True:
        children = direct_children(directory, directories, entries)
        if len(children) != 1 or not children[0][1]:
            return chain, directory
        directory = children[0][0]
        chain.append(directory)


def menu_label(chain: list[bytes]) -> str:
    return " / ".join(display_path(posixpath.basename(directory)) for directory in chain)


def build_summary(
    entries: list[SourceEntry],
    titles: dict[bytes, str],
    directories: set[bytes],
) -> str:
    lines = [
        "# Summary",
        "",
        "- [Home](index.md)",
        "- [Changelog](changelog.md)",
        "- [External open problems](open-problems.md)",
    ]

    def add_directory(directory: bytes, depth: int) -> None:
        chain, directory = menu_chain(directory, directories, entries)
        lines.append(
            f"{'  ' * depth}- [{markdown_text(menu_label(chain))}]"
            f"({markdown_path(nav_path(directory))})"
        )
        for child, is_directory in direct_children(directory, directories, entries):
            if is_directory:
                add_directory(child, depth + 1)
            else:
                lines.append(
                    f"{'  ' * (depth + 1)}- "
                    f"[{markdown_text(titles[child])}]({markdown_path(child)})"
                )

    for directory in sorted(directories):
        if not posixpath.dirname(directory):
            add_directory(directory, 0)
    lines.append("")
    return "\n".join(lines)


def build_nav_page(
    directory: bytes,
    directories: set[bytes],
    entries: list[SourceEntry],
    titles: dict[bytes, str],
    sha: str,
    label: str | None = None,
) -> str:
    label = display_path(posixpath.basename(directory)) if label is None else label
    current_nav = nav_path(directory)
    start = posixpath.dirname(current_nav)
    lines = [
        f"# {label}",
        "",
        f"Navigation page for `{display_path(directory)}/` at upstream snapshot `{sha}`.",
        "",
    ]
    children = direct_children(directory, directories, entries)
    if children:
        lines.extend(["## Contents", ""])
        for child, is_directory in children:
            target = nav_path(child) if is_directory else child
            relative = posixpath.relpath(target, start=start)
            if is_directory:
                chain, leaf = menu_chain(child, directories, entries)
                target = nav_path(leaf)
                relative = posixpath.relpath(target, start=start)
                title = menu_label(chain)
            else:
                title = titles[child]
            lines.append(f"- [{markdown_text(title)}]({markdown_path(relative)})")
    else:
        lines.append("This directory has no publishable Markdown pages in the current snapshot.")
    lines.append("")
    return "\n".join(lines)


def log_commits(upstream: Path, sha: str) -> list[Commit]:
    raw = run_git(
        upstream,
        "log",
        "--first-parent",
        "--format=%H%x09%cs%x09%s",
        sha,
        "--",
        *(os.fsdecode(root) + "/" for root in PUBLISHED_ROOTS),
    )
    commits: list[Commit] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3 or not re.fullmatch(r"[0-9a-f]{40,64}", fields[0]):
            raise BuildError("git log returned malformed commit metadata")
        commits.append(Commit(sha=fields[0], date=fields[1], subject=fields[2]))
    return commits


def changed_paths(upstream: Path, commit: Commit) -> list[bytes]:
    raw = run_git(
        upstream,
        "show",
        "--first-parent",
        "--format=",
        "--name-only",
        "--no-renames",
        "-z",
        commit.sha,
        "--",
        *(os.fsdecode(root) + "/" for root in PUBLISHED_ROOTS),
    )
    # Reuse the publication predicate: the changelog must list only files the
    # site actually publishes. Emitter sources such as *.scribe.cs live under
    # Blueprint/ but are never published, so listing them is noise the reader
    # cannot follow.
    return sorted({path for path in raw.split(b"\0") if is_published_path(path)})


def build_changelog(
    upstream: Path, sha: str, published: frozenset[bytes] = frozenset()
) -> str:
    """Render the changelog.

    ``published`` is the set of paths present in the current snapshot. Only those
    are linked: a path that upstream has since deleted still belongs in the
    history, but linking it would produce a dangling site link, which the release
    gate rejects. Such paths stay as plain code text.
    """
    commits = log_commits(upstream, sha)
    selected_dates: list[str] = []
    for commit in commits:
        if commit.date not in selected_dates:
            selected_dates.append(commit.date)
            if len(selected_dates) == CHANGELOG_DAYS:
                break
    selected = set(selected_dates)

    # The first (newest) commit touching a path on a day supplies that path's metadata.
    daily: dict[str, dict[bytes, Commit]] = defaultdict(dict)
    for commit in commits:
        if commit.date not in selected:
            continue
        for path in changed_paths(upstream, commit):
            daily[commit.date].setdefault(path, commit)

    lines = [
        "# Changelog",
        "",
        f"Generated from the first-parent history of upstream snapshot `{sha}`, "
        f"covering the most recent {CHANGELOG_DAYS} dates with changes.",
        "Each path is listed once per day, using the most recent commit that touched it that day.",
        "",
    ]
    for date in selected_dates:
        lines.extend([f"## {date}", ""])
        for path in sorted(daily.get(date, {})):
            commit = daily[date][path]
            short_sha = commit.sha[:7]
            commit_url = f"{UPSTREAM_REPOSITORY}/commit/{commit.sha}"
            path_text = markdown_text(display_path(path))
            subject = markdown_text(commit.subject)
            if path in published:
                rendered_path = f"[`{path_text}`]({markdown_path(path)})"
            else:
                # Deleted upstream since this commit; linking it would dangle.
                rendered_path = f"`{path_text}`"
            lines.append(
                f"- {rendered_path} · [{short_sha}]({commit_url}) · {commit.date} · {subject}"
            )
        if not daily.get(date):
            lines.append("- No published Markdown paths to list for this date.")
        lines.append("")

    history_url = f"{UPSTREAM_REPOSITORY}/commits/{sha}/"
    lines.extend([f"[Full upstream history up to this snapshot]({history_url})", ""])
    return "\n".join(lines)


def build_index(sha: str, built_at: str, published: frozenset[bytes]) -> str:
    commit_url = f"{UPSTREAM_REPOSITORY}/commit/{sha}"
    current_upstream = f"{UPSTREAM_REPOSITORY}/blob/dev"
    examples = (
        (
            b"Blueprint/D5/S0/Certificates/GreathouseLogTwoFloorRefutation.md",
            "Can a plausible formula fail?",
            "Follow the refutation of the literal OEIS A175406 floor formula at an explicit index.",
        ),
        (
            b"Blueprint/D5/S3/Quantum/Entanglement/LocalMarginalCorrelationBlindSpot.md",
            "Do local observations determine the whole?",
            "Explore a finite quantum-state example: two different global states have the same local marginals.",
        ),
    )
    routes = [
        f"- [{question}]({markdown_path(path)}) {description}"
        for path, question, description in examples
        if path in published
    ]
    if not routes:
        routes.append(
            f"- [Which questions could I explore?]({current_upstream}/README.md#three-places-to-look) "
            "Browse examples in the current upstream guidance."
        )
    question_routes = "\n".join(routes)
    return f"""# trureturing — Discovering truth

trureturing is a **truth-discovery library**. Its name brings together **true**, **return**
and **Turing**: truth, return and Turing computation. Logic here includes mathematical
reasoning as well as philosophical and conceptual inquiry.

**If machine learning is a logical black box, this project is a logical white box.**
Definitions, assumptions, proof steps and dependencies make reasoning inspectable.
**All logic can be formalized** is the guiding conviction, not a universal theorem or
a completed capability. Lean proofs establish their stated claims under their assumptions;
research inputs, experiments and open questions retain their own scope.

This entrance offers a glimpse of the iceberg. Bring your own question and explore its
larger shape for yourself.

**[Start your journey with Claude Code or Codex]({current_upstream}/README.md#start-your-journey)**
— current upstream guidance.

## Search

<link href="pagefind/pagefind-ui.css" rel="stylesheet">
<div id="search"></div>
<script src="pagefind/pagefind-ui.js"></script>
<script>
window.addEventListener("DOMContentLoaded", function () {{
  new PagefindUI({{ element: "#search", showSubResults: true }});
}});
</script>

## Follow a question

{question_routes}
- [What is recorded as resolved, and what has no recorded resolution?](open-problems.md)
  Explore the external problem dossiers and their snapshot resolution records.

Continue with **current upstream guidance**:
[First run]({current_upstream}/README.md#first-run) ·
[Use Claude Code or Codex and contribute]({current_upstream}/docs/CONTRIBUTING.md#use-claude-code-or-codex) ·
[Three places to look]({current_upstream}/README.md#three-places-to-look).
These links follow upstream `dev` and may describe work beyond this book's snapshot.

## About this snapshot

This site is an automatically derived projection of the `Blueprint/`, `Problems/` and `Library/` Markdown content in
[the-omega-institute/trureturing]({UPSTREAM_REPOSITORY}), published for browsing and search.
The source of mathematical truth is always the upstream repository, never this site.

The content shown here is pinned to upstream commit [`{sha}`]({commit_url}).
{snapshot_freshness(built_at)}

The generator, configuration and workflows in this repository are MIT licensed. For upstream
content, consult the license and any content-specific notices in the
[upstream source at this snapshot]({UPSTREAM_REPOSITORY}/tree/{sha}).
This site grants no rights to that content and does not sublicense it. KaTeX and Pagefind
assets retain their own copyright and license notices.
"""


def build_open_problems(upstream: Path, sha: str, built_at: str) -> str:
    try:
        return derive_open_problems(upstream, sha, built_at).markdown
    except OpenProblemError as exc:
        raise BuildError(str(exc)) from exc


def safe_output_path(upstream: Path, output: Path) -> Path:
    upstream = upstream.resolve()
    cwd = Path.cwd().resolve()
    # Inspect the lexical final component before resolving, otherwise an
    # output symlink could redirect rmtree() to an unrelated directory.
    lexical_output = output.absolute()
    if lexical_output.is_symlink():
        raise BuildError("refusing to replace a symlink output path")
    output = lexical_output.resolve(strict=False)
    if output == Path(output.anchor):
        raise BuildError("refusing to replace a filesystem root")

    def contains(parent: Path, child: Path) -> bool:
        return child == parent or parent in child.parents

    if contains(upstream, output) or contains(output, upstream):
        raise BuildError("refusing an output path inside or above the upstream checkout")
    if contains(cwd, output) or contains(output, cwd):
        raise BuildError("refusing an output path inside or above the working directory")
    if output.exists() and not output.is_dir():
        raise BuildError("output path exists and is not a directory")
    if output.exists():
        marker = output / PROJECTION_MARKER
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            raise BuildError("projection marker is not a regular file")
        if marker.exists() and marker.read_bytes() != PROJECTION_MARKER_CONTENT:
            raise BuildError("existing output has an invalid projection marker")
        if not marker.exists() and any(output.iterdir()):
            raise BuildError("refusing to replace a non-empty unmarked output directory")
    return output


def write_projection(
    staging: Path,
    entries: list[SourceEntry],
    blobs: list[bytes],
    upstream: Path,
    sha: str,
    built_at: str,
    revision: str,
) -> dict[str, object]:
    titles: dict[bytes, str] = {}
    staging_resolved = staging.resolve()
    for entry, content in zip(entries, blobs, strict=True):
        destination = staging / decode_path(entry.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if os.path.commonpath([staging_resolved, destination.resolve()]) != os.fspath(
            staging_resolved
        ):
            raise BuildError(f"upstream path escapes output: {decode_path(entry.path)}")
        destination.write_bytes(content)
        destination.chmod(0o755 if entry.mode == b"100755" else 0o644)
        titles[entry.path] = file_title(entry.path, content)

    directories = directory_set(entries)
    (staging / "SUMMARY.md").write_text(
        build_summary(entries, titles, directories), encoding="utf-8"
    )
    published = frozenset(entry.path for entry in entries)
    (staging / "index.md").write_text(build_index(sha, built_at, published), encoding="utf-8")
    (staging / "open-problems.md").write_text(
        build_open_problems(upstream, sha, built_at), encoding="utf-8"
    )
    (staging / "changelog.md").write_text(
        build_changelog(upstream, sha, published),
        encoding="utf-8",
    )
    # One navigation page per menu entry: a directory folded into a chain has none.
    folded: set[bytes] = set()
    labels: dict[bytes, str] = {}
    for directory in sorted(directories):
        chain, leaf = menu_chain(directory, directories, entries)
        folded.update(chain[:-1])
        labels.setdefault(leaf, menu_label(chain))
    for directory in sorted(directories - folded):
        destination = staging / decode_path(nav_path(directory))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            build_nav_page(directory, directories, entries, titles, sha, labels[directory]),
            encoding="utf-8",
        )

    provenance: dict[str, object] = {
        "upstream_sha": sha,
        "file_count": len(entries),
        "tool_version": TOOL_VERSION,
        "built_at": built_at,
        "generator_revision": revision,
    }
    (staging / PROJECTION_MARKER).write_bytes(PROJECTION_MARKER_CONTENT)
    (staging / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return provenance


def build_site(upstream: Path, output: Path, *, upstream_sha: str | None = None) -> dict[str, object]:
    upstream = upstream.resolve()
    if not upstream.is_dir():
        raise BuildError(f"upstream checkout is not a directory: {upstream}")
    output = safe_output_path(upstream, output)
    output.parent.mkdir(parents=True, exist_ok=True)

    built_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    revision = generator_revision()
    sha = upstream_sha if upstream_sha is not None else capture_upstream_sha(upstream)
    if not SHA_RE.fullmatch(sha.encode("ascii")):
        raise BuildError("upstream snapshot must be a full object ID")
    run_git(upstream, "cat-file", "-e", f"{sha}^{{commit}}")
    entries = list_source_entries(upstream, sha)
    blobs = read_blobs(upstream, entries)

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        provenance = write_projection(staging, entries, blobs, upstream, sha, built_at, revision)
        if output.exists():
            shutil.rmtree(output)
        os.replace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return provenance


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path, help="path to the upstream Git checkout")
    parser.add_argument("output", type=Path, help="generated mdBook source directory")
    parser.add_argument("--upstream-sha", help="full immutable SHA captured by the freshness probe")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        provenance = build_site(args.upstream, args.output, upstream_sha=args.upstream_sha)
    except BuildError as exc:
        print(f"build-site: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(provenance, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
