from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import search_titles, site_freshness


ROOT = Path(__file__).resolve().parents[1]


class SearchTitleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.book = self.root / "src", self.root / "book"
        self.source.mkdir()
        self.book.mkdir()
        self.provenance = dict(upstream_sha="a" * 40, generator_revision=site_freshness.generator_revision(),
                               built_at="2026-09-20T12:00:00Z", file_count=1, tool_version="1.0.0")
        self.write_provenance()

    def write_provenance(self):
        for directory in (self.source, self.book):
            (directory / "provenance.json").write_text(json.dumps(self.provenance))

    def test_native_titles_literal_paths_determinism_and_no_html_mutation(self):
        # Fixed expected document.title values, independent of the extractor.
        fixtures = {
            "index.html": ("Home - Book", "Home - Book"),
            "headed.html": ("Heading - Book", "Heading - Book"),
            "unheaded.html": ("Navigation name - Book", "Navigation name - Book"),
            "override.html": ("Override - Book", "Override - Book"),
            "目录/a %23#?.html": ("未入账 &amp; &quot;Q&quot; &lt;b&gt; - Book", '未入账 & "Q" <b> - Book'),
            "x/index.html": ("X directory - Book", "X directory - Book"),
            "x.html": ("X file - Book", "X file - Book"),
            "literal%2Fsegment.html": ("  &amp;lt;img&amp;gt; <b>x</b> - Book  ", "  &lt;img&gt; <b>x</b> - Book  "),
        }
        for path, (encoded, _) in fixtures.items():
            page = self.book / path
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(f'<html><head><script>"<title>fake</title>"</script><TITLE>{encoded}</TITLE>'
                            '</head><body><h1>Different body title</h1><title>Body title</title></body></html>')
        for path, head in {"helper.html": "", "blank.html": "<title> \t\n </title>",
                           "duplicate.html": "<title>one</title><title>two</title>"}.items():
            (self.book / path).write_text(f"<head>{head}</head><h1>Never inferred</h1>")
        before = {str(p): p.read_bytes() for p in self.book.rglob("*.html")}
        result = search_titles.generate(self.source, self.book)
        first = (self.book / result["map"]).read_bytes()
        value = json.loads(first)
        self.assertEqual(value["titles"], {path: expected for path, (_, expected) in fixtures.items()})
        self.assertEqual(value["version"], 1)
        self.assertEqual(result["titles"], len(fixtures))
        self.assertEqual(result["bytes"], len(first))
        search_titles.generate(self.source, self.book)
        self.assertEqual(first, (self.book / result["map"]).read_bytes())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.book.rglob("*.html")})
        self.assertEqual(list(self.source.iterdir()), [self.source / "provenance.json"])

    def test_identity_is_known_before_render_and_changes_with_every_provenance_input(self):
        expected = hashlib.sha256(("a" * 40 + "\0" + self.provenance["generator_revision"] +
                                   "\0" + self.provenance["built_at"]).encode()).hexdigest()
        result = search_titles.generate(self.source, self.book)
        self.assertEqual(result["map"], f"search-titles-{expected}.json")
        self.assertEqual(result["identity"], expected)
        script = search_titles.search_bootstrap(self.provenance["upstream_sha"],
                                                self.provenance["generator_revision"], self.provenance["built_at"])
        self.assertIn(f'startSearchTitles("{expected}")', script)
        args = [self.provenance[k] for k in ("upstream_sha", "generator_revision", "built_at")]
        for index in range(3):
            changed = args.copy()
            changed[index] += "different"
            self.assertNotEqual(expected, search_titles.title_identity(*changed))

    def test_provenance_failures_do_not_write_a_map(self):
        for change in ({"generator_revision": "0" * 64}, {"built_at": "invalid"}):
            with self.subTest(change=change):
                original = self.provenance.copy()
                self.provenance.update(change)
                self.write_provenance()
                with self.assertRaises(ValueError):
                    search_titles.generate(self.source, self.book)
                self.provenance = original
        self.write_provenance()
        (self.book / "provenance.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "rendered provenance"):
            search_titles.generate(self.source, self.book)
        self.assertFalse(list(self.book.glob("search-titles-*.json")))

    def test_browser_behavior_contract_under_node(self):
        subprocess.run(["node", "--test", str(ROOT / "tests/search_titles.test.cjs")], check=True)


if __name__ == "__main__":
    unittest.main()
