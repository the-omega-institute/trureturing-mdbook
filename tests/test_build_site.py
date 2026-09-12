from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-site.py"
SPEC = importlib.util.spec_from_file_location("build_site", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
build_site = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_site
SPEC.loader.exec_module(build_site)

VERIFY_SCRIPT = ROOT / "scripts" / "verify-site.py"
VERIFY_SPEC = importlib.util.spec_from_file_location("verify_site", VERIFY_SCRIPT)
assert VERIFY_SPEC is not None and VERIFY_SPEC.loader is not None
verify_site = importlib.util.module_from_spec(VERIFY_SPEC)
sys.modules[VERIFY_SPEC.name] = verify_site
VERIFY_SPEC.loader.exec_module(verify_site)

PREPROCESSOR_SCRIPT = ROOT / "scripts" / "escape_pseudo_links.py"
PREPROCESSOR_SPEC = importlib.util.spec_from_file_location(
    "escape_pseudo_links", PREPROCESSOR_SCRIPT
)
assert PREPROCESSOR_SPEC is not None and PREPROCESSOR_SPEC.loader is not None
escape_pseudo_links = importlib.util.module_from_spec(PREPROCESSOR_SPEC)
sys.modules[PREPROCESSOR_SPEC.name] = escape_pseudo_links
PREPROCESSOR_SPEC.loader.exec_module(escape_pseudo_links)


class BuildSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.upstream = self.root / "upstream"
        self.output = self.root / "site-src"
        self.upstream.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", "test@example.invalid")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str, date: str | None = None) -> str:
        environment = os.environ.copy()
        if date is not None:
            environment["GIT_AUTHOR_DATE"] = date
            environment["GIT_COMMITTER_DATE"] = date
        return subprocess.run(
            ["git", "-C", os.fspath(self.upstream), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        ).stdout.strip()

    def write(self, relative: str, content: str) -> Path:
        path = self.upstream / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self, subject: str, date: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-m", subject, date=date)
        return self.git("rev-parse", "HEAD")

    def make_fixture(self) -> str:
        self.write("Blueprint/Guide.md", "# Published title\n\nSee [child](Part/Child.md).\n")
        child = self.write("Blueprint/Part/Child.md", "No heading here.\n")
        child.chmod(0o755)
        self.write("Blueprint/ignored.txt", "not Markdown\n")
        self.write("Blueprint/UPPER.MD", "# wrong suffix\n")
        self.write("outside.md", "# outside\n")
        os.symlink("Guide.md", self.upstream / "Blueprint" / "linked.md")
        self.commit("initial fixture", "2026-07-01T09:00:00+00:00")

        self.write("Blueprint/Guide.md", "# Published title\n\nFirst update.\n")
        self.commit("first same-day update", "2026-07-02T09:00:00+00:00")
        self.write("Blueprint/Guide.md", "# Published title\n\nSecond update.\n")
        return self.commit("second same-day update", "2026-07-02T10:00:00+00:00")

    def test_projection_summary_navigation_changelog_and_provenance(self) -> None:
        sha = self.make_fixture()

        provenance = build_site.build_site(self.upstream, self.output)

        self.assertEqual(provenance["upstream_sha"], sha)
        self.assertEqual(provenance["file_count"], 2)
        self.assertEqual(
            (self.output / "Blueprint" / "Guide.md").read_text(encoding="utf-8"),
            "# Published title\n\nSecond update.\n",
        )
        self.assertTrue(self.output.joinpath("Blueprint/Part/Child.md").is_file())
        self.assertFalse(self.output.joinpath("Blueprint/linked.md").exists())
        self.assertFalse(self.output.joinpath("Blueprint/ignored.txt").exists())
        self.assertFalse(self.output.joinpath("Blueprint/UPPER.MD").exists())
        self.assertTrue(
            self.output.joinpath("Blueprint/Part/Child.md").stat().st_mode & stat.S_IXUSR
        )

        summary = self.output.joinpath("SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn("[Published title](Blueprint/Guide.md)", summary)
        self.assertIn("[Child](Blueprint/Part/Child.md)", summary)
        self.assertIn(
            f"[Part]({build_site.nav_path(b'Blueprint/Part').decode()})", summary
        )
        self.assertTrue(
            (self.output / os.fsdecode(build_site.nav_path(b"Blueprint"))).is_file()
        )
        self.assertTrue(
            (self.output / os.fsdecode(build_site.nav_path(b"Blueprint/Part"))).is_file()
        )

        index = self.output.joinpath("index.md").read_text(encoding="utf-8")
        self.assertIn(sha, index)
        self.assertIn("source of mathematical truth is always the upstream repository", index)
        self.assertIn("upstream declares no content", index)
        self.assertIn("pagefind/pagefind-ui.js", index)

        changelog = self.output.joinpath("changelog.md").read_text(encoding="utf-8")
        section = changelog.split("## 2026-07-02", 1)[1].split("## ", 1)[0]
        # One list entry for that path on that day. Counting the raw path string
        # would be brittle: a linked path renders it twice, as label and target.
        guide_entries = [
            line
            for line in section.splitlines()
            if line.startswith("- ") and "Blueprint/Guide.md" in line
        ]
        self.assertEqual(len(guide_entries), 1)
        self.assertIn("[`Blueprint/Guide.md`](Blueprint/Guide.md)", guide_entries[0])
        self.assertIn("second same-day update", section)
        self.assertNotIn("first same-day update", section)
        self.assertIn(f"/commits/{sha}/", changelog)

        on_disk = json.loads(self.output.joinpath("provenance.json").read_text())
        self.assertEqual(on_disk, provenance)
        self.assertNotIn("prose_pseudo_link_count", provenance)

    def test_rebuild_removes_stale_derived_files(self) -> None:
        self.make_fixture()
        build_site.build_site(self.upstream, self.output)
        stale = self.output / "stale.md"
        stale.write_text("stale", encoding="utf-8")

        build_site.build_site(self.upstream, self.output)

        self.assertFalse(stale.exists())

    def test_empty_publication_set_fails_closed(self) -> None:
        self.write("Blueprint/not-markdown.txt", "nothing to publish\n")
        self.commit("no markdown", "2026-07-01T09:00:00+00:00")

        with self.assertRaisesRegex(build_site.BuildError, "selected no files"):
            build_site.build_site(self.upstream, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_emits_machine_readable_result(self) -> None:
        sha = self.make_fixture()

        completed = subprocess.run(
            [os.fspath(SCRIPT), os.fspath(self.upstream), os.fspath(self.output)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(json.loads(completed.stdout)["upstream_sha"], sha)

    def test_output_guard_rejects_protected_relations_and_unmarked_contents(self) -> None:
        with self.assertRaisesRegex(build_site.BuildError, "inside or above"):
            build_site.safe_output_path(self.upstream, self.upstream / "Blueprint")
        with self.assertRaisesRegex(build_site.BuildError, "inside or above"):
            build_site.safe_output_path(self.upstream, self.upstream.parent)

        linked_parent = self.root / "linked-parent"
        os.symlink(self.upstream, linked_parent)
        with self.assertRaisesRegex(build_site.BuildError, "inside or above"):
            build_site.safe_output_path(self.upstream, linked_parent / "Blueprint")

        existing = self.root / "existing"
        existing.mkdir()
        (existing / "user-data").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(build_site.BuildError, "non-empty unmarked"):
            build_site.safe_output_path(self.upstream, existing)

        (existing / "user-data").unlink()
        (existing / build_site.PROJECTION_MARKER).write_bytes(
            build_site.PROJECTION_MARKER_CONTENT
        )
        self.assertEqual(build_site.safe_output_path(self.upstream, existing), existing.resolve())

    def test_nav_encoding_is_flat_and_injective_for_md_named_directories(self) -> None:
        first = build_site.nav_path(b"Blueprint/Foo")
        second = build_site.nav_path(b"Blueprint/Foo.md/Bar")
        self.assertNotEqual(first, second)
        self.assertNotIn(b"/", first[len(b"_nav/") :])
        self.assertNotIn(b"/", second[len(b"_nav/") :])

    def test_blob_content_mutation_fails_closed(self) -> None:
        self.make_fixture()
        build_site.build_site(self.upstream, self.output)
        (self.output / "Blueprint/Guide.md").write_text("tampered\n", encoding="utf-8")
        book = self.root / "book"
        book.mkdir()
        with self.assertRaisesRegex(verify_site.VerificationError, "blob mismatch"):
            verify_site.verify(self.upstream, self.output, book)

    def test_unicode_space_and_long_paths_are_projected_and_verified(self) -> None:
        long_name = "long-" + "segment-" * 20 + ".md"
        paths = [
            "Blueprint/目录 with space/文件 名.md",
            "Blueprint/纯中文.md",
            f"Blueprint/long names/{long_name}",
        ]
        for index, path in enumerate(paths):
            self.write(path, f"# Page {index}\n")
        self.commit("unicode path fixture", "2026-07-03T09:00:00+00:00")

        build_site.build_site(self.upstream, self.output)
        summary = (self.output / "SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn(
            "Blueprint/目录%20with%20space/文件%20名.md",
            summary,
        )
        book = self.root / "book"
        book.mkdir()
        (book / "provenance.json").write_bytes(
            (self.output / "provenance.json").read_bytes()
        )
        (book / "open-problems.html").write_text("<h1>External open problems</h1>\n", encoding="utf-8")
        for path in paths:
            source_path = self.output / path
            self.assertTrue(source_path.is_file(), path)
            html_path = book / (path[:-3] + ".html")
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<h1>fixture</h1>\n", encoding="utf-8")

        result = verify_site.verify(self.upstream, self.output, book)

        self.assertEqual(result["source_files"], 3)
        self.assertEqual(result["mapped_pages"], 4)

    def test_publication_predicate_preserves_all_git_path_bytes(self) -> None:
        long_name = "x" * 197 + ".md"
        expected = {
            b'Blueprint/quote"name.md',
            b"Blueprint/back\\slash.md",
            b"Blueprint/line\nbreak.md",
            "Blueprint/中文 目录/中文名.md".encode(),
            b"Blueprint/space only.md",
            f"Blueprint/{long_name}".encode(),
            b"Blueprint/non-utf8-\xff.md",
        }
        non_utf8 = b"Blueprint/non-utf8-\xff.md"
        for path in expected - {non_utf8}:
            destination = self.upstream / os.fsdecode(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"# fixture\n")
        self.write("Blueprint/not-markdown.txt", "ignored\n")
        self.write("Blueprint/source.scribe.cs", "ignored\n")
        self.git("add", "-A")
        blob = subprocess.run(
            ["git", "-C", os.fspath(self.upstream), "hash-object", "-w", "--stdin"],
            input=b"# fixture\n",
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", os.fspath(self.upstream), "update-index", "-z", "--index-info"],
            input=b"100644 " + blob + b"\t" + non_utf8 + b"\0",
            check=True,
        )
        self.git("commit", "-m", "raw path fixture", date="2026-07-04T09:00:00+00:00")

        entries = build_site.list_source_entries(
            self.upstream, self.git("rev-parse", "HEAD")
        )

        selected = {entry.path for entry in entries}
        self.assertEqual(len(expected), 7)
        self.assertEqual(len(selected), len(expected))
        self.assertEqual(selected, expected)


class VerifySiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.book = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_commonmark_extensionless_pseudo_link_fails_closed(self) -> None:
        (self.book / "index.html").write_text(
            '<p>mu_H<a href="univ">d</a></p>', encoding="utf-8"
        )

        pseudo: list[dict[str, str]] = []
        with self.assertRaisesRegex(verify_site.VerificationError, "broken relative"):
            verify_site._validate_links(self.book, [b"index.html"], pseudo)
        self.assertEqual(pseudo, [{"source": "index.html", "href": "univ"}])

    def test_inline_code_link_text_does_not_create_a_pseudo_link(self) -> None:
        (self.book / "index.html").write_text(
            "<p><code>[x](univ)</code></p>", encoding="utf-8"
        )

        pseudo: list[dict[str, str]] = []
        self.assertEqual(verify_site._validate_links(self.book, [b"index.html"], pseudo), 0)
        self.assertEqual(pseudo, [])

    def test_extensionless_pseudo_link_with_query_fails_closed(self) -> None:
        (self.book / "index.html").write_text(
            '<p><a href="missing?x=1">missing</a></p>', encoding="utf-8"
        )

        pseudo: list[dict[str, str]] = []
        with self.assertRaisesRegex(verify_site.VerificationError, "broken relative"):
            verify_site._validate_links(self.book, [b"index.html"], pseudo)
        self.assertEqual(
            pseudo,
            [{"source": "index.html", "href": "missing?x=1"}],
        )

    def test_missing_static_relative_resource_fails_closed(self) -> None:
        (self.book / "index.html").write_text(
            '<a href="missing.html">missing</a>', encoding="utf-8"
        )

        with self.assertRaisesRegex(verify_site.VerificationError, "broken relative"):
            verify_site.validate_links(self.book, [b"index.html"])

    def test_partial_math_rendering_fails_closed(self) -> None:
        source = self.book / "source"
        book = self.book / "book"
        (source / "Blueprint").mkdir(parents=True)
        book.mkdir()
        (source / "Blueprint/Page.md").write_text(
            "good $x+1$ and bad $\\notacommand{x}$\n", encoding="utf-8"
        )
        (book / "Blueprint").mkdir()
        (book / "Blueprint/Page.html").write_text(
            '<span class="katex"><span>good</span></span>', encoding="utf-8"
        )
        with self.assertRaisesRegex(verify_site.VerificationError, "math token mismatch"):
            verify_site.validate_math(
                source,
                book,
                {b"Blueprint/Page.md"},
                [b"Blueprint/Page.html"],
            )


class EscapePseudoLinksTests(unittest.TestCase):
    def test_escapes_only_adjacent_prose_notation(self) -> None:
        content = (
            "mu_H[d](univ) and [real](target.md) and foo][kept](target.md)\n"
            "`code[d](univ)` and $math[d](univ)$ and $$display[d](univ)$$\n"
            "\\(paren[d](univ)\\) and \\[bracket[d](univ)\\]\n"
            "```text\nfenced[d](univ)\n```\n"
        )

        transformed = escape_pseudo_links.escape_pseudo_links(content)

        self.assertIn(r"mu_H\[d](univ)", transformed)
        self.assertIn("and [real](target.md)", transformed)
        self.assertIn("foo][kept](target.md)", transformed)
        self.assertIn("`code[d](univ)`", transformed)
        self.assertIn("$math[d](univ)$", transformed)
        self.assertIn("$$display[d](univ)$$", transformed)
        self.assertIn(r"\(paren[d](univ)\)", transformed)
        self.assertIn(r"\[bracket[d](univ)\]", transformed)
        self.assertIn("fenced[d](univ)", transformed)

    def test_standalone_powered_coefficients_from_upstream_remain_prose(self) -> None:
        # Both abstracts failed the release gate at upstream 4f945ce64c83.
        for expression in (
            "[X^(m-1)](1-A)^m/(1-mX)=0",
            "[X^(m-1)](1-A)^(m squared)/(1-m squared X)=0",
            "[x^n](1-(A+B))^2",
        ):
            for prefix in ("", "For every m>1, ", "The equation is\n"):
                with self.subTest(expression=expression, prefix=prefix):
                    content = prefix + expression + ".\n"
                    transformed = escape_pseudo_links.escape_pseudo_links(content)
                    self.assertEqual(transformed, prefix + "\\" + expression + ".\n")
                    self.assertEqual(escape_pseudo_links.escape_pseudo_links(transformed), transformed)

    def test_standalone_coefficient_rule_preserves_links_and_protected_regions(self) -> None:
        expression = "[X^(m-1)](1-A)^m"
        content = (
            "[real](1-A)^m and [X^n](notes.md)^2 and [X^n](../notes.md)^2\n"
            "[X^n](https://example.org)^2 and [X^n](#coefficient)^2\n"
            "[X^n](1-A) and [X^n](appendix)\n"
            f"`{expression}` and ``{expression}``\n"
            f"${expression}$ and $$\n{expression}\n$$\n"
            f"\\({expression}\\) and \\[\n{expression}\n\\]\n"
            f"```text\n{expression}\n```\n~~~\n{expression}\n~~~\n"
            f"\\{expression}\n"
        )
        self.assertEqual(escape_pseudo_links.escape_pseudo_links(content), content)

    def test_mdbook_protocol_transforms_nested_chapters_in_memory(self) -> None:
        book = {
            "items": [
                {
                    "Chapter": {
                        "content": "outer[d](univ)",
                        "sub_items": [
                            {"Chapter": {"content": "inner[x](y)", "sub_items": []}}
                        ],
                    }
                }
            ]
        }

        transformed = escape_pseudo_links.preprocess_book(book)

        chapter = transformed["items"][0]["Chapter"]
        self.assertEqual(chapter["content"], r"outer\[d](univ)")
        self.assertEqual(
            chapter["sub_items"][0]["Chapter"]["content"], r"inner\[x](y)"
        )


if __name__ == "__main__":
    unittest.main()


class PublicationRootsTests(unittest.TestCase):
    setUp = BuildSiteTests.setUp
    tearDown = BuildSiteTests.tearDown
    git = BuildSiteTests.git
    write = BuildSiteTests.write
    commit = BuildSiteTests.commit

    def fixture(self) -> str:
        self.write("Blueprint/Example.md", "# Example\n")
        self.write("Problems/alpha.md", (
            "---\nslug: alpha\nbibkey: paper2026\ndoi: 10.46298/dmtcs.17199\n"
            "triage: window\nmotivation_gids:\n  - D5/S1/Example\n---\n# Alpha\n"
        ))
        self.write("Library/Words/paper2026.md", (
            "---\nbibkey: paper2026\nauthors: A. Author\nyear: 2026\n"
            "title: Paper\ndoi: 10.46298/dmtcs.17199\nclaim: Question\n"
            "strata_touched:\n  - D5/S1/Example\nlicense: citation-only\ntriage: anchor\n"
            "---\n# Paper\n"
        ))
        self.write("Library/Words/Nested/context.md", "# Context\n")
        return self.commit("publish all roots", "2026-07-06T09:00:00+00:00")

    def test_publication_predicate_includes_problems_and_recursive_library_markdown(self) -> None:
        for path in (b"Problems/alpha.md", b"Library/paper.md", b"Library/Words/Nested/paper.md"):
            with self.subTest(path=path):
                self.assertTrue(build_site.is_published_path(path))
        for path in (b"Problems/nested/alpha.md", b"Library/note.txt", b"Library/note.MD",
                     b"ProblemsElsewhere/alpha.md", b"LibraryElsewhere/note.md"):
            with self.subTest(path=path):
                self.assertFalse(build_site.is_published_path(path))

    def test_new_roots_are_projected_navigable_and_in_changelog(self) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)
        summary = (self.output / "SUMMARY.md").read_text(encoding="utf-8")
        changelog = (self.output / "changelog.md").read_text(encoding="utf-8")
        for relative in ("Problems/alpha.md", "Library/Words/paper2026.md",
                         "Library/Words/Nested/context.md"):
            with self.subTest(path=relative):
                self.assertEqual((self.output / relative).read_bytes(), (self.upstream / relative).read_bytes())
                self.assertIn(f"]({relative})", summary)
                self.assertIn(f"]({relative})", changelog)
        for directory, child in ((b"Problems", "../Problems/alpha.md"),
                                 (b"Library", build_site.nav_path(b"Library/Words").decode().removeprefix("_nav/")),
                                 (b"Library/Words", "../Library/Words/paper2026.md"),
                                 (b"Library/Words/Nested", "../Library/Words/Nested/context.md")):
            navigation = build_site.nav_path(directory).decode()
            self.assertIn(f"]({navigation})", summary)
            self.assertIn(f"]({child})", (self.output / navigation).read_text(encoding="utf-8"))

    def check_blob_tampering_rejected(self, relative: str) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)
        expected = verify_site.expected_source_blobs(self.upstream, self.git("rev-parse", "HEAD"))
        self.assertIn(relative.encode(), expected)
        self.assertEqual(verify_site.projected_source_blobs(self.output), expected)
        (self.output / relative).write_bytes(b"tampered\n")
        with self.assertRaisesRegex(verify_site.VerificationError, "projected source blob mismatch.*" + relative):
            verify_site.verify(self.upstream, self.output, self.root / "book")

    def test_problems_projected_blob_bytes_are_verified(self) -> None:
        self.check_blob_tampering_rejected("Problems/alpha.md")

    def test_library_projected_blob_bytes_are_verified(self) -> None:
        self.check_blob_tampering_rejected("Library/Words/paper2026.md")


class ChangelogPredicateTests(BuildSiteTests):
    """The changelog must list only files the site actually publishes."""

    def test_is_published_path_rejects_emitter_sources(self) -> None:
        predicate = build_site.is_published_path
        self.assertTrue(predicate(b"Blueprint/D5/S1/Foo.md"))
        self.assertFalse(predicate(b"Blueprint/D5/S1/Foo.scribe.cs"))
        self.assertFalse(predicate(b"docs/Foo.md"))
        self.assertFalse(predicate(b"Blueprint/D5/S1/Foo.MD"))

    def test_changelog_omits_unpublished_emitter_sources(self) -> None:
        self.write("Blueprint/Page.md", "# Page\n\nBody.\n")
        self.write("Blueprint/Page.scribe.cs", "// emitter source\n")
        sha = self.commit("add page and emitter", "2026-07-03T09:00:00+00:00")

        changelog = build_site.build_changelog(self.upstream, sha)

        self.assertIn("Page.md", changelog)
        self.assertNotIn("scribe.cs", changelog)


class ChangelogLinkTests(BuildSiteTests):
    """Changelog paths link to the site, but only when the page still exists."""

    def test_present_path_is_linked_and_deleted_path_is_not(self) -> None:
        self.write("Blueprint/Kept.md", "# Kept\n\nBody.\n")
        self.write("Blueprint/Gone.md", "# Gone\n\nBody.\n")
        self.commit("add both pages", "2026-07-04T09:00:00+00:00")

        (self.upstream / "Blueprint" / "Gone.md").unlink()
        sha = self.commit("delete one page", "2026-07-05T09:00:00+00:00")

        published = frozenset(
            entry.path
            for entry in build_site.read_source_entries(
                self.upstream, sha, build_site.BuildError
            )
        )
        changelog = build_site.build_changelog(self.upstream, sha, published)

        # Still present: linked into the site.
        self.assertIn("[`Blueprint/Kept.md`](Blueprint/Kept.md)", changelog)
        # Deleted upstream: mentioned, but never linked (a link would dangle).
        self.assertIn("`Blueprint/Gone.md`", changelog)
        self.assertNotIn("](Blueprint/Gone.md)", changelog)

    def test_changelog_links_survive_the_release_link_gate(self) -> None:
        """The whole point of not linking deleted paths is that the gate is strict."""
        self.write("Blueprint/Kept.md", "# Kept\n\nBody.\n")
        self.write("Blueprint/Gone.md", "# Gone\n\nBody.\n")
        self.commit("add both pages", "2026-07-04T09:00:00+00:00")
        (self.upstream / "Blueprint" / "Gone.md").unlink()
        sha = self.commit("delete one page", "2026-07-05T09:00:00+00:00")

        build_site.build_site(self.upstream, self.output)
        changelog = (self.output / "changelog.md").read_text(encoding="utf-8")
        self.assertNotIn("](Blueprint/Gone.md)", changelog)
        self.assertIn(sha[:7], changelog)
