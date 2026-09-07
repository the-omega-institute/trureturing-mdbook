"""Derive the problem page from immutable dossiers and Markdown marker records.

Marker syntax is not evidence of a typed Scribe claim or a valid Lean proof.
"""

from __future__ import annotations

import io
import json
import os
import re
import string
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote_from_bytes

try:
    from source_tree import PUBLISHED_MODES, SourceEntry, is_published_path
except ModuleNotFoundError:
    from scripts.source_tree import PUBLISHED_MODES, SourceEntry, is_published_path


UPSTREAM_REPOSITORY = "https://github.com/the-omega-institute/trureturing"
PAGE_PATH = b"open-problems.md"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
BIBKEY_RE = re.compile(r"[a-z][a-z0-9]*")
DOI_RE = re.compile(r"^10\.[0-9]{4,9}/\S+$")
GID_RE = re.compile(r"D[0-9]+/S[0-9]+/(?:[A-Za-z_][A-Za-z0-9_]*/)*"
                    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
PROBLEM_KEYS = {"slug", "bibkey", "doi", "triage", "motivation_gids"}
LIBRARY_KEYS = {"bibkey", "authors", "year", "title", "doi", "claim",
                "strata_touched", "license", "triage"}
MARKER_PREFIX = "scribe-open-problem-resolution"
MARKER_RE = re.compile(r"<!-- scribe-open-problem-resolution-v([0-9]+) (.+) -->")
# YAML c-printable excludes these ranges; UTF-8 decoding excludes surrogates.
FORBIDDEN_YAML_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f\ufffe\uffff]")


class OpenProblemError(RuntimeError):
    """An input cannot be interpreted without guessing."""


@dataclass(frozen=True)
class Problem:
    slug: str
    title: str
    bibkey: str
    doi: str
    triage: str
    path: bytes


@dataclass(frozen=True)
class Resolution:
    kind: str
    declaration_gid: str
    path: bytes
    line: int


@dataclass(frozen=True)
class ProblemPage:
    markdown: str
    dossier_count: int
    marker_count: int


def git(upstream: Path, *args: str, input: bytes | None = None) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(upstream), *args], input=input,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise OpenProblemError(
            f"open problems: git {args[0]} failed: {exc.stderr.decode('utf-8', 'replace').strip()}"
        ) from exc


def input_entries(upstream: Path, sha: str) -> list[SourceEntry]:
    raw = git(upstream, "ls-tree", "-r", "-z", sha, "--", "Blueprint", "Problems", "Library")
    entries = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
        except ValueError as exc:
            raise OpenProblemError("open problems: malformed Git tree record") from exc
        if kind != b"blob" and mode in PUBLISHED_MODES:
            raise OpenProblemError("open problems: regular input is not a blob")
        entries.append(SourceEntry(mode, oid, path))
    return entries


def input_blobs(upstream: Path, entries: list[SourceEntry]) -> list[tuple[bytes, bytes]]:
    if not entries:
        return []
    stream = io.BytesIO(git(
        upstream, "cat-file", "--batch", input=b"".join(entry.oid + b"\n" for entry in entries),
    ))
    blobs = []
    for entry in entries:
        header = stream.readline().split()
        if len(header) != 3 or header[:2] != [entry.oid, b"blob"] or not header[2].isdigit():
            raise OpenProblemError("open problems: malformed cat-file blob header")
        size = int(header[2])
        blob = stream.read(size)
        if len(blob) != size or stream.read(1) != b"\n":
            raise OpenProblemError("open problems: truncated cat-file blob")
        blobs.append((entry.path, blob))
    if stream.read():
        raise OpenProblemError("open problems: unexpected cat-file trailing data")
    return blobs


def validate_plain_scalar(scalar: str, label: str) -> None:
    if (not scalar or scalar != scalar.strip() or scalar[0] in "\"'[]{},#&*!>|%@`"
            or re.match(r"[-?:](?: |$)", scalar)
            or any(char in scalar for char in "\t\n\r\x85\u2028\u2029\ufeff")
            or " #" in scalar or ": " in scalar or scalar.endswith(":")):
        raise OpenProblemError(f"{label}: unsupported front matter scalar")


def front_matter(path: bytes, blob: bytes) -> tuple[dict[str, str | list[str]], str]:
    label = os.fsdecode(path)
    try:
        text = blob.decode("utf-8")
    except UnicodeError as exc:
        raise OpenProblemError(f"{label}: front matter must be UTF-8") from exc
    forbidden = FORBIDDEN_YAML_CHAR_RE.search(text)
    if forbidden:
        raise OpenProblemError(
            f"{label}: forbidden YAML character U+{ord(forbidden[0]):04X}"
        )
    if text.startswith("\ufeff") or "\r" in text or not text.startswith("---\n"):
        raise OpenProblemError(f"{label}: needs canonical front matter (UTF-8, no BOM or CR)")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise OpenProblemError(f"{label}: unterminated front matter")
    # The upstream format uses plain scalar lines and two-space block lists.
    # No tabs, folding, comments, collections within items, or YAML type coercion.
    fields: dict[str, str | list[str]] = {}
    current = None
    for line in text[4:end].split("\n"):
        if line.startswith("  - ") and current is not None:
            value = fields[current]
            item = line[4:]
            if not isinstance(value, list):
                raise OpenProblemError(f"{label}: malformed front matter list")
            validate_plain_scalar(item, label)
            value.append(item)
            continue
        match = re.fullmatch(r"([a-z_]+):(?: (.+))?", line)
        if not match:
            raise OpenProblemError(f"{label}: unsupported front matter syntax")
        current, scalar = match.groups()
        if current in fields:
            raise OpenProblemError(f"{label}: duplicate front matter key {current}")
        if scalar is not None:
            validate_plain_scalar(scalar, label)
        fields[current] = scalar if scalar is not None else []
    return fields, text[end + 5:]


def required_scalar(fields: dict[str, str | list[str]], key: str, path: bytes) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value:
        raise OpenProblemError(f"{os.fsdecode(path)}: {key} must be a nonempty scalar")
    return value


def parse_dossiers(blobs: list[tuple[bytes, bytes]]) -> list[Problem]:
    problems = []
    previous = ""
    for path, blob in blobs:
        fields, body = front_matter(path, blob)
        label = os.fsdecode(path)
        if set(fields) != PROBLEM_KEYS:
            raise OpenProblemError(
                f"{label}: unsupported problem schema; expected slug, bibkey, doi, "
                "triage, motivation_gids"
            )
        slug = required_scalar(fields, "slug", path)
        if not SLUG_RE.fullmatch(slug) or path != f"Problems/{slug}.md".encode():
            raise OpenProblemError(f"{label}: noncanonical slug or slug/path mismatch")
        if slug <= previous:
            raise OpenProblemError(f"{label}: duplicate or out-of-order problem slug {slug}")
        previous = slug
        bibkey = required_scalar(fields, "bibkey", path)
        doi = required_scalar(fields, "doi", path)
        triage = required_scalar(fields, "triage", path)
        if not BIBKEY_RE.fullmatch(bibkey):
            raise OpenProblemError(f"{label}: noncanonical bibkey")
        if not DOI_RE.fullmatch(doi):
            raise OpenProblemError(f"{label}: invalid DOI")
        if triage not in {"theorem", "window", "wall"}:
            raise OpenProblemError(f"{label}: unknown triage")
        gids = fields["motivation_gids"]
        if (not isinstance(gids, list) or not gids or len(set(gids)) != len(gids)
                or any(not GID_RE.fullmatch(gid) for gid in gids)):
            raise OpenProblemError(f"{label}: motivation_gids must be unique formal GIDs")
        titles = re.findall(r"^# (.+)$", body, re.MULTILINE)
        if len(titles) != 1 or not titles[0].strip():
            raise OpenProblemError(f"{label}: expected one nonempty problem title")
        problems.append(Problem(slug, titles[0], bibkey, doi, triage, path))
    return problems


def marker_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise OpenProblemError(f"resolution marker has duplicate key {key}")
        result[key] = value
    return result


def parse_markers(blobs: list[tuple[bytes, bytes]], slugs: set[str]) -> dict[str, Resolution]:
    resolutions: dict[str, Resolution] = {}
    for path, blob in blobs:
        if MARKER_PREFIX.encode() not in blob:
            continue
        try:
            lines = blob.decode("utf-8").splitlines()
        except UnicodeError as exc:
            raise OpenProblemError(f"{os.fsdecode(path)}: resolution marker page is not UTF-8") from exc
        previous = ""
        for number, line in enumerate(lines, 1):
            if MARKER_PREFIX not in line:
                continue
            label = f"{os.fsdecode(path)}:{number}: resolution marker"
            match = MARKER_RE.fullmatch(line)
            if match is None:
                raise OpenProblemError(f"{label} has malformed syntax")
            version, payload = match.groups()
            if version != "1":
                raise OpenProblemError(f"{label} has unknown schema version {version}")
            try:
                record = json.loads(payload, object_pairs_hook=marker_object)
            except (ValueError, RecursionError) as exc:
                raise OpenProblemError(f"{label} has malformed JSON") from exc
            if not isinstance(record, dict):
                raise OpenProblemError(f"{label} payload must be a JSON object")
            if set(record) != {"problem_slug", "declaration_gid", "resolution_kind"}:
                raise OpenProblemError(f"{label} has missing or unknown keys")
            slug, kind = record["problem_slug"], record["resolution_kind"]
            gid = record["declaration_gid"]
            if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
                raise OpenProblemError(f"{label} has an invalid problem slug")
            if not isinstance(kind, str) or kind not in {"proved", "refuted"}:
                raise OpenProblemError(f"{label} has an invalid resolution kind")
            if not isinstance(gid, str) or not GID_RE.fullmatch(gid) or "." not in gid:
                raise OpenProblemError(f"{label} has an invalid declaration GID")
            if slug not in slugs:
                raise OpenProblemError(f"{label} references unknown problem slug {slug}")
            if slug in resolutions:
                raise OpenProblemError(f"{label} has duplicate resolution slug {slug}")
            if slug <= previous:
                raise OpenProblemError(f"{label} has out-of-order resolution slug {slug}")
            previous = slug
            resolutions[slug] = Resolution(kind, gid, path, number)
    return resolutions


def frozen_state_date(upstream: Path, sha: str, blueprint_path: bytes) -> str:
    # Upstream's documented GID layout preserves module segments across .lean,
    # Blueprint/*.scribe.cs and Blueprint/*.md. The marker container is authoritative.
    module = blueprint_path.removeprefix(b"Blueprint/").removesuffix(b".md")
    state_path = b"Golden/Frozen/state/" + module + b".lean.json"
    history = git(
        upstream, "log", "--diff-filter=A", "--format=%cs", "--reverse", "--no-renames",
        sha, "--", ":(literal)" + os.fsdecode(state_path),
    ).splitlines()
    if not history:
        raise OpenProblemError(f"no adding commit for {os.fsdecode(state_path)} at {sha}")
    try:
        frozen_date = history[0].decode("ascii")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", frozen_date):
            raise ValueError("expected YYYY-MM-DD")
        date.fromisoformat(frozen_date)
    except (UnicodeError, ValueError) as exc:
        raise OpenProblemError(f"invalid frozen-state commit date for {os.fsdecode(state_path)}") from exc
    return frozen_date


def markdown_text(text: str) -> str:
    return "".join("\\" + char if char in string.punctuation else char for char in text)


def derive_open_problems(upstream: Path, sha: str) -> ProblemPage:
    entries = input_entries(upstream, sha)
    dossier_entries = []
    for entry in entries:
        if entry.path == b"Problems" or entry.path.startswith(b"Problems/"):
            if entry.mode not in PUBLISHED_MODES:
                raise OpenProblemError("open problem inputs must be regular Git blobs")
            if not re.fullmatch(rb"Problems/[a-z0-9]+(?:-[a-z0-9]+)*\.md", entry.path):
                raise OpenProblemError(f"invalid open problem input path: {os.fsdecode(entry.path)}")
            dossier_entries.append(entry)
    # Git orders filenames, where alpha-beta.md precedes alpha.md; order by slug.
    dossier_entries.sort(key=lambda entry: entry.path[:-3])
    problems = parse_dossiers(input_blobs(upstream, dossier_entries))
    notes: dict[str, tuple[bytes, str]] = {}
    for bibkey in sorted({problem.bibkey for problem in problems}):
        candidates = [entry for entry in entries
                      if entry.path.startswith(b"Library/")
                      and entry.path.count(b"/") == 2
                      and entry.path.endswith(b"/" + bibkey.encode() + b".md")]
        if len(candidates) != 1:
            reason = "missing" if not candidates else "ambiguous"
            raise OpenProblemError(f"{reason} Library bibkey {bibkey}")
        if candidates[0].mode not in PUBLISHED_MODES:
            raise OpenProblemError(f"Library bibkey {bibkey} must be a regular Git blob")
        path, blob = input_blobs(upstream, candidates)[0]
        fields, _ = front_matter(path, blob)
        if set(fields) != LIBRARY_KEYS:
            raise OpenProblemError(f"{os.fsdecode(path)}: missing or unknown Library metadata keys")
        for key in sorted(LIBRARY_KEYS - {"strata_touched"}):
            required_scalar(fields, key, path)
        strata = fields["strata_touched"]
        if not isinstance(strata, list) or not strata:
            raise OpenProblemError(f"{os.fsdecode(path)}: strata_touched must be a nonempty block list")
        if required_scalar(fields, "bibkey", path) != bibkey:
            raise OpenProblemError(f"{os.fsdecode(path)}: bibkey/path mismatch")
        doi = required_scalar(fields, "doi", path)
        if not DOI_RE.fullmatch(doi):
            raise OpenProblemError(f"{os.fsdecode(path)}: invalid DOI")
        notes[bibkey] = path, doi
    for problem in problems:
        path, doi = notes[problem.bibkey]
        if doi != problem.doi:
            raise OpenProblemError(
                f"{os.fsdecode(path)}: DOI {doi!r} disagrees with "
                f"{os.fsdecode(problem.path)}: DOI {problem.doi!r}"
            )
    blueprint = [entry for entry in entries
                 if entry.path.startswith(b"Blueprint/") and is_published_path(entry.path)
                 and entry.mode in PUBLISHED_MODES]
    resolutions = parse_markers(input_blobs(upstream, blueprint), {p.slug for p in problems})
    frozen_dates = {
        path: frozen_state_date(upstream, sha, path)
        for path in sorted({resolution.path for resolution in resolutions.values()})
    }
    lines = [
        "# Open problems from research papers", "",
        f"**{len(resolutions)} of {len(problems)} solved in this repository.**", "",
        'What "solved" means here: a frozen Lean theorem in this repository is recorded',
        "against the problem. Nobody has machine-checked that the theorem says the same",
        "thing as the paper, and a problem with no record here may still have been solved",
        "by someone else.", "",
    ]
    for heading, solved, count in (
        ("Solved", True, len(resolutions)),
        ("Not solved here", False, len(problems) - len(resolutions)),
    ):
        lines.extend([f"## {heading} ({count})", ""])
        for problem in problems:
            if (problem.slug in resolutions) != solved:
                continue
            note_path, doi = notes[problem.bibkey]
            dossier_url = quote_from_bytes(problem.path, safe="/")
            note_url = quote_from_bytes(note_path, safe="/")
            doi_url = "https://doi.org/" + quote_from_bytes(doi.encode(), safe="/")
            lines.extend([f"### {markdown_text(problem.title)}", ""])
            resolution = resolutions.get(problem.slug)
            if resolution is not None:
                target = quote_from_bytes(resolution.path, safe="/")
                label = "Proved" if resolution.kind == "proved" else "Refuted"
                declaration = resolution.declaration_gid.split(".", 1)[1]
                lines.extend([
                    f"**{label}.** Lean theorem [`{declaration}`]({target}), "
                    f"frozen in this repository {frozen_dates[resolution.path]}.", "",
                ])
            lines.extend([
                f"[Problem details]({dossier_url}) \u00b7 [Reading note]({note_url}) "
                f"\u00b7 [Source paper]({doi_url})", "",
            ])
    lines.extend([
        "## How this list is made", "",
        f"Source revision: [`{sha[:8]}`]({UPSTREAM_REPOSITORY}/commit/{sha}).", "",
        "The list is generated from problem files, reading notes, and resolution records in theorem pages at this source revision.",
        "The records are read as text, so ordinary prose can produce one; this page does not check that a record came from the repository's own verified claim.",
        "This page does not run the repository's checks or verify the named theorems or their Lean proofs.", "",
        'The "frozen in this repository" date is the date of the first commit that added the theorem\'s module to the frozen record. It is not the date the problem was solved in the world or the resolution was recorded.', "",
    ])
    return ProblemPage("\n".join(lines), len(problems), len(resolutions))
