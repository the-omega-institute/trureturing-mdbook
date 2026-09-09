from __future__ import annotations

import functools
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

import test_build_site as fixtures
from scripts import site_freshness as freshness


class FreshnessTests(unittest.TestCase):
    setUp = fixtures.BuildSiteTests.setUp
    tearDown = fixtures.BuildSiteTests.tearDown
    git = fixtures.BuildSiteTests.git
    write = fixtures.BuildSiteTests.write
    commit = fixtures.BuildSiteTests.commit

    def prepare(self):
        self.write("Blueprint/Test.md", "# Test\n")
        self.sha = self.commit("initial snapshot", "2026-07-01T00:00:00Z")
        self.payload = {
            "upstream_sha": self.sha, "generator_revision": freshness.generator_revision(),
            "file_count": 1, "built_at": "2026-07-01T00:00:00Z", "tool_version": "1.0.0",
        }
        self.status = 200
        self.body = json.dumps(self.payload).encode()
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                self.send_response(owner.status)
                self.end_headers()
                self.wfile.write(owner.body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=functools.partial(server.serve_forever, poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}/provenance.json"

    def run_probe(self, *, force=False):
        return freshness.probe(str(self.upstream), "refs/heads/main", self.url, force=force)

    def test_only_matching_successfully_deployed_pair_skips(self):
        self.prepare()
        unchanged = self.run_probe()
        self.assertEqual((unchanged["upstream_sha"], unchanged["build_required"], unchanged["reason"]),
                         (self.sha, "false", "unchanged"))
        self.write("Blueprint/Test.md", "# Next result\n")
        next_sha = self.commit("new result", "2026-07-02T00:00:00Z")
        for _ in range(2):  # No deployment success: the next check must retry.
            changed = self.run_probe()
            self.assertEqual((changed["upstream_sha"], changed["build_required"], changed["reason"]),
                             (next_sha, "true", "upstream-changed"))
        self.payload["upstream_sha"] = next_sha
        self.body = json.dumps(self.payload).encode()
        self.assertEqual(self.run_probe()["build_required"], "false")
        self.assertEqual(len(set(self.requests)), len(self.requests))

    def test_generator_change_builds_even_for_same_upstream(self):
        self.prepare()
        self.payload["generator_revision"] = "0" * 64
        self.body = json.dumps(self.payload).encode()
        result = self.run_probe()
        self.assertEqual((result["build_required"], result["reason"]), ("true", "generator-changed"))

    def test_failed_missing_and_malformed_provenance_always_retry(self):
        self.prepare()
        for status, body in ((404, b"missing"), (503, self.body), (200, b"{bad json"),
                             (200, b"null"), (200, b"[]"), (200, b"{}"), (200, b"\xff")):
            with self.subTest(status=status, body=body):
                self.status, self.body = status, body
                result = self.run_probe()
                self.assertEqual((result["build_required"], result["reason"]),
                                 ("true", "unusable-deployed-provenance"))
        self.status = 200
        for key, value in (("built_at", "yesterday"), ("built_at", "2026-02-30T00:00:00Z"),
                           ("file_count", True), ("file_count", 0), ("upstream_sha", "bad"),
                           ("generator_revision", None), ("tool_version", "")):
            with self.subTest(key=key, value=value):
                self.body = json.dumps({**self.payload, key: value}).encode()
                self.assertEqual(self.run_probe()["build_required"], "true")

    def test_network_failure_attempts_build_and_force_bypasses_provenance(self):
        self.prepare()
        self.url = "http://127.0.0.1:0/provenance.json"
        self.assertEqual(self.run_probe()["reason"], "unusable-deployed-provenance")
        result = self.run_probe(force=True)
        self.assertEqual((result["build_required"], result["reason"]), ("true", "forced"))
        self.assertEqual(self.requests, [])

    def test_cli_outputs_immutable_capture_and_manual_force(self):
        self.prepare()
        output = self.root / "github-output"
        command = [sys.executable, str(fixtures.ROOT / "scripts/site_freshness.py"),
                   "--upstream", str(self.upstream), "--ref", "refs/heads/main",
                   "--provenance-url", self.url, "--github-output", str(output)]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        parsed = json.loads(result.stdout)
        self.assertEqual(dict(line.split("=", 1) for line in output.read_text().splitlines()), parsed)
        self.assertEqual(parsed["build_required"], "false")
        forced = subprocess.run(command + ["--force"], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(forced.stdout)["build_required"], "true")
        # Build after HEAD moves still consumes the originally captured commit.
        self.write("Blueprint/Test.md", "# Future result\n")
        self.commit("head moved after probe", "2026-07-02T00:00:00Z")
        built = fixtures.build_site.build_site(self.upstream, self.output, upstream_sha=parsed["upstream_sha"])
        self.assertEqual(built["upstream_sha"], self.sha)
        self.assertEqual((self.output / "Blueprint/Test.md").read_text(), "# Test\n")

    def test_upstream_probe_failure_does_not_publish_invented_revision(self):
        self.prepare()
        output = self.root / "github-output"
        result = subprocess.run([sys.executable, str(fixtures.ROOT / "scripts/site_freshness.py"),
                                 "--upstream", str(self.upstream), "--ref", "refs/heads/missing",
                                 "--github-output", str(output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(output.exists())

    def test_generator_revision_tracks_build_inputs_and_visible_timestamp_is_shared(self):
        self.prepare()
        generator = self.root / "generator"
        for name in freshness.GENERATOR_INPUTS:
            destination = generator / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((fixtures.ROOT / name).read_bytes())
        original = freshness.generator_revision(generator)
        self.assertEqual(original, freshness.generator_revision())
        (generator / "scripts/open_problems.py").write_text("changed parser")
        self.assertNotEqual(original, freshness.generator_revision(generator))
        built = fixtures.build_site.build_site(self.upstream, self.output)
        self.assertEqual(built["generator_revision"], original)
        self.assertTrue(freshness.provenance_is_usable(built))
        for page in ("index.md", "open-problems.md"):
            text = (self.output / page).read_text()
            self.assertEqual(text.count(built["built_at"]), 1)
            self.assertIn("Snapshot built at", text)
            self.assertIn("15-minute schedule", text)
            self.assertIn("delay or skip", text)
        changed = {**built, "generator_revision": "0" * 64}
        (self.output / "provenance.json").write_text(json.dumps(changed))
        with self.assertRaisesRegex(fixtures.verify_site.VerificationError, "generator_revision"):
            fixtures.verify_site.load_provenance(self.output)


if __name__ == "__main__":
    unittest.main()
