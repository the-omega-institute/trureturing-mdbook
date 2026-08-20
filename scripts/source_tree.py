"""Shared publication predicate for tracked upstream Markdown blobs."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


PUBLISHED_MODES = {b"100644", b"100755"}
REJECTED_MODES = {b"120000", b"160000"}


def is_published_path(path: bytes) -> bool:
    """The single path predicate for published upstream content.

    Both the tree listing and the changelog use this, so the changelog can
    never drift from what the site actually publishes.
    """
    return path.startswith(b"Blueprint/") and path.endswith(b".md")


@dataclass(frozen=True)
class SourceEntry:
    mode: bytes
    oid: bytes
    path: bytes


def list_source_entries(
    upstream: Path,
    sha: str,
    error_type: type[Exception],
) -> list[SourceEntry]:
    command = [
        "git",
        "-C",
        os.fspath(upstream),
        "-c",
        "core.quotePath=false",
        "ls-tree",
        "-r",
        "-z",
        sha,
        "--",
        "Blueprint/",
    ]
    try:
        raw = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise error_type(f"git command failed (ls-tree): {detail}") from exc

    entries: list[SourceEntry] = []
    seen: set[bytes] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_type, oid = metadata.split(b" ", 2)
        except ValueError as exc:
            raise error_type("git ls-tree returned a malformed record") from exc
        if not is_published_path(path):
            continue
        if mode in REJECTED_MODES or mode not in PUBLISHED_MODES:
            continue
        if object_type != b"blob":
            raise error_type("git ls-tree returned a non-blob with a published mode")
        if path in seen:
            raise error_type(f"duplicate upstream path: {os.fsdecode(path)}")
        seen.add(path)
        entries.append(SourceEntry(mode=mode, oid=oid, path=path))

    entries.sort(key=lambda entry: entry.path)
    if not entries:
        raise error_type("the upstream publication predicate selected no files")
    return entries
