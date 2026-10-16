from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest

import test_build_site as fixtures
from scripts import render_page_sources as sources


REPOSITORY = sources.UPSTREAM_REPOSITORY
MODULE = "D5/S0/Certificates/Example.lean"


def page(main: str, before: str = "") -> str:
    return (f"<!DOCTYPE HTML>\n<html><head><title>T</title></head><body>{before}\n"
            f'<div id="mdbook-content" class="content">\n<main>\n{main}\n</main>\n'
            '<nav class="nav-wrapper"></nav>\n</div></body></html>\n')


class PageSourcesTests(unittest.TestCase):
    setUp = fixtures.BuildSiteTests.setUp
    tearDown = fixtures.BuildSiteTests.tearDown
    git = fixtures.BuildSiteTests.git
    write = fixtures.BuildSiteTests.write
    commit = fixtures.BuildSiteTests.commit

    def prepare(self) -> None:
        self.write("Blueprint/Guide.md", "# Guide\n")
        self.write("Library/Words/note.md", "# Note\n")
        self.write(MODULE, "-- module\n")
        self.first = self.commit("Add the guide", "2026-07-01T09:00:00Z")
        self.git("checkout", "-b", "topic")
        self.write("Blueprint/Guide.md", "# Guide\n\nRevised.\n")
        self.write("Blueprint/notes.txt", "not published\n")
        self.topic = self.commit("Revise the guide", "2026-07-02T09:00:00Z")
        # A side-branch change that the merge below discards.
        self.write("Library/Words/note.md", "# Note\n\nDiscarded.\n")
        self.commit("Discarded note change", "2026-07-02T10:00:00Z")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "--no-commit", "topic")
        self.git("checkout", self.first, "--", "Library/Words/note.md")
        self.git("commit", "-m", "Merge pull request #1 from topic", date="2026-07-03T09:00:00Z")
        self.merge = self.git("rev-parse", "HEAD")
        self.write("Library/Words/note.md", "# Note\n\nEdited.\n")
        self.sha = self.commit("Edit <the> note & cost $$x$$", "2026-07-04T09:00:00Z")
        fixtures.build_site.build_site(self.upstream, self.output, upstream_sha=self.sha)
        self.book = self.root / "book"
        self.book.mkdir()
        shutil.copyfile(self.output / "provenance.json", self.book / "provenance.json")
        for path in ("Blueprint/Guide.html", "Library/Words/note.html", "index.html",
                     "open-problems.html"):
            self.html(path, page("<h1>Page</h1>"))

    def html(self, path: str, content: str) -> None:
        destination = self.book / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def test_history_credits_the_mainline_commits_that_changed_each_page(self) -> None:
        self.prepare()
        history = sources.file_history(self.upstream, self.sha)
        self.assertEqual(set(history), {b"Blueprint/Guide.md", b"Library/Words/note.md"})
        guide = history[b"Blueprint/Guide.md"]
        # The guide changed on the mainline when the pull request was merged.
        self.assertEqual(guide.newest, sources.Change(
            self.merge, "2026-07-03", "Test Author", "Merge pull request #1 from topic"))
        self.assertEqual(guide.oldest.sha, self.first)
        note = history[b"Library/Words/note.md"]
        self.assertEqual(note.newest, sources.Change(
            self.sha, "2026-07-04", "Test Author", "Edit <the> note & cost $$x$$"))
        self.assertEqual(note.oldest.sha, self.first)
        for path, item in history.items():
            with self.subTest(path=path):
                # The same commits as `git log --first-parent -- <path>`; the discarded
                # side-branch change to the note never appears.
                expected = self.git("log", "--first-parent", "--format=%H", self.sha, "--",
                                    path.decode()).split()
                self.assertEqual((item.newest.sha, item.oldest.sha), (expected[0], expected[-1]))

    def test_history_reads_unusual_published_paths(self) -> None:
        self.prepare()
        name = "Blueprint/line\nbreak \x1e 中文.md"
        self.write(name, "# Odd\n")
        sha = self.commit("odd path", "2026-07-05T09:00:00Z")
        history = sources.file_history(self.upstream, sha)
        self.assertEqual(history[name.encode()].newest.sha, sha)

    def test_each_published_page_gets_one_source_panel_at_the_end_of_main(self) -> None:
        self.prepare()
        result = subprocess.run(
            [sys.executable, str(fixtures.ROOT / "scripts/render_page_sources.py"),
             str(self.upstream), str(self.output), str(self.book)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "upstream_sha": self.sha, "source_panels": 2, "linked_declarations": 0,
            "pages_without_history": 0,
        })
        guide = (self.book / "Blueprint/Guide.html").read_text()
        before, panel, after = guide.partition(sources.PANEL_MARKER)
        self.assertEqual(before, page("<h1>Page</h1>").partition("</main>")[0])
        self.assertTrue(after.endswith("</aside>\n</main>\n<nav class=\"nav-wrapper\"></nav>\n</div></body></html>\n"))
        for url in (f"{REPOSITORY}/blob/{self.sha}/Blueprint/Guide.md",
                    f"{REPOSITORY}/commit/{self.sha}",
                    f"{REPOSITORY}/commit/{self.merge}",
                    f"{REPOSITORY}/commit/{self.first}",
                    f"{REPOSITORY}/commits/{self.sha}/Blueprint/Guide.md",
                    f"{REPOSITORY}/blame/{self.sha}/Blueprint/Guide.md",
                    f"{REPOSITORY}/blob/dev/Blueprint/Guide.md",
                    f"{REPOSITORY}/edit/dev/Blueprint/Guide.md"):
            self.assertIn(f'href="{url}"', after)
        self.assertIn('data-pagefind-ignore="all"', after)
        self.assertIn("by Test Author", after)
        self.assertIn("Merge pull request #1 from topic", after)
        self.assertIn("first added", after)
        note = (self.book / "Library/Words/note.html").read_text()
        self.assertIn("Edit &lt;the&gt; note &amp; cost &#36;&#36;x&#36;&#36;", note)
        self.assertNotIn("$", note)
        for path in ("index.html", "open-problems.html"):
            self.assertEqual((self.book / path).read_text(), page("<h1>Page</h1>"))
        fixtures.verify_site.validate_links(self.book, [b"Blueprint/Guide.html"])
        # Running again adds nothing.
        rendered = guide
        again = sources.render_page_sources(self.upstream, self.output, self.book)
        self.assertEqual((again["source_panels"], again["linked_declarations"]), (0, 0))
        self.assertEqual((self.book / "Blueprint/Guide.html").read_text(), rendered)

    def test_declaration_ids_in_inline_code_link_to_their_lean_module(self) -> None:
        self.prepare()
        url = f"{REPOSITORY}/blob/{self.sha}/{MODULE}"
        linked = f'<a href="{url}" title="Lean source on GitHub"><code>{{}}</code></a>'
        cases = (
            ("<code>D5/S0/Certificates/Example.result</code>",
             linked.format("D5/S0/Certificates/Example.result")),
            ("<code>D5/S0/Certificates/Example</code>", linked.format("D5/S0/Certificates/Example")),
            ("<code>D5/S0/Certificates/Example.Nested.name</code>",
             linked.format("D5/S0/Certificates/Example.Nested.name")),
            # No module file at the snapshot: nothing to point at.
            ("<code>D5/S0/Certificates/Missing.result</code>", None),
            # Already inside a link, a heading anchor or preformatted text.
            (f'<a href="#x"><code>D5/S0/Certificates/Example.result</code></a>', None),
            ('<h2><a class="header" href="#h">See <code>D5/S0/Certificates/Example</code></a></h2>', None),
            ("<pre><code>D5/S0/Certificates/Example</code></pre>", None),
            # Not exactly an ID: attributes, entities, markup or surrounding text.
            ('<code class="language-lean">D5/S0/Certificates/Example</code>', None),
            ("<code>D5/S0/Certificates/Example.result &amp; more</code>", None),
            ("<code>D5/S0/Certificates/<em>Example</em></code>", None),
            ("<code>see D5/S0/Certificates/Example</code>", None),
            ("<!-- <code>D5/S0/Certificates/Example</code> -->", None),
        )
        for original, expected in cases:
            with self.subTest(original=original):
                self.html("Blueprint/Guide.html", page(f"<p>{original}</p>"))
                content, count = sources.link_declarations(
                    page(f"<p>{original}</p>"), sources.regular_source_paths(self.upstream, self.sha),
                    self.sha)
                self.assertEqual(content, page(f"<p>{expected or original}</p>"))
                self.assertEqual(count, int(expected is not None))
        # Outside <main> (sidebar, head, scripts) nothing changes.
        outside = page("<p>text</p>", before="<code>D5/S0/Certificates/Example</code>")
        self.assertEqual(sources.link_declarations(outside, {MODULE.encode()}, self.sha), (outside, 0))

    def test_declarations_are_linked_on_every_page(self) -> None:
        self.prepare()
        self.html("open-problems.html", page("<li><code>D5/S0/Certificates/Example.result</code></li>"))
        result = sources.render_page_sources(self.upstream, self.output, self.book)
        self.assertEqual(result["linked_declarations"], 1)
        self.assertIn(f'href="{REPOSITORY}/blob/{self.sha}/{MODULE}"',
                      (self.book / "open-problems.html").read_text())

    def test_a_published_page_without_main_stops_the_build(self) -> None:
        self.prepare()
        self.html("Blueprint/Guide.html", "<p>no template</p>\n")
        with self.assertRaisesRegex(sources.PageSourceError, "no <main> element"):
            sources.render_page_sources(self.upstream, self.output, self.book)

    def test_rendered_provenance_must_match_the_source_snapshot(self) -> None:
        self.prepare()
        provenance = json.loads((self.book / "provenance.json").read_bytes())
        provenance["upstream_sha"] = "0" * 40
        (self.book / "provenance.json").write_text(json.dumps(provenance))
        with self.assertRaisesRegex(sources.PageSourceError, "provenance does not match"):
            sources.render_page_sources(self.upstream, self.output, self.book)


if __name__ == "__main__":
    unittest.main()
