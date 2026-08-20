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
        self.assertIn("数学真源", index)
        self.assertIn("上游当前未声明内容许可证", index)
        self.assertIn("pagefind/pagefind-ui.js", index)

        changelog = self.output.joinpath("changelog.md").read_text(encoding="utf-8")
        section = changelog.split("## 2026-07-02", 1)[1].split("## ", 1)[0]
        self.assertEqual(section.count("Blueprint/Guide.md"), 1)
        self.assertIn("second same-day update", section)
        self.assertNotIn("first same-day update", section)
        self.assertIn(f"/commits/{sha}/Blueprint/", changelog)

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
        for path in paths:
            source_path = self.output / path
            self.assertTrue(source_path.is_file(), path)
            html_path = book / (path[:-3] + ".html")
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<h1>fixture</h1>\n", encoding="utf-8")

        result = verify_site.verify(self.upstream, self.output, book)

        self.assertEqual(result["source_files"], 3)
        self.assertEqual(result["mapped_pages"], 3)

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
