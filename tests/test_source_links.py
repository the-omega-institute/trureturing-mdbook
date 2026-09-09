from __future__ import annotations

from html import escape
import json
import os
import shutil
import subprocess
import sys
import unittest
from urllib.parse import quote

import test_build_site as fixtures
from scripts import render_source_links as links


SOURCE = "D5/S1/Words/Compositions/ResidualPermutationSign.lean"
NOTE = "Library/Words/codex2026a392714residual.md"
# Exact link context at upstream 6737e68263e13766bc726dd5409e690ed708a094:365.
NOTE_LINE = f"正式代码见 [ResidualPermutationSign.lean](../../{SOURCE})。\n"


class SourceLinksTests(unittest.TestCase):
    setUp = fixtures.BuildSiteTests.setUp
    tearDown = fixtures.BuildSiteTests.tearDown
    git = fixtures.BuildSiteTests.git
    write = fixtures.BuildSiteTests.write
    commit = fixtures.BuildSiteTests.commit

    def prepare(self):
        self.write(NOTE, NOTE_LINE)
        self.write(SOURCE, "-- captured source\n")
        self.write("Blueprint/Guide.md", "# Guide\n")
        self.sha = self.commit("source-link snapshot", "2026-09-10T00:00:00Z")
        fixtures.build_site.build_site(self.upstream, self.output, upstream_sha=self.sha)
        self.book = self.root / "book"
        self.book.mkdir()
        shutil.copyfile(self.output / "provenance.json", self.book / "provenance.json")
        for path in ("Blueprint/Guide.html", "open-problems.html"):
            self.html(path, "<h1>Page</h1>\n")
        self.chapter = NOTE[:-3] + ".html"
        self.html(self.chapter, f'<p>正式代码见 <a href="../../{SOURCE}">ResidualPermutationSign.lean</a>。</p>\n'
                  '<a href="../../Blueprint/Guide.html#guide">Guide</a>\n')
        self.html("print.html", f'<p>正式代码见 <a href="{SOURCE}">ResidualPermutationSign.lean</a>。</p>\n'
                  '<a href="Blueprint/Guide.html#guide">Guide</a>\n')

    def html(self, path, content):
        destination = self.book / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content.encode("utf-8"))

    def permalink(self, path=SOURCE):
        return f"https://github.com/the-omega-institute/trureturing/blob/{self.sha}/{quote(path, safe='/')}"

    def test_actual_note_and_print_links_pass_unchanged_gate_with_identical_sources(self):
        self.prepare()
        with self.assertRaisesRegex(fixtures.verify_site.VerificationError, r"broken relative resources \(2\)"):
            fixtures.verify_site.verify(self.upstream, self.output, self.book)
        originals = {path: (self.book / path).read_bytes() for path in (self.chapter, "print.html")}
        source_blobs = fixtures.verify_site.projected_source_blobs(self.output)
        result = subprocess.run(
            [sys.executable, str(fixtures.ROOT / "scripts/render_source_links.py"),
             str(self.upstream), str(self.output), str(self.book)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "upstream_sha": self.sha, "rewritten_anchors": 2,
            "changed_pages": [self.chapter, "print.html"],
        })
        for path, old_href in ((self.chapter, "../../" + SOURCE), ("print.html", SOURCE)):
            self.assertEqual((self.book / path).read_bytes(), originals[path].replace(
                f'href="{old_href}"'.encode(), f'href="{self.permalink()}"'.encode()))
        self.assertEqual((self.output / NOTE).read_bytes(), NOTE_LINE.encode())
        self.assertEqual(fixtures.verify_site.projected_source_blobs(self.output), source_blobs)
        self.assertFalse((self.output / SOURCE).exists())
        self.assertFalse((self.book / SOURCE).exists())
        verified = fixtures.verify_site.verify(self.upstream, self.output, self.book)
        self.assertEqual(verified["broken_relative_resources"], 0)
        self.assertEqual(links.render_source_links(self.upstream, self.output, self.book)["rewritten_anchors"], 0)

    def test_upstream_advance_cannot_change_captured_targets_or_permalink_sha(self):
        self.prepare()
        (self.upstream / SOURCE).unlink()
        self.write(NOTE, "Changed after capture\n")
        self.write("D5/Future.lean", "-- only at future HEAD\n")
        later = self.commit("upstream advanced", "2026-09-10T01:00:00Z")
        self.assertNotEqual(later, self.sha)
        links.render_source_links(self.upstream, self.output, self.book)
        self.assertIn(self.permalink(), (self.book / "print.html").read_text())
        self.assertEqual(fixtures.verify_site.verify(self.upstream, self.output, self.book)["upstream_sha"], self.sha)
        self.html("future.html", '<a href="D5/Future.lean">Future</a>')
        links.render_source_links(self.upstream, self.output, self.book)
        self.assertEqual((self.book / "future.html").read_text(), '<a href="D5/Future.lean">Future</a>')
        with self.assertRaisesRegex(fixtures.verify_site.VerificationError, "broken relative"):
            fixtures.verify_site.verify(self.upstream, self.output, self.book)

    def test_general_regular_files_and_encoded_paths_keep_query_and_fragment(self):
        self.prepare()
        paths = ["scripts/prove.py", "settings.yaml", "LICENSE", "Blueprint/data.json", "Library/raw.html",
                 "D5/space #?汉%.txt"]
        for path in paths:
            self.write(path, "source\n")
        (self.upstream / paths[0]).chmod(0o755)
        sha = self.commit("other source file types", "2026-09-10T01:00:00Z")
        tracked = links.regular_source_paths(self.upstream, sha)
        for path in paths:
            with self.subTest(path=path):
                encoded = quote(path, safe="/")
                self.assertEqual(links.source_permalink(
                    "../../" + encoded + "?plain=1&view=source#L12-L20",
                    os.fsencode(self.chapter), self.book, tracked, sha),
                    f"{links.UPSTREAM_REPOSITORY}/blob/{sha}/{encoded}?plain=1&view=source#L12-L20")

    def test_only_real_anchor_values_change_and_all_other_html_bytes_survive(self):
        self.prepare()
        href = f"{SOURCE}?plain=1&amp;view=source#L12-L20"
        content = (
            '<!doctype html>\r\n<p>prose [x](D5/missing.lean) &amp; text</p>\r\n'
            f'<code>&lt;a href="{SOURCE}"&gt;example&lt;/a&gt;</code>\r\n'
            '<span class="katex"><math><mi>x</mi></math></span>\r\n'
            f'<!-- <a href="{SOURCE}">comment</a> -->\r\n'
            f'<script>const example = \'<a href="{SOURCE}">code</a>\';</script>\r\n'
            f'<textarea><a href="{SOURCE}">example text</a></textarea>\r\n'
            f'<title><a href="{SOURCE}">title text</a></title>\r\n'
            f'<a href="https://example.org/{SOURCE}?q=1&amp;y=2#L1">external</a>\r\n'
            '<a href="//example.org/source">protocol relative</a><a href="#local">local</a>\r\n'
            '<a href="Blueprint/Guide.html#guide">navigation</a>\r\n'
            f'<A title=\'an href="{SOURCE}" example\' HREF=\'{href}\'>Source</A>\r\n'
            f'<a href={SOURCE}>Unquoted</a>\r\n'
        )
        self.html("syntax.html", content)
        links.render_source_links(self.upstream, self.output, self.book)
        expected = content.replace(f"HREF='{href}'", f'HREF="{escape(self.permalink() + "?plain=1&view=source#L12-L20", quote=True)}"')
        expected = expected.replace(f"href={SOURCE}", f'href="{self.permalink()}"')
        self.assertEqual((self.book / "syntax.html").read_bytes(), expected.encode())

    def test_missing_targets_symlinks_and_embedded_resources_still_fail(self):
        self.prepare()
        os.symlink(SOURCE, self.upstream / "source-symlink.lean")
        sha = self.commit("symlink is not a regular blob", "2026-09-10T01:00:00Z")
        paths = links.regular_source_paths(self.upstream, sha)
        for href in ("D5/Missing.lean", "source-symlink.lean", SOURCE + "/", "Guide.html"):
            with self.subTest(href=href):
                self.assertIsNone(links.source_permalink(href, b"missing.html", self.book, paths, sha))
                self.html("missing.html", f'<a href="{href}">Missing</a>')
                links.render_source_links(self.upstream, self.output, self.book)
                with self.assertRaisesRegex(fixtures.verify_site.VerificationError, "broken relative"):
                    fixtures.verify_site.validate_links(self.book, [b"missing.html"])
        self.html("embedded.html", f'<img src="{SOURCE}">')
        links.render_source_links(self.upstream, self.output, self.book)
        with self.assertRaisesRegex(fixtures.verify_site.VerificationError, "broken relative"):
            fixtures.verify_site.validate_links(self.book, [b"embedded.html"])

    def test_missing_published_page_is_never_redirected_to_an_upstream_html_blob(self):
        self.prepare()
        self.write("Blueprint/Guide.html", "not the published Markdown page\n")
        sha = self.commit("HTML collision", "2026-09-10T01:00:00Z")
        (self.book / "Blueprint/Guide.html").unlink()
        self.assertIsNone(links.source_permalink(
            "Blueprint/Guide.html", b"print.html", self.book,
            links.regular_source_paths(self.upstream, sha), sha))
        links.render_source_links(self.upstream, self.output, self.book)
        with self.assertRaisesRegex(fixtures.verify_site.VerificationError, "missing HTML page mappings"):
            fixtures.verify_site.verify(self.upstream, self.output, self.book)

    def test_malformed_and_escaping_paths_fail_before_rewriting(self):
        self.prepare()
        paths = links.regular_source_paths(self.upstream, self.sha)
        for href in ("../../../" + SOURCE, "../../../%2e%2e/" + SOURCE,
                     "../../D5/%00source.lean", "../../D5/%0asource.lean", "../../D5/%zz.lean",
                     "..%5c..%5c" + SOURCE, "%2f" + SOURCE, "../../D5/\tsource.lean"):
            with self.subTest(href=href):
                with self.assertRaises(links.SourceLinkError):
                    links.source_permalink(href, os.fsencode(self.chapter), self.book, paths, self.sha)

    def test_source_and_rendered_provenance_must_agree(self):
        self.prepare()
        provenance = json.loads((self.book / "provenance.json").read_bytes())
        provenance["upstream_sha"] = "0" * 40
        (self.book / "provenance.json").write_text(json.dumps(provenance))
        with self.assertRaisesRegex(links.SourceLinkError, "provenance does not match"):
            links.render_source_links(self.upstream, self.output, self.book)


if __name__ == "__main__":
    unittest.main()
