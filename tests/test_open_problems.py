from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_build_site as fixtures

build_site = fixtures.build_site
verify_site = fixtures.verify_site


class OpenProblemTests(unittest.TestCase):
    setUp = fixtures.BuildSiteTests.setUp
    tearDown = fixtures.BuildSiteTests.tearDown
    git = fixtures.BuildSiteTests.git
    write = fixtures.BuildSiteTests.write
    commit = fixtures.BuildSiteTests.commit

    def dossier(self, slug: str = "alpha", triage: str = "window") -> str:
        return (
            f"---\nslug: {slug}\nbibkey: paper2026\narxiv_id: 2601.12345\n"
            f"triage: {triage}\nmotivation_gids:\n  - D5/S1/Example\n---\n\n"
            f"# Problem {slug}\n\nLater external resolutions have not been checked.\n"
        )

    def library(self) -> str:
        return (
            "---\nbibkey: paper2026\nauthors: A. Author\nyear: 2026\n"
            "title: Example paper\ndoi: 10.48550/arXiv.2601.12345\n"
            "claim: An external question.\nstrata_touched:\n  - D5/S1/Example\n"
            "license: citation-only\ntriage: anchor\n---\n\n# Example paper\n"
        )

    def marker(self, slug: str = "alpha", kind: str = "proved") -> str:
        payload = json.dumps({"problem_slug": slug, "resolution_kind": kind})
        return f"<!-- scribe-open-problem-resolution-v1 {payload} -->\n"

    def fixture(self, markers: str = "") -> str:
        self.write("Blueprint/Example.md", "# Example\n\n" + markers)
        self.write("Problems/alpha.md", self.dossier())
        self.write("Problems/beta.md", self.dossier("beta", "wall"))
        self.write("Library/Words/paper2026.md", self.library())
        return self.commit("open problem fixture", "2026-07-06T09:00:00+00:00")

    def check_rejected(self, path: str, value: str, diagnostic: str) -> None:
        self.write(path, value)
        self.commit("invalid open problem input", "2026-07-07T09:00:00+00:00")
        with self.assertRaisesRegex(build_site.BuildError, diagnostic):
            build_site.build_site(self.upstream, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_unknown_marker_version(self) -> None:
        self.fixture()
        self.check_rejected(
            "Blueprint/Example.md", self.marker().replace("-v1 ", "-v2 "),
            "marker.*version",
        )

    def test_rejects_non_object_marker_payloads(self) -> None:
        self.fixture()
        for payload in ('[]', 'null', 'true', '42', '"alpha"'):
            with self.subTest(payload=payload):
                self.check_rejected(
                    "Blueprint/Example.md",
                    f"<!-- scribe-open-problem-resolution-v1 {payload} -->\n",
                    "marker.*object",
                )

    def test_rejects_missing_unknown_and_duplicate_marker_keys(self) -> None:
        self.fixture()
        for payload in (
            '{}', '{"problem_slug":"alpha"}',
            '{"problem_slug":"alpha","resolution_kind":"proved","gid":"D5/Foo.bar"}',
            '{"problem_slug":"alpha","problem_slug":"beta","resolution_kind":"proved"}',
        ):
            with self.subTest(payload=payload):
                self.check_rejected(
                    "Blueprint/Example.md",
                    f"<!-- scribe-open-problem-resolution-v1 {payload} -->\n",
                    "marker.*(keys|key)",
                )

    def test_rejects_malformed_marker_syntax_and_values(self) -> None:
        self.fixture()
        for marker in (
            self.marker().replace('"proved"', 'NaN'),
            self.marker().replace('"proved"', 'false'),
            self.marker(kind="resolved"), self.marker(slug="Alpha"),
            self.marker().replace(" -->", ""),
            self.marker().replace("-v1 ", "-v1\n"),
            self.marker().replace('"proved"', '"proved",'),
            "prefix " + self.marker(),
        ):
            with self.subTest(marker=marker):
                self.check_rejected("Blueprint/Example.md", marker, "marker")

    def test_rejects_marker_for_unknown_problem(self) -> None:
        self.fixture()
        self.check_rejected("Blueprint/Example.md", self.marker("missing"), "unknown.*slug")

    def test_rejects_duplicate_resolution_slugs_across_pages(self) -> None:
        self.fixture(self.marker())
        self.check_rejected("Blueprint/Other.md", self.marker(kind="refuted"), "duplicate.*slug")

    def test_rejects_out_of_order_marker_slugs(self) -> None:
        self.fixture()
        self.check_rejected(
            "Blueprint/Example.md", self.marker("beta") + self.marker("alpha"),
            "out.of.order.*slug",
        )

    def test_rejects_malformed_dossier_front_matter(self) -> None:
        self.fixture()
        original = self.dossier()
        for dossier in (
            original.removeprefix("---\n"), original.replace("\n---\n", "\n", 1),
            original.replace("triage: window\n", ""),
            original.replace("triage: window", "triage: open"),
            original.replace("triage: window", "triage: window\nunknown: true"),
            original.replace("triage: window", "triage: window\ntriage: wall"),
            original.replace("arxiv_id: 2601.12345", "arxiv_id: bad-id"),
            original.replace("slug: alpha", "slug: beta"),
            original.replace("  - D5/S1/Example\n", ""),
            original.replace("  - D5/S1/Example", "  - ../escape"),
            original.replace("  - D5/S1/Example", "  - D5/S1/Example\n  - D5/S1/Example"),
            original.replace("\n", "\r\n"), "\ufeff" + original,
        ):
            with self.subTest(dossier=dossier):
                self.check_rejected("Problems/alpha.md", dossier, "Problems/alpha.md")

    def test_rejects_doi_migration_until_schema_is_supported(self) -> None:
        self.fixture()
        self.check_rejected(
            "Problems/alpha.md",
            self.dossier().replace("arxiv_id: 2601.12345", "doi: 10.48550/arXiv.2601.12345"),
            "schema.*arxiv_id.*doi",
        )

    def test_rejects_non_utf8_dossier(self) -> None:
        self.fixture()
        (self.upstream / "Problems/alpha.md").write_bytes(b"\xff\n")
        self.commit("invalid encoding", "2026-07-07T09:00:00+00:00")
        with self.assertRaisesRegex(build_site.BuildError, "UTF-8"):
            build_site.build_site(self.upstream, self.output)

    def test_rejects_unsafe_or_nested_problem_inputs(self) -> None:
        self.fixture()
        path = self.upstream / "Problems/alpha.md"
        path.unlink()
        os.symlink("beta.md", path)
        self.commit("symlink dossier", "2026-07-07T09:00:00+00:00")
        with self.assertRaisesRegex(build_site.BuildError, "regular.*problem|problem.*regular"):
            build_site.build_site(self.upstream, self.output)
        path.unlink()
        self.write("Problems/alpha.md", self.dossier())
        self.check_rejected("Problems/nested/extra.md", self.dossier("extra"), "problem.*path")

    def test_rejects_missing_duplicate_or_inconsistent_literature(self) -> None:
        self.fixture()
        for note in (
            self.library().replace("doi: 10.48550/arXiv.2601.12345\n", ""),
            self.library().replace("10.48550/arXiv.2601.12345", "not-a-doi"),
            self.library().replace("2601.12345", "2601.99999"),
            self.library().replace("bibkey: paper2026", "bibkey: wrong2026"),
        ):
            with self.subTest(note=note):
                self.check_rejected("Library/Words/paper2026.md", note, "Library/Words/paper2026.md")
        self.write("Library/Words/paper2026.md", self.library())
        self.check_rejected("Library/Other/paper2026.md", self.library(), "ambiguous.*bibkey")
        (self.upstream / "Library/Words/paper2026.md").unlink()
        (self.upstream / "Library/Other/paper2026.md").unlink()
        self.commit("missing library note", "2026-07-08T09:00:00+00:00")
        with self.assertRaisesRegex(build_site.BuildError, "missing.*bibkey"):
            build_site.build_site(self.upstream, self.output)

    def check_library_rejected_without_replacement(
        self, old: str, new: str, diagnostic: str = "unsupported front matter scalar",
    ) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)

        def snapshot():
            return {
                path.relative_to(self.output).as_posix(): path.read_bytes()
                for path in self.output.rglob("*") if path.is_file()
            }

        original = snapshot()
        self.assertIn(b"0 recorded Markdown resolution markers", original["open-problems.md"])
        self.assertIn(old, self.library())
        self.assertNotEqual(old, new)
        self.write("Library/Words/paper2026.md", self.library().replace(old, new, 1))
        sha = self.commit("malformed Library front matter", "2026-07-07T09:00:00+00:00")
        diagnostic = "Library/Words/paper2026.md: " + diagnostic
        with self.assertRaisesRegex(build_site.BuildError, re.escape(diagnostic)) as caught:
            build_site.build_site(self.upstream, self.output)
        self.assertIsInstance(caught.exception.__cause__, build_site.OpenProblemError)
        self.assertEqual(snapshot(), original)

        completed = subprocess.run(
            [sys.executable, os.fspath(build_site.__file__), str(self.upstream), str(self.output)],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("build-site: " + diagnostic, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(snapshot(), original)

        # Pin a separate candidate to the bad input so verification reaches the
        # shared reader without changing the previously valid projection.
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            candidate = Path(temporary) / "source"
            shutil.copytree(self.output, candidate)
            provenance = json.loads(original["provenance.json"])
            provenance["upstream_sha"] = sha
            (candidate / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
            with self.assertRaisesRegex(verify_site.VerificationError, re.escape(diagnostic)) as caught:
                verify_site.verify(self.upstream, candidate, Path(temporary) / "book")
            self.assertIsInstance(caught.exception.__cause__, verify_site.OpenProblemError)
        self.assertEqual(snapshot(), original)

    def test_rejects_library_scalar_mapping_without_replacing_projection(self) -> None:
        self.check_library_rejected_without_replacement(
            "title: Example paper", "title: Example: broken",
        )

    def test_rejects_library_flow_list_without_replacing_projection(self) -> None:
        self.check_library_rejected_without_replacement(
            "  - D5/S1/Example", "  - [unterminated",
        )

    def test_rejects_library_empty_title_without_replacing_projection(self) -> None:
        self.check_library_rejected_without_replacement(
            "title: Example paper", "title:", "title must be a nonempty scalar",
        )

    def test_rejects_library_empty_strata_without_replacing_projection(self) -> None:
        self.check_library_rejected_without_replacement(
            "strata_touched:\n  - D5/S1/Example", "strata_touched:",
            "strata_touched must be a nonempty block list",
        )

    def test_rejects_library_scalar_strata_without_replacing_projection(self) -> None:
        self.check_library_rejected_without_replacement(
            "strata_touched:\n  - D5/S1/Example", "strata_touched: D5/S1/Example",
            "strata_touched must be a nonempty block list",
        )

    def test_rejects_library_nul_in_body_without_replacing_projection(self) -> None:
        self.check_library_rejected_without_replacement(
            "# Example paper", "# Example\x00paper", "forbidden YAML character U+0000",
        )

    def test_cli_fails_without_publishing_invalid_input(self) -> None:
        self.fixture(self.marker().replace("-v1 ", "-v99 "))
        completed = subprocess.run(
            [sys.executable, os.fspath(build_site.__file__), str(self.upstream), str(self.output)],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("marker", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_verifier_rejects_tampered_or_missing_generated_page(self) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)
        book = self.root / "book"
        book.mkdir()
        (book / "provenance.json").write_bytes((self.output / "provenance.json").read_bytes())
        (book / "Blueprint").mkdir()
        (book / "Blueprint/Example.html").write_text("<h1>Example</h1>", encoding="utf-8")
        (book / "open-problems.html").write_text("<h1>Problems</h1>", encoding="utf-8")
        page = self.output / "open-problems.md"
        page.write_text("# Invented resolution\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_site.VerificationError, "open.problems.*mismatch"):
            verify_site.verify(self.upstream, self.output, book)
        page.unlink()
        with self.assertRaisesRegex(verify_site.VerificationError, "open.problems.*missing"):
            verify_site.verify(self.upstream, self.output, book)

    def test_rejects_duplicate_and_out_of_order_dossier_slugs(self) -> None:
        from scripts.open_problems import OpenProblemError, parse_dossiers

        alpha = (b"Problems/alpha.md", self.dossier().encode())
        beta = (b"Problems/beta.md", self.dossier("beta").encode())
        for entries in ([alpha, alpha], [beta, alpha]):
            with self.subTest(entries=entries):
                with self.assertRaisesRegex(OpenProblemError, "duplicate|out.of.order"):
                    parse_dossiers(entries)

    def mock_book(self):
        book = self.root / "book"
        book.mkdir()
        (book / "provenance.json").write_bytes((self.output / "provenance.json").read_bytes())
        (book / "Blueprint").mkdir()
        (book / "Blueprint/Example.html").write_text("<h1>Example</h1>", encoding="utf-8")
        (book / "open-problems.html").write_text(
            '<h1>External open problems</h1><a href="Blueprint/Example.html">Source</a>',
            encoding="utf-8",
        )
        return book

    def test_verifier_requires_rendered_open_problems_page(self) -> None:
        self.fixture()
        build_site.build_site(self.upstream, self.output)
        book = self.mock_book()
        (book / "open-problems.html").unlink()
        with self.assertRaisesRegex(verify_site.VerificationError, "missing HTML.*open-problems"):
            verify_site.verify(self.upstream, self.output, book)

    def test_verifier_independently_regenerates_producer_output(self) -> None:
        self.fixture()
        with mock.patch.object(build_site, "build_open_problems", return_value="# Invented\n"):
            build_site.build_site(self.upstream, self.output)
        book = self.mock_book()
        with self.assertRaisesRegex(verify_site.VerificationError, "open.problems.*mismatch"):
            verify_site.verify(self.upstream, self.output, book)

    def test_lists_all_dossiers_without_claiming_world_status(self) -> None:
        sha = self.fixture()
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn(sha, page)
        self.assertIn("2 external problem dossiers", page)
        self.assertEqual(page.count("This repository has no recorded resolution binding"), 2)
        self.assertIn("does not establish whether a problem is still open in the world", page)
        self.assertIn("not validated typed claims", page)
        self.assertIn("narrative text can produce identical comments", page)
        self.assertIn(f"/blob/{sha}/Problems/alpha.md", page)
        self.assertIn(f"/blob/{sha}/Library/Words/paper2026.md", page)
        self.assertIn("https://doi.org/10.48550/arXiv.2601.12345", page)
        self.assertLess(page.index("## Problem alpha"), page.index("## Problem beta"))
        self.assertNotIn("](Problems/", page)
        self.assertNotIn("](Library/", page)
        self.assertFalse((self.output / "Problems").exists())
        self.assertFalse((self.output / "Library").exists())
        self.assertIn(
            "- [External open problems](open-problems.md)",
            (self.output / "SUMMARY.md").read_text(encoding="utf-8"),
        )
        changelog = (self.output / "changelog.md").read_text(encoding="utf-8")
        self.assertNotIn("Problems/", changelog)
        self.assertNotIn("Library/", changelog)

    def test_lists_proved_and_refuted_markers_as_unvalidated_records(self) -> None:
        self.fixture(self.marker() + "\n" + self.marker("beta", "refuted"))
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("Recorded Markdown marker: **proved**", page)
        self.assertIn("Recorded Markdown marker: **refuted**", page)
        self.assertEqual(page.count("](Blueprint/Example.md)"), 2)
        self.assertNotIn("This repository has no recorded resolution binding", page)
        self.assertIn("not validated typed claims", page)

    def test_uses_captured_sha_despite_new_head_and_dirty_inputs(self) -> None:
        sha = self.fixture(self.marker())

        def advance_head(upstream):
            self.write("Problems/alpha.md", "invalid new committed dossier\n")
            self.write("Blueprint/Example.md", self.marker().replace("-v1 ", "-v99 "))
            self.write("Library/Words/paper2026.md", "invalid new committed library\n")
            self.commit("advance head", "2026-07-08T09:00:00+00:00")
            self.write("Problems/beta.md", "invalid dirty dossier\n")
            return sha

        with mock.patch.object(build_site, "capture_upstream_sha", side_effect=advance_head):
            result = build_site.build_site(self.upstream, self.output)
        self.assertEqual(result["upstream_sha"], sha)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("Recorded Markdown marker: **proved**", page)
        self.assertIn("Problem beta", page)
        verified = verify_site.verify(self.upstream, self.output, self.mock_book())
        self.assertEqual(verified["upstream_sha"], sha)
        self.assertEqual(verified["mapped_pages"], 2)
        self.assertEqual(verified["open_problem_dossiers"], 2)

    def test_new_dossier_is_discovered_without_a_manual_page_list(self) -> None:
        self.fixture()
        self.write("Problems/gamma.md", self.dossier("gamma", "theorem"))
        self.commit("add external problem", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("3 external problem dossiers", page)
        self.assertIn("## Problem gamma", page)
        self.assertIn("Triage: `theorem`", page)

    def test_slug_prefixes_are_listed_in_slug_order(self) -> None:
        self.fixture()
        self.write("Problems/alpha-beta.md", self.dossier("alpha-beta"))
        self.commit("add related slug", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("3 external problem dossiers", page)
        self.assertLess(page.index("## Problem alpha\n"), page.index("## Problem alpha\\-beta\n"))
        self.assertLess(page.index("## Problem alpha\\-beta\n"), page.index("## Problem beta\n"))

    def test_no_dossiers_is_explicit_and_not_a_resolution_report(self) -> None:
        self.write("Blueprint/Example.md", "# Example\n")
        self.commit("pre-dossier repository", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("0 external problem dossiers", page)
        self.assertIn("does not establish whether a problem is still open in the world", page)


class FrontMatterTests(unittest.TestCase):
    def test_rejects_unsupported_scalar_syntax_in_fields_and_lists(self) -> None:
        from scripts.open_problems import OpenProblemError, front_matter

        for scalar in (
            "", " leading", "trailing ", "Example # comment", "Example: broken",
            *(prefix + "value" for prefix in "\"'[{&*!>|"),
        ):
            for line in (f"value: {scalar}", f"value:\n  - {scalar}"):
                with self.subTest(line=line):
                    with self.assertRaisesRegex(OpenProblemError, "front matter"):
                        front_matter(b"Library/Words/paper2026.md", f"---\n{line}\n---\n".encode())

        with self.assertRaisesRegex(OpenProblemError, "malformed front matter list"):
            front_matter(b"note.md", b"---\nvalue: scalar\n  - item\n---\n")

    def test_preserves_plain_scalar_text_in_fields_and_lists(self) -> None:
        from scripts.open_problems import front_matter

        for scalar in (
            "Example paper", "10.48550/arXiv.2601.12345", "D5/S1/Example", "2026", "C#",
            "-word", "?word", ":word", "a:b", "embedded [brackets], {braces}",
            "---", "...", "true", "null", "~", "caf\u00e9", "\u4e2d\u6587", "\U0001f600",
        ):
            with self.subTest(scalar=scalar):
                fields, body = front_matter(
                    b"note.md", f"---\nscalar: {scalar}\nlist:\n  - {scalar}\n---\nbody\n".encode(),
                )
                self.assertEqual(fields, {"scalar": scalar, "list": [scalar]})
                self.assertEqual(body, "body\n")


# Each payload gets a discoverable end-to-end test in both scalar branches.
SCALAR_REJECTION_CASES = {
    "flow_sequence": "[unterminated",
    "flow_mapping": "{unterminated",
    "mapping_separator": "Example: broken",
    "trailing_colon": "Example:",
    "comment_only": "#comment",
    "inline_comment": "Example # comment",
    "nested_sequence": "- nested",
    "explicit_key": "? key",
    "explicit_value": ": value",
    "bare_sequence_indicator": "-",
    "bare_key_indicator": "?",
    "bare_value_indicator": ":",
    "flow_sequence_end": "]value",
    "flow_mapping_end": "}value",
    "flow_comma": ",value",
    "directive": "%value",
    "reserved_at": "@value",
    "reserved_backtick": "`value",
    "single_quote": "'value'",
    "double_quote": '"value"',
    "anchor": "&value",
    "alias": "*value",
    "tag": "!value",
    "folded_block": ">value",
    "literal_block": "|value",
    "tab": "Example\ttext",
    "tab_mapping": "Example:\tbroken",
    "tab_comment": "Example\t#comment",
    "next_line": "Example\u0085text",
    "line_separator": "Example\u2028text",
    "paragraph_separator": "Example\u2029text",
    "embedded_bom": "Example\ufefftext",
    "leading_space": " leading",
    "trailing_space": "trailing ",
}
LIBRARY_STRUCTURE_REJECTIONS = {
    "empty_item": ("  - D5/S1/Example", "  - ", "unsupported front matter scalar"),
    "bare_item": ("  - D5/S1/Example", "  -", "unsupported front matter syntax"),
    "indented_item": ("  - D5/S1/Example", "   - Example", "unsupported front matter syntax"),
    "tab_indentation": ("  - D5/S1/Example", "\t- Example", "unsupported front matter syntax"),
    "tab_after_dash": ("  - D5/S1/Example", "  -\tExample", "unsupported front matter syntax"),
    "list_continuation": ("  - D5/S1/Example", "  - Example\n    continuation", "unsupported front matter syntax"),
    "scalar_continuation": ("title: Example paper", "title: Example\n  continuation", "unsupported front matter syntax"),
    "list_after_scalar": ("title: Example paper", "title: Example\n  - extra", "malformed front matter list"),
    "duplicate_key": ("title: Example paper", "title: Example\ntitle: Duplicate", "duplicate front matter key title"),
    "space_only_title": ("title: Example paper", "title: ", "unsupported front matter syntax"),
    "list_title": ("title: Example paper", "title:\n  - Example", "title must be a nonempty scalar"),
}
FORBIDDEN_YAML_CODEPOINTS = (
    *range(0x09), 0x0B, 0x0C, *range(0x0E, 0x20),
    *range(0x7F, 0x85), *range(0x86, 0xA0), 0xFFFE, 0xFFFF,
)


def library_rejection_test(old: str, new: str, diagnostic: str):
    def test(self):
        self.check_library_rejected_without_replacement(old, new, diagnostic)
    return test


for case_name, scalar in (
    *SCALAR_REJECTION_CASES.items(),
    *((f"character_u{codepoint:04x}", f"Example{chr(codepoint)}text")
      for codepoint in FORBIDDEN_YAML_CODEPOINTS),
):
    diagnostic = "unsupported front matter scalar"
    if case_name.startswith("character_u"):
        diagnostic = "forbidden YAML character U+" + case_name.removeprefix("character_u").upper()
    for context, old, new in (
        ("field", "title: Example paper", f"title: {scalar}"),
        ("list", "  - D5/S1/Example", f"  - {scalar}"),
    ):
        name = f"test_rejects_library_{case_name}_{context}_without_replacing_projection"
        setattr(OpenProblemTests, name, library_rejection_test(old, new, diagnostic))

for key, value in (
    ("authors", "A. Author"), ("year", "2026"), ("claim", "An external question."),
    ("license", "citation-only"), ("triage", "anchor"),
):
    for shape, replacement in (("empty", ""), ("list", "\n  - Example")):
        name = f"test_rejects_library_{shape}_{key}_without_replacing_projection"
        setattr(OpenProblemTests, name, library_rejection_test(
            f"{key}: {value}", f"{key}:{replacement}", f"{key} must be a nonempty scalar",
        ))

for case_name, arguments in LIBRARY_STRUCTURE_REJECTIONS.items():
    name = f"test_rejects_library_{case_name}_without_replacing_projection"
    setattr(OpenProblemTests, name, library_rejection_test(*arguments))


if __name__ == "__main__":
    unittest.main()
