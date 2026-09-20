from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from itertools import permutations, product
from pathlib import Path
from unittest import mock


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
        self.assertIn(f"{build_site.UPSTREAM_REPOSITORY}/tree/{sha}", index)
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

    def test_single_child_directory_chains_collapse_into_one_menu_entry(self) -> None:
        # Blueprint/D5/S0/… is the only content under Blueprint and D5, so the menu
        # should not spend two levels on "Blueprint" and "D5" before anything readable.
        self.write("Blueprint/D5/S0/Deep/Page.md", "# Deep page\n")
        self.write("Blueprint/D5/S0/Top.md", "# Top page\n")
        self.write("Library/Words/note.md", "# Note\n")
        self.commit("chains", "2026-07-03T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        summary = (self.output / "SUMMARY.md").read_text(encoding="utf-8")
        leaf = build_site.nav_path(b"Blueprint/D5/S0").decode()
        self.assertEqual(summary.split("- [External open problems](open-problems.md)\n", 1)[1], (
            f"- [Blueprint / D5 / S0]({leaf})\n"
            f"  - [Deep]({build_site.nav_path(b'Blueprint/D5/S0/Deep').decode()})\n"
            "    - [Deep page](Blueprint/D5/S0/Deep/Page.md)\n"
            "  - [Top page](Blueprint/D5/S0/Top.md)\n"
            f"- [Library / Words]({build_site.nav_path(b'Library/Words').decode()})\n"
            "  - [Note](Library/Words/note.md)\n"
        ))
        for skipped in (b"Blueprint", b"Blueprint/D5", b"Library"):
            self.assertFalse((self.output / os.fsdecode(build_site.nav_path(skipped))).exists(), skipped)
        page = (self.output / leaf).read_text(encoding="utf-8")
        self.assertTrue(page.startswith("# Blueprint / D5 / S0\n\nNavigation page for `Blueprint/D5/S0/`"))
        self.assertIn("- [Deep](" + build_site.nav_path(b"Blueprint/D5/S0/Deep").decode().removeprefix("_nav/") + ")", page)
        self.assertIn("- [Top page](../Blueprint/D5/S0/Top.md)", page)
        book = self.root / "book"
        book.mkdir()
        (book / "provenance.json").write_bytes((self.output / "provenance.json").read_bytes())
        (book / "open-problems.html").write_text("<h1>External open problems</h1>\n", encoding="utf-8")
        for path in ("Blueprint/D5/S0/Deep/Page.md", "Blueprint/D5/S0/Top.md", "Library/Words/note.md"):
            destination = book / (path[:-3] + ".html")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("<h1>x</h1>", encoding="utf-8")
        verify_site.verify(self.upstream, self.output, book)

    def test_a_directory_with_a_page_and_a_subdirectory_keeps_its_own_menu_entry(self) -> None:
        self.make_fixture()
        build_site.build_site(self.upstream, self.output)
        summary = (self.output / "SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn(f"- [Blueprint]({build_site.nav_path(b'Blueprint').decode()})\n", summary)
        self.assertNotIn("Blueprint / Part", summary)

    def test_book_menu_folds_by_default_and_shows_no_section_numbers(self) -> None:
        book_toml = (ROOT / "book.toml").read_text(encoding="utf-8")
        self.assertIn("no-section-label = true", book_toml)
        fold = book_toml.split("[output.html.fold]", 1)[1].split("\n[", 1)[0]
        self.assertIn("enable = true", fold)
        self.assertIn("level = 0", fold)

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


class EntranceRouteTests(unittest.TestCase):
    setUp = BuildSiteTests.setUp
    tearDown = BuildSiteTests.tearDown
    git = BuildSiteTests.git
    write = BuildSiteTests.write
    commit = BuildSiteTests.commit

    EXAMPLES = (
        "Blueprint/D5/S0/Certificates/GreathouseLogTwoFloorRefutation.md",
        "Blueprint/D5/S3/Quantum/Entanglement/LocalMarginalCorrelationBlindSpot.md",
    )
    FALLBACK = "[Which questions could I explore?]"

    def test_home_routes_follow_exact_selected_paths(self) -> None:
        # Matching basenames at other paths must not select a curated route.
        for path in self.EXAMPLES:
            self.write(f"Blueprint/Other/{Path(path).name}", "# Other page\n")
        for selected in ((), self.EXAMPLES[:1], self.EXAMPLES[1:], self.EXAMPLES):
            with self.subTest(selected=selected):
                for path in self.EXAMPLES:
                    if path in selected:
                        self.write(path, "# Example\n")
                    else:
                        (self.upstream / path).unlink(missing_ok=True)
                sha = self.commit("example selection", "2026-07-07T09:00:00+00:00")

                build_site.build_site(self.upstream, self.output, upstream_sha=sha)
                index = (self.output / "index.md").read_text(encoding="utf-8")

                for path in self.EXAMPLES:
                    target = f"]({path})"
                    if path in selected:
                        self.assertIn(target, index)
                        self.assertTrue((self.output / path).is_file())
                    else:
                        self.assertNotIn(target, index)
                self.assertEqual(self.FALLBACK in index, not selected)
                self.assertIn(
                    f"{build_site.UPSTREAM_REPOSITORY}/blob/dev/README.md#three-places-to-look",
                    index,
                )
                self.assertIn("](open-problems.md)", index)
                self.assertTrue((self.output / "open-problems.md").is_file())

    def test_historical_snapshot_uses_fallback_even_when_head_has_examples(self) -> None:
        self.write("Blueprint/Page.md", "# Historical page\n")
        historical_sha = self.commit("small snapshot", "2026-07-07T09:00:00+00:00")
        for path in self.EXAMPLES:
            self.write(path, "# New example\n")
        self.commit("later examples", "2026-07-08T09:00:00+00:00")

        build_site.build_site(self.upstream, self.output, upstream_sha=historical_sha)
        index = (self.output / "index.md").read_text(encoding="utf-8")

        self.assertIn(self.FALLBACK, index)
        for path in self.EXAMPLES:
            self.assertNotIn(f"]({path})", index)
            self.assertFalse((self.output / path).exists())


class WorkedEscapeRouteTests(unittest.TestCase):
    setUp = BuildSiteTests.setUp
    tearDown = BuildSiteTests.tearDown
    git = BuildSiteTests.git
    write = BuildSiteTests.write
    commit = BuildSiteTests.commit

    # Deliberately independent of the generator's selection constants.
    SOURCES = tuple(
        f"D5/S3/ConceptDynamics/InformationEscape/{name}.lean"
        for name in ("EscapePairs", "StructuralNovelty", "TheoremUnit")
    )
    BLUEPRINT = "Blueprint/D5/S3/ConceptDynamics/InformationEscape/EscapePairs.md"

    def fixture(self) -> str:
        self.write("Blueprint/Page.md", "# A published page\n")
        for source in self.SOURCES:
            self.write(source, "-- Source destination fixture, not Lean validation.\n")
        return self.commit("source references", "2026-07-09T09:00:00+00:00")

    def assert_route(self, present: bool) -> str:
        page = self.output / "information-escape.md"
        self.assertEqual(page.is_file(), present)
        index = (self.output / "index.md").read_text()
        summary = (self.output / "SUMMARY.md").read_text()
        self.assertEqual("](information-escape.md)" in index, present)
        self.assertEqual("](information-escape.md)" in summary, present)
        if present:
            self.assertIn(
                "- [Home](index.md)\n"
                "- [What can these observations distinguish?](information-escape.md)\n",
                summary,
            )
        else:
            self.assertIn("Current upstream methodology", index)
            self.assertIn("/blob/dev/README.md#information-escape", index)
        return page.read_text() if present else ""

    def test_generated_groups_and_unique_sets_by_independent_enumeration(self) -> None:
        sha = self.fixture()
        provenance = build_site.build_site(self.upstream, self.output, upstream_sha=sha)
        page = self.assert_route(True)

        class TableReader(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows, self.captions = [], []
                self.cell = None
                self.in_caption = False

            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self.rows.append([])
                elif tag in ("th", "td"):
                    self.cell = [tag, dict(attrs).get("scope"), ""]
                    self.rows[-1].append(self.cell)
                elif tag == "caption":
                    self.in_caption = True
                    self.captions.append("")

            def handle_data(self, data):
                if self.cell is not None:
                    self.cell[2] += data
                if self.in_caption:
                    self.captions[-1] += data

            def handle_endtag(self, tag):
                if tag in ("th", "td"):
                    self.cell = None
                elif tag == "caption":
                    self.in_caption = False

        table = TableReader()
        table.feed(page)
        self.assertEqual(table.captions, ["All four readout selections on the same four states"])
        self.assertEqual(table.rows[0], [
            ["th", "col", "Readouts"],
            ["th", "col", "Indistinguishable groups"],
            ["th", "col", "Escape pairs"],
        ])
        states = ["".join(bits) for bits in product("01", repeat=2)]
        distinct_pairs = set(permutations(states, 2))
        selections = {"None": (), "First": (0,), "Second": (1,), "Both": (0, 1)}
        escapes = {}
        self.assertEqual(len(table.rows), 5)
        for row, (label, coordinates) in zip(table.rows[1:], selections.items(), strict=True):
            self.assertEqual(row[0], ["th", "row", label])
            groups = {}
            for state in states:
                observation = tuple(state[i] for i in coordinates)
                groups.setdefault(observation, set()).add(state)
            shown_groups = [
                frozenset(group.split(", ")) for group in re.findall(r"\{([^}]+)\}", row[1][2])
            ]
            self.assertCountEqual(shown_groups, map(frozenset, groups.values()), label)
            escapes[label] = {
                (a, b) for a, b in distinct_pairs if all(a[i] == b[i] for i in coordinates)
            }
            self.assertEqual(int(row[2][2]), len(escapes[label]), label)
            self.assertEqual(len(escapes[label]), sum(len(g) * (len(g) - 1) for g in groups.values()))

        for entry, remaining in (("first", "Second"), ("second", "First")):
            line = next(line for line in page.splitlines() if line.startswith(f"- **{entry}**:"))
            pairs = re.findall(r"`\(([01]{2}), ([01]{2})\)`", line)
            self.assertCountEqual(pairs, escapes[remaining] - escapes["Both"])
        self.assertEqual(len(escapes["None"] - escapes["First"]), 8)
        self.assertEqual(len(escapes["First"] - escapes["Both"]), 4)
        self.assertIn("adding first removes **8**", page)
        self.assertIn("**4 and 4**, not 8 and 4", page)
        for question in (
            "Where did information escape?", "How is that escape addressed?",
            "What new information emerges?", "Where does information continue to escape?",
        ):
            self.assertIn(f"## {question}", page)
        for boundary in (
            "not a new Lean theorem or a certified judge run", "Statement := True",
            "proof := True.intro", "NativeTheoremUnit", "LegacyPrimitiveRealization",
            "under development", "Observe warnings", "not admission blockers",
        ):
            self.assertIn(boundary, page)
        for source in self.SOURCES:
            self.assertIn(f"/blob/{sha}/{source})", page)
        self.assertNotRegex(page, r"\.lean#L\d")
        self.assertIn("/blob/dev/README.md#information-escape", page)
        self.assertIn("[**Normative Draft**]", page)
        self.assertIn("/blob/dev/docs/develop/spec/", page)
        self.assertNotIn("<script", page)
        self.assertNotIn(self.BLUEPRINT, page)
        self.assertEqual(provenance["file_count"], 1)
        self.assertEqual((self.output / "Blueprint/Page.md").read_bytes(),
                         (self.upstream / "Blueprint/Page.md").read_bytes())

    def test_tutorial_links_survive_the_existing_prose_preprocessor(self) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)
        page = self.assert_route(True)
        self.assertEqual(escape_pseudo_links.escape_pseudo_links(page), page)

    def test_all_sources_required_despite_decoys_or_worktree_files(self) -> None:
        self.write("Blueprint/Page.md", "# Published\n")
        for source in self.SOURCES:
            self.write(f"elsewhere/{Path(source).name}", "-- Decoy\n")
        for present in ((), self.SOURCES[:1], self.SOURCES[:2], self.SOURCES):
            with self.subTest(present=present):
                for source in self.SOURCES:
                    if source in present:
                        self.write(source, "-- Selected\n")
                    else:
                        (self.upstream / source).unlink(missing_ok=True)
                sha = self.commit("partial tree", "2026-07-09T09:00:00+00:00")
                for source in self.SOURCES:
                    self.write(source, "-- Uncommitted worktree file\n")
                build_site.build_site(self.upstream, self.output, upstream_sha=sha)
                self.assert_route(present == self.SOURCES)

    def test_each_required_path_must_be_regular_not_a_symlink(self) -> None:
        self.fixture()
        for source in self.SOURCES:
            with self.subTest(source=source):
                entry = self.upstream / source
                entry.unlink()
                os.symlink("EscapePairs.lean" if entry.name != "EscapePairs.lean"
                           else "StructuralNovelty.lean", entry)
                sha = self.commit("symlink source", "2026-07-09T09:00:00+00:00")
                build_site.build_site(self.upstream, self.output, upstream_sha=sha)
                self.assert_route(False)
                entry.unlink()
                self.write(source, "-- Restored regular source\n")

    def test_captured_history_controls_additions_removals_and_stale_cleanup(self) -> None:
        self.write("Blueprint/Page.md", "# Earlier snapshot\n")
        before = self.commit("before references", "2026-07-08T09:00:00+00:00")
        complete = self.fixture()
        # A later HEAD and complete working tree must not create the historical route.
        build_site.build_site(self.upstream, self.output, upstream_sha=before)
        self.assert_route(False)
        (self.upstream / self.SOURCES[-1]).unlink()
        after = self.commit("remove a reference", "2026-07-10T09:00:00+00:00")
        # A missing file at HEAD must not suppress the captured historical route.
        build_site.build_site(self.upstream, self.output, upstream_sha=complete)
        page = self.assert_route(True)
        self.assertIn(f"/blob/{complete}/", page)
        self.assertNotIn(f"/blob/{after}/", page)
        build_site.build_site(self.upstream, self.output, upstream_sha=after)
        self.assert_route(False)

    def test_blueprint_link_requires_exact_published_regular_path(self) -> None:
        self.fixture()
        self.write("Blueprint/Elsewhere/EscapePairs.md", "# Decoy\n")
        for kind in ("missing", "symlink", "regular"):
            with self.subTest(kind=kind):
                entry = self.upstream / self.BLUEPRINT
                entry.parent.mkdir(parents=True, exist_ok=True)
                if kind == "symlink":
                    os.symlink("../../../../Elsewhere/EscapePairs.md", entry)
                elif kind == "regular":
                    entry.unlink()
                    self.write(self.BLUEPRINT, "# Published EscapePairs\n")
                sha = self.commit("Blueprint selection", "2026-07-09T09:00:00+00:00")
                build_site.build_site(self.upstream, self.output, upstream_sha=sha)
                page = self.assert_route(True)
                self.assertEqual(f"]({self.BLUEPRINT})" in page, kind == "regular")
                self.assertEqual(entry.is_symlink(), kind == "symlink")

    def test_git_tree_failure_is_an_error_and_preserves_previous_output(self) -> None:
        sha = self.fixture()
        build_site.build_site(self.upstream, self.output, upstream_sha=sha)
        previous = (self.output / "provenance.json").read_bytes()
        original_run = subprocess.run

        def fail_full_tree(command, **kwargs):
            if command[3:] == ["ls-tree", "-r", "-z", sha]:
                raise subprocess.CalledProcessError(1, command, stderr=b"tree unavailable")
            return original_run(command, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=fail_full_tree):
            with self.assertRaisesRegex(build_site.BuildError, "source references.*tree unavailable"):
                build_site.build_site(self.upstream, self.output, upstream_sha=sha)
        self.assertEqual((self.output / "provenance.json").read_bytes(), previous)
        self.assert_route(True)

    def test_existing_link_gate_requires_tutorial_html(self) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)
        book = self.root / "book"
        book.mkdir()
        (book / "index.html").write_text('<a href="information-escape.html">Worked route</a>')
        with self.assertRaisesRegex(verify_site.VerificationError, "broken relative.*information-escape"):
            verify_site.validate_links(book, [b"index.html"])
        (book / "information-escape.html").write_text("<h1>Worked route</h1>")
        self.assertEqual(verify_site.validate_links(book, [b"index.html"]), 1)


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
            # Library/Words/oeis2026triage0911b.md at upstream fe47b6c24bb2.
            "[X^(m−1)](1−F)^m/(1−mX)=0",
            "[X^(m−1)](1-F)^m",
            "[X^(m-1)](1−F)^m",
        ):
            for prefix in ("", "For every m>1, ", "The equation is\n"):
                with self.subTest(expression=expression, prefix=prefix):
                    content = prefix + expression + ".\n"
                    transformed = escape_pseudo_links.escape_pseudo_links(content)
                    self.assertEqual(transformed, prefix + "\\" + expression + ".\n")
                    self.assertEqual(escape_pseudo_links.escape_pseudo_links(transformed), transformed)

    def test_standalone_coefficient_rule_preserves_links_and_protected_regions(self) -> None:
        for expression in ("[X^(m-1)](1-A)^m", "[X^(m−1)](1−F)^m"):
            with self.subTest(expression=expression):
                content = (
                    "[real](1-A)^m and [X^n](notes.md)^2 and [X^n](../notes.md)^2\n"
                    "[X^n](https://example.org)^2 and [X^n](#coefficient)^2\n"
                    "[X^n](1−F.md)^2 and [X^n](../1−F)^2 and [X^n](#1−F)^2\n"
                    "[X^n](1-A) and [X^n](1−F) and [X^n](appendix)\n"
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
                                 (b"Library/Words", "../Library/Words/paper2026.md"),
                                 (b"Library/Words/Nested", "../Library/Words/Nested/context.md")):
            navigation = build_site.nav_path(directory).decode()
            self.assertIn(f"]({navigation})", summary)
            self.assertIn(f"]({child})", (self.output / navigation).read_text(encoding="utf-8"))
        # Library holds only Words, so the two fold into one menu entry.
        self.assertIn(f"- [Library / Words]({build_site.nav_path(b'Library/Words').decode()})", summary)
        self.assertFalse((self.output / build_site.nav_path(b"Library").decode()).exists())

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
