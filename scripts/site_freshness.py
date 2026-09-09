#!/usr/bin/env python3
"""Probe upstream once and rebuild unless the deployed snapshot is identical."""

from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPException
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


UPSTREAM_URL = "https://github.com/the-omega-institute/trureturing.git"
PROVENANCE_URL = "https://the-omega-institute.github.io/trureturing-mdbook/provenance.json"
SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
GENERATOR_INPUTS = (
    "book.toml", ".github/workflows/pages.yml", "scripts/build-site.py",
    "scripts/escape_pseudo_links.py", "scripts/open_problems.py", "scripts/render_source_links.py",
    "scripts/source_tree.py", "scripts/verify-site.py", "scripts/site_freshness.py",
)
FRESHNESS_POLICY = (
    "Upstream changes are checked on a nominal 15-minute schedule. "
    "GitHub may delay or skip scheduled runs, and builds and deployment take additional time. "
    "The build time describes this snapshot, not the last check for changes."
)


def generator_revision(root: Path | None = None) -> str:
    """Identify the actual build inputs, including during uncommitted local builds."""
    root = root or Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in GENERATOR_INPUTS:
        content = (root / name).read_bytes()
        digest.update(name.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
    return digest.hexdigest()


def snapshot_freshness(built_at: str) -> str:
    return f"Snapshot built at **{built_at}** (UTC).\n\n{FRESHNESS_POLICY}"


def provenance_is_usable(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    sha, revision = value.get("upstream_sha"), value.get("generator_revision")
    count, built_at = value.get("file_count"), value.get("built_at")
    if (not isinstance(sha, str) or not SHA_RE.fullmatch(sha)
            or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision)
            or not isinstance(count, int) or isinstance(count, bool) or count <= 0
            or not isinstance(value.get("tool_version"), str) or not value["tool_version"]
            or not isinstance(built_at, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", built_at)):
        return False
    try:
        datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def deployed_provenance(url: str) -> object:
    # Ask the public deployment, never a build/cache record. A failed deployment
    # leaves the old pair visible, so the next scheduled run retries automatically.
    separator = "&" if "?" in url else "?"
    request = Request(f"{url}{separator}probe={time.time_ns()}", headers={
        "Cache-Control": "no-cache", "Accept": "application/json",
        "User-Agent": "trureturing-mdbook-freshness",
    })
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read(65537))
    except (OSError, URLError, HTTPException, ValueError, RecursionError):
        return None


def probe(upstream: str, ref: str, provenance_url: str, *, force: bool = False) -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--refs", upstream, ref],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
    )
    records = result.stdout.splitlines()
    if len(records) != 1:
        raise ValueError("upstream probe must resolve exactly one ref")
    sha, observed_ref = records[0].split("\t")
    if observed_ref != ref or not SHA_RE.fullmatch(sha):
        raise ValueError("upstream probe returned an invalid revision")
    revision = generator_revision()
    deployed = deployed_provenance(provenance_url) if not force else None
    if force:
        reason = "forced"
    elif not provenance_is_usable(deployed):
        reason = "unusable-deployed-provenance"
    elif deployed["upstream_sha"] != sha:
        reason = "upstream-changed"
    elif deployed["generator_revision"] != revision:
        reason = "generator-changed"
    else:
        reason = "unchanged"
    return {
        "upstream_sha": sha, "generator_revision": revision,
        "build_required": "false" if reason == "unchanged" else "true", "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=UPSTREAM_URL)
    parser.add_argument("--ref", default="refs/heads/dev")
    parser.add_argument("--provenance-url", default=PROVENANCE_URL)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = probe(args.upstream, args.ref, args.provenance_url, force=args.force)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.writelines(f"{key}={value}\n" for key, value in result.items())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"freshness probe: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
