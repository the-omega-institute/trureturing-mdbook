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
from urllib.parse import unquote_to_bytes, urlsplit

import test_build_site as fixtures

build_site = fixtures.build_site
verify_site = fixtures.verify_site


class OpenProblemTests(unittest.TestCase):
    setUp = fixtures.BuildSiteTests.setUp
    tearDown = fixtures.BuildSiteTests.tearDown
    git = fixtures.BuildSiteTests.git
    write = fixtures.BuildSiteTests.write
    commit = fixtures.BuildSiteTests.commit

    def dossier(
        self, slug: str = "alpha", triage: str = "window",
        doi: str = "10.48550/arXiv.2601.12345",
    ) -> str:
        return (
            f"---\nslug: {slug}\nbibkey: paper2026\ndoi: {doi}\n"
            f"triage: {triage}\nmotivation_gids:\n  - D5/S1/Example\n---\n\n"
            f"# Problem {slug}\n\nLater external resolutions have not been checked.\n"
        )

    def library(self, doi: str = "10.48550/arXiv.2601.12345") -> str:
        return (
            "---\nbibkey: paper2026\nauthors: A. Author\nyear: 2026\n"
            f"title: Example paper\ndoi: {doi}\n"
            "claim: An external question.\nstrata_touched:\n  - D5/S1/Example\n"
            "license: citation-only\ntriage: anchor\n---\n\n# Example paper\n"
        )

    def marker(
        self, slug: str = "alpha", kind: str = "proved",
        gid: object = "D5/S1/Example.theorem17",
    ) -> str:
        payload = json.dumps({
            "problem_slug": slug, "declaration_gid": gid, "resolution_kind": kind,
        })
        return f"<!-- scribe-open-problem-resolution-v1 {payload} -->\n"

    def fixture(
        self, markers: str = "", doi: str = "10.48550/arXiv.2601.12345",
        frozen: bool = True, frozen_date: str = "2026-07-03",
    ) -> str:
        if markers and frozen:
            self.write("Golden/Frozen/state/D5/S1/Example.lean.json", '{"statement_id":"fixture"}\n')
            self.commit("first freeze", frozen_date + "T09:00:00+00:00")
        self.write("Blueprint/D5/S1/Example.md", "# Example\n\n" + markers)
        self.write("Problems/alpha.md", self.dossier(doi=doi))
        self.write("Problems/beta.md", self.dossier("beta", "wall", doi))
        self.write("Library/Words/paper2026.md", self.library(doi))
        return self.commit("open problem fixture", "2026-07-06T09:00:00+00:00")

    def mixed_fixture(self) -> str:
        self.write("Golden/Frozen/state/D5/S1/Other.lean.json", '{"statement_id":"other"}\n')
        self.commit("freeze other module", "2026-07-02T09:00:00+00:00")
        self.fixture(self.marker())
        self.write("Blueprint/D5/S1/Other.md", "# Other\n\n" + self.marker(
            "beta", "refuted", "D5/S1/Other.counterexample",
        ))
        self.write("Problems/gamma.md", self.dossier("gamma", "theorem"))
        return self.commit("proved, refuted, and unrecorded problems", "2026-07-07T09:00:00+00:00")

    def check_rejected(self, path: str, value: str, diagnostic: str) -> None:
        self.write(path, value)
        self.commit("invalid open problem input", "2026-07-07T09:00:00+00:00")
        with self.assertRaisesRegex(build_site.BuildError, diagnostic):
            build_site.build_site(self.upstream, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_unknown_marker_version(self) -> None:
        self.fixture()
        self.check_rejected(
            "Blueprint/D5/S1/Example.md", self.marker().replace("-v1 ", "-v2 "),
            "marker.*version",
        )

    def test_accepts_exactly_three_marker_keys(self) -> None:
        from scripts.open_problems import parse_markers

        resolution = parse_markers(
            [(b"Blueprint/D5/S1/Example.md", self.marker().encode())], {"alpha"},
        )["alpha"]
        self.assertEqual(resolution.declaration_gid, "D5/S1/Example.theorem17")
        self.assertEqual(resolution.path, b"Blueprint/D5/S1/Example.md")

    def test_rejects_two_key_marker(self) -> None:
        from scripts.open_problems import OpenProblemError, parse_markers

        marker = '<!-- scribe-open-problem-resolution-v1 {"problem_slug":"alpha","resolution_kind":"proved"} -->'
        with self.assertRaisesRegex(OpenProblemError, "missing or unknown keys"):
            parse_markers([(b"Blueprint/D5/S1/Example.md", marker.encode())], {"alpha"})

    def test_rejects_four_key_marker(self) -> None:
        self.fixture()
        self.check_rejected(
            "Blueprint/D5/S1/Example.md", self.marker().replace('}', ', "extra": true}'),
            "missing or unknown keys",
        )

    def test_rejects_unknown_marker_key(self) -> None:
        self.fixture()
        self.check_rejected(
            "Blueprint/D5/S1/Example.md", self.marker().replace('"declaration_gid"', '"gid"'),
            "missing or unknown keys",
        )

    def test_rejects_empty_declaration_gid(self) -> None:
        self.fixture()
        self.check_rejected("Blueprint/D5/S1/Example.md", self.marker(gid=""), "invalid declaration GID")

    def test_rejects_noncanonical_declaration_gid(self) -> None:
        self.fixture()
        for gid in (None, 42, [], "Example.theorem17", "D5/S1/Example", "D5/S1/../Example.t",
                    "D5/S1/Example.t ", "D5/S1/Example..t", "D5/S1/Example.t#anchor"):
            with self.subTest(gid=gid):
                self.check_rejected(
                    "Blueprint/D5/S1/Example.md", self.marker(gid=gid), "invalid declaration GID",
                )

    def test_resolved_entry_renders_theorem_relative_blueprint_link_and_frozen_date(self) -> None:
        self.fixture(self.marker())
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        solved = page.split("## Solved (1)\n", 1)[1].split("\n## ", 1)[0]
        entry = solved.split("### Problem alpha\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn(
            "**Proved.** Lean theorem [`theorem17`](Blueprint/D5/S1/Example.md), "
            "frozen in this repository 2026-07-03.",
            entry,
        )
        self.assertTrue(entry.startswith("\n**Proved.**"))
        self.assertIn("2026-07-03.\n\n[Problem details]", entry)
        self.assertNotIn("Recorded Markdown marker:", entry)

    def test_readme_describes_declaration_name_label_and_unchanged_destination(self) -> None:
        readme = " ".join((fixtures.ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn(
            "The theorem link displays only the declaration name (for example, `conjecture17`); "
            "its destination is unchanged, still using the marker's containing Blueprint path",
            readme,
        )

    def check_summary(self, solved: int, total: int) -> None:
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertEqual(
            page.splitlines()[:4],
            ["# Open problems from research papers", "", f"**{solved} of {total} solved in this repository.**", ""],
        )
        self.assertEqual(
            re.findall(r"^## (.+)$", page, re.MULTILINE),
            [f"Solved ({solved})", f"Not solved here ({total - solved})", "How this list is made"],
        )

    def test_summary_counts_mixed_solved_and_open(self) -> None:
        self.fixture(self.marker())
        self.check_summary(1, 2)

    def test_summary_counts_all_solved_including_refuted(self) -> None:
        self.fixture(self.marker() + self.marker("beta", "refuted"))
        self.check_summary(2, 2)

    def test_summary_counts_none_solved(self) -> None:
        self.fixture()
        self.check_summary(0, 2)

    def test_summary_counts_no_dossiers(self) -> None:
        self.write("Blueprint/D5/S1/Example.md", "# Example\n")
        self.commit("pre-dossier repository", "2026-07-08T09:00:00+00:00")
        self.check_summary(0, 0)

    def test_solved_definition_precedes_entries(self) -> None:
        self.fixture(self.marker())
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        definition = (
            'What "solved" means here: a frozen Lean theorem in this repository is recorded '
            "against the problem. Nobody has machine-checked that the theorem says the same "
            "thing as the paper, and a problem with no record here may still have been solved "
            "by someone else."
        )
        intro, entries = page.split("\n## ", 1)
        self.assertEqual(" ".join(intro.split("\n\n")[2].splitlines()), definition)
        self.assertNotIn(definition, " ".join(entries.splitlines()))

    def test_unsolved_entry_has_only_links_and_page_level_absence_of_record_limit(self) -> None:
        self.fixture(self.marker())
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        opened = page.split("## Not solved here (1)\n", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(
            opened.strip(),
            "### Problem beta\n\n"
            "[Problem details](Problems/beta.md) \u00b7 [Reading note](Library/Words/paper2026.md) \u00b7 "
            "[Source paper](https://doi.org/10.48550/arXiv.2601.12345)",
        )
        limitation = "a problem with no record here may still have been solved by someone else."
        self.assertEqual(" ".join(page.splitlines()).count(limitation), 1)
        self.assertIn(limitation, " ".join(page.split("\n## ", 1)[0].splitlines()))

    def test_refuted_entry_renders_refuted_date_and_linked_theorem(self) -> None:
        self.fixture(self.marker(kind="refuted"), frozen_date="2026-07-04")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        solved = page.split("## Solved (1)\n", 1)[1].split("\n## ", 1)[0]
        entry = solved.split("### Problem alpha\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn(
            "**Refuted.** Lean theorem [`theorem17`](Blueprint/D5/S1/Example.md), "
            "frozen in this repository 2026-07-04.",
            entry,
        )
        self.assertNotIn("**Proved", entry)
        self.assertNotIn("**Solved", entry)
        self.assertTrue(entry.startswith("\n**Refuted.**"))
        self.assertIn("2026-07-04.\n\n[Problem details]", entry)
        self.assertNotIn("Recorded Markdown marker:", entry)

    def test_each_module_uses_its_own_frozen_date(self) -> None:
        self.mixed_fixture()
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        solved = page.split("## Solved (2)\n", 1)[1].split("\n## ", 1)[0]
        for slug, kind, declaration, path, expected_date in (
            ("alpha", "Proved", "theorem17", "Example", "2026-07-03"),
            ("beta", "Refuted", "counterexample", "Other", "2026-07-02"),
        ):
            with self.subTest(slug=slug):
                entry = solved.split(f"### Problem {slug}\n", 1)[1].split("\n### ", 1)[0]
                self.assertIn(
                    f"**{kind}.** Lean theorem [`{declaration}`](Blueprint/D5/S1/{path}.md), "
                    f"frozen in this repository {expected_date}.", entry,
                )

    def test_sections_split_entries_and_preserve_slug_order_within_each(self) -> None:
        self.fixture(self.marker("beta") + self.marker("beta-delta"))
        self.write("Problems/alpha-beta.md", self.dossier("alpha-beta"))
        self.write("Problems/beta-delta.md", self.dossier("beta-delta"))
        self.commit("add slug prefixes in both sections", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r"^## (.+)$", page, re.MULTILINE),
            ["Solved (2)", "Not solved here (2)", "How this list is made"],
        )
        solved = page.split("## Solved (2)\n", 1)[1].split("\n## ", 1)[0]
        opened = page.split("## Not solved here (2)\n", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(re.findall(r"^### (.+)$", solved, re.MULTILINE), ["Problem beta", "Problem beta\\-delta"])
        self.assertEqual(re.findall(r"^### (.+)$", opened, re.MULTILINE), ["Problem alpha", "Problem alpha\\-beta"])
        self.assertEqual(len(re.findall(r"^### ", page, re.MULTILINE)), 4)

    def test_trailing_method_explains_records_and_displayed_freeze_date(self) -> None:
        sha = self.fixture(self.marker())
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        heading = "## How this list is made\n"
        self.assertEqual(page.count(heading), 1)
        content, caveats = page.split(heading)
        self.assertNotIn("\n## ", caveats)
        self.assertNotIn("\n### ", caveats)
        self.assertTrue(caveats.startswith(
            f"\nSource revision: [`{sha[:8]}`](https://github.com/the-omega-institute/trureturing/commit/{sha}).\n\n"
        ))
        self.assertNotIn(sha, content)
        for sentence in (
            "The list is generated from problem files, reading notes, and resolution records in theorem pages at this source revision.",
            "The records are read as text, so ordinary prose can produce one; this page does not check that a record came from the repository's own verified claim.",
            "This page does not run the repository's checks or verify the named theorems or their Lean proofs.",
            'The "frozen in this repository" date is the date of the first commit that added the theorem\'s module to the frozen record.',
            "It is not the date the problem was solved in the world or the resolution was recorded.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, page)
                self.assertIn(sentence, caveats)
                self.assertNotIn(sentence, content)
        for removed in (
            "below", "above", "binding", "matching Markdown comments", "validated typed claims",
            "repository validity", "declaration identity", "triage", "research category", "Upstream snapshot",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed.lower(), page.lower())

    def test_whole_page_external_urls_are_only_source_revision_and_papers(self) -> None:
        sha = self.mixed_fixture()
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        targets = re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", page)
        external = {target for target in targets if urlsplit(target).scheme or urlsplit(target).netloc}
        external.update(re.findall(r"(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^\s<>()]+", page))
        self.assertEqual(
            external,
            {f"https://github.com/the-omega-institute/trureturing/commit/{sha}",
             "https://doi.org/10.48550/arXiv.2601.12345"},
        )
        self.assertEqual(targets.count(f"https://github.com/the-omega-institute/trureturing/commit/{sha}"), 1)

    def test_all_entry_navigation_links_resolve_to_written_projection_blobs(self) -> None:
        self.mixed_fixture()
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        for slug, theorem in (("alpha", ("theorem17", "Example")),
                              ("beta", ("counterexample", "Other")), ("gamma", None)):
            entry = page.split(f"### Problem {slug}\n", 1)[1].split("\n##", 1)[0]
            expected_links = [
                ("Problem details", f"Problems/{slug}.md"),
                ("Reading note", "Library/Words/paper2026.md"),
                ("Source paper", "https://doi.org/10.48550/arXiv.2601.12345"),
            ]
            if theorem is not None:
                expected_links.insert(0, (f"`{theorem[0]}`", f"Blueprint/D5/S1/{theorem[1]}.md"))
            with self.subTest(slug=slug):
                links = re.findall(r"\[([^\]\n]*)\]\(([^)\n]+)\)", entry)
                self.assertEqual(links, expected_links)
            for label, target in links:
                if label == "Source paper":
                    continue
                with self.subTest(slug=slug, label=label):
                    parsed = urlsplit(target)
                    self.assertEqual((parsed.scheme, parsed.netloc, parsed.query, parsed.fragment), ("", "", "", ""))
                    projected = self.output / os.fsdecode(unquote_to_bytes(target))
                    self.assertTrue(projected.is_file(), target)
                    self.assertEqual(projected.read_bytes(), (self.upstream / target).read_bytes())

    def test_missing_frozen_state_history_is_an_error(self) -> None:
        self.fixture(self.marker(), frozen=False)
        with self.assertRaisesRegex(build_site.BuildError, "no adding commit.*Golden/Frozen/state/D5/S1/Example.lean.json"):
            build_site.build_site(self.upstream, self.output)
        self.assertFalse(self.output.exists())

    def test_frozen_state_git_error_is_an_error(self) -> None:
        from scripts import open_problems

        self.fixture(self.marker())
        original = open_problems.subprocess.run

        def fail_history(command, **kwargs):
            if "log" in command:
                raise subprocess.CalledProcessError(128, command, stderr=b"history unavailable")
            return original(command, **kwargs)

        with mock.patch.object(open_problems.subprocess, "run", side_effect=fail_history):
            with self.assertRaisesRegex(build_site.BuildError, "git log failed: history unavailable"):
                build_site.build_site(self.upstream, self.output)
        self.assertFalse(self.output.exists())

    def test_frozen_date_uses_first_addition_not_readdition_or_binding(self) -> None:
        self.fixture(self.marker())
        state = self.upstream / "Golden/Frozen/state/D5/S1/Example.lean.json"
        state.unlink()
        self.commit("remove freeze", "2026-07-07T09:00:00+00:00")
        self.write("Golden/Frozen/state/D5/S1/Example.lean.json", '{"statement_id":"second"}\n')
        self.commit("refreeze", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("frozen in this repository 2026-07-03.", page)

    def test_frozen_history_is_limited_to_captured_snapshot(self) -> None:
        sha = self.fixture(self.marker(), frozen=False)
        self.write("Golden/Frozen/state/D5/S1/Example.lean.json", '{"statement_id":"later"}\n')
        self.commit("freeze after snapshot", "2026-07-08T09:00:00+00:00")
        with mock.patch.object(build_site, "capture_upstream_sha", return_value=sha):
            with self.assertRaisesRegex(build_site.BuildError, "no adding commit"):
                build_site.build_site(self.upstream, self.output)

    def test_resolution_link_and_frozen_path_use_marker_container(self) -> None:
        self.fixture(self.marker(gid="D5/S1/DifferentName.Namespace.theorem17"))
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("[`Namespace.theorem17`](Blueprint/D5/S1/Example.md)", page)
        self.assertNotIn("DifferentName", page)
        self.assertIn("frozen in this repository 2026-07-03.", page)

    def test_library_marker_text_is_not_a_blueprint_resolution(self) -> None:
        self.fixture()
        self.write("Library/Words/paper2026.md", self.library() + self.marker())
        self.commit("literature quoting a marker", "2026-07-07T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("**0 of 2 solved in this repository.**", page)

    def test_rejects_non_object_marker_payloads(self) -> None:
        self.fixture()
        for payload in ('[]', 'null', 'true', '42', '"alpha"'):
            with self.subTest(payload=payload):
                self.check_rejected(
                    "Blueprint/D5/S1/Example.md",
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
                    "Blueprint/D5/S1/Example.md",
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
                self.check_rejected("Blueprint/D5/S1/Example.md", marker, "marker")

    def test_rejects_marker_for_unknown_problem(self) -> None:
        self.fixture()
        self.check_rejected("Blueprint/D5/S1/Example.md", self.marker("missing"), "unknown.*slug")

    def test_rejects_duplicate_resolution_slugs_across_pages(self) -> None:
        self.fixture(self.marker())
        self.check_rejected("Blueprint/Other.md", self.marker(kind="refuted"), "duplicate.*slug")

    def test_rejects_out_of_order_marker_slugs(self) -> None:
        self.fixture()
        self.check_rejected(
            "Blueprint/D5/S1/Example.md", self.marker("beta") + self.marker("alpha"),
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
            original.replace("doi: 10.48550/arXiv.2601.12345", "doi: bad-id"),
            original.replace("slug: alpha", "slug: beta"),
            original.replace("  - D5/S1/Example\n", ""),
            original.replace("  - D5/S1/Example", "  - ../escape"),
            original.replace("  - D5/S1/Example", "  - D5/S1/Example\n  - D5/S1/Example"),
            original.replace("\n", "\r\n"), "\ufeff" + original,
        ):
            with self.subTest(dossier=dossier):
                self.check_rejected("Problems/alpha.md", dossier, "Problems/alpha.md")

    def test_rejects_retired_arxiv_id_key(self) -> None:
        self.fixture()
        self.check_rejected(
            "Problems/alpha.md",
            self.dossier().replace("doi: 10.48550/arXiv.2601.12345", "arxiv_id: 2601.12345"),
            "unsupported problem schema; expected slug, bibkey, doi, triage, motivation_gids",
        )

    def test_rejects_arxiv_id_alongside_doi(self) -> None:
        self.fixture()
        self.check_rejected(
            "Problems/alpha.md",
            self.dossier().replace("triage: window", "arxiv_id: 2601.12345\ntriage: window"),
            "unsupported problem schema; expected slug, bibkey, doi, triage, motivation_gids",
        )

    def check_dossier_doi_rejected(self, doi: str) -> None:
        from scripts.open_problems import OpenProblemError, parse_dossiers

        with self.assertRaisesRegex(OpenProblemError, r"^Problems/alpha\.md: invalid DOI$"):
            parse_dossiers([(b"Problems/alpha.md", self.dossier(doi=doi).encode())])

    def test_rejects_dossier_doi_bare_arxiv_id(self) -> None:
        self.check_dossier_doi_rejected("2305.08349")

    def test_rejects_dossier_doi_arxiv_prefix(self) -> None:
        self.check_dossier_doi_rejected("arXiv:2305.08349")

    def test_rejects_dossier_doi_url_prefix(self) -> None:
        self.check_dossier_doi_rejected("https://doi.org/10.48550/arXiv.2305.08349")

    def test_rejects_dossier_doi_short_registrant(self) -> None:
        self.check_dossier_doi_rejected("10.123/x")

    def test_rejects_dossier_doi_long_registrant(self) -> None:
        self.check_dossier_doi_rejected("10.1234567890/x")

    def test_rejects_dossier_doi_empty_suffix(self) -> None:
        self.check_dossier_doi_rejected("10.48550/")

    def test_rejects_dossier_doi_inner_space(self) -> None:
        self.check_dossier_doi_rejected("10.48550/arXiv.2305 08349")

    def test_rejects_doi_case_mismatch(self) -> None:
        self.fixture()
        self.check_rejected(
            "Problems/alpha.md", self.dossier(doi="10.48550/arxiv.2601.12345"),
            re.escape(
                "Library/Words/paper2026.md: DOI '10.48550/arXiv.2601.12345' disagrees with "
                "Problems/alpha.md: DOI '10.48550/arxiv.2601.12345'"
            ),
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
        self.assertIn(b"**0 of 2 solved in this repository.**", original["open-problems.md"])
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
            (candidate / "Library/Words/paper2026.md").write_bytes(
                (self.upstream / "Library/Words/paper2026.md").read_bytes()
            )
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
        book = self.mock_book()
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
        for path in verify_site.projected_source_paths(self.output):
            destination = book / os.fsdecode(path[:-3] + b".html")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("<h1>Fixture</h1>", encoding="utf-8")
        (book / "open-problems.html").write_text(
            '<h1>External open problems</h1><a href="Blueprint/D5/S1/Example.html">Source</a>',
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
        self.assertIn("**0 of 2 solved in this repository.**", page)
        self.assertIn(
            "a problem with no record here may still have been solved by someone else.",
            " ".join(page.splitlines()),
        )
        self.assertIn("](Problems/alpha.md)", page)
        self.assertIn("](Library/Words/paper2026.md)", page)
        self.assertIn("https://doi.org/10.48550/arXiv.2601.12345", page)
        self.assertLess(page.index("### Problem alpha"), page.index("### Problem beta"))
        self.assertTrue((self.output / "Problems").is_dir())
        self.assertTrue((self.output / "Library").is_dir())
        self.assertIn(
            "- [External open problems](open-problems.md)",
            (self.output / "SUMMARY.md").read_text(encoding="utf-8"),
        )
        changelog = (self.output / "changelog.md").read_text(encoding="utf-8")
        self.assertIn("](Problems/alpha.md)", changelog)
        self.assertIn("](Library/Words/paper2026.md)", changelog)

    def test_accepts_non_arxiv_doi_end_to_end(self) -> None:
        self.fixture(doi="10.46298/dmtcs.17199")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertEqual(page.count("[Source paper](https://doi.org/10.46298/dmtcs.17199)"), 2)

    def test_renders_readable_resource_labels_without_research_categories(self) -> None:
        self.mixed_fixture()
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        for slug in ("alpha", "beta", "gamma"):
            with self.subTest(slug=slug):
                self.assertIn(
                    f"[Problem details](Problems/{slug}.md) \u00b7 [Reading note](Library/Words/paper2026.md) \u00b7 "
                    "[Source paper](https://doi.org/10.48550/arXiv.2601.12345)",
                    page,
                )
        for removed in ("triage", "research category", "`window`", "`wall`", "`theorem`"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, page.lower())

    def test_lists_proved_and_refuted_records_with_solved_scope(self) -> None:
        self.fixture(self.marker() + "\n" + self.marker("beta", "refuted"))
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("**Proved.** Lean theorem", page)
        self.assertIn("**Refuted.** Lean theorem", page)
        self.assertEqual(page.count("](Blueprint/D5/S1/Example.md)"), 2)
        self.assertIn("**2 of 2 solved in this repository.**", page)
        self.assertIn("Nobody has machine-checked that the theorem says the same thing as the paper", " ".join(page.splitlines()))
        for slug in ("alpha", "beta"):
            with self.subTest(slug=slug):
                entry = page.split(f"### Problem {slug}\n", 1)[1].split("\n##", 1)[0]
                self.assertIn("frozen in this repository 2026-07-03.", entry)

    def test_uses_captured_sha_despite_new_head_and_dirty_inputs(self) -> None:
        sha = self.fixture(self.marker())

        def advance_head(upstream):
            self.write("Problems/alpha.md", "invalid new committed dossier\n")
            self.write("Blueprint/D5/S1/Example.md", self.marker().replace("-v1 ", "-v99 "))
            self.write("Library/Words/paper2026.md", "invalid new committed library\n")
            self.commit("advance head", "2026-07-08T09:00:00+00:00")
            self.write("Problems/beta.md", "invalid dirty dossier\n")
            return sha

        with mock.patch.object(build_site, "capture_upstream_sha", side_effect=advance_head):
            result = build_site.build_site(self.upstream, self.output)
        self.assertEqual(result["upstream_sha"], sha)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("**Proved.** Lean theorem", page)
        self.assertIn("Problem beta", page)
        verified = verify_site.verify(self.upstream, self.output, self.mock_book())
        self.assertEqual(verified["upstream_sha"], sha)
        self.assertEqual(verified["mapped_pages"], 5)
        self.assertEqual(verified["open_problem_dossiers"], 2)

    def test_new_dossier_is_discovered_without_a_manual_page_list(self) -> None:
        self.fixture()
        self.write("Problems/gamma.md", self.dossier("gamma", "theorem"))
        self.commit("add external problem", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("**0 of 3 solved in this repository.**", page)
        opened = page.split("## Not solved here (3)\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("### Problem gamma\n\n[Problem details](Problems/gamma.md)", opened)
        self.assertNotIn("`theorem`", page)

    def test_slug_prefixes_are_listed_in_slug_order(self) -> None:
        self.fixture()
        self.write("Problems/alpha-beta.md", self.dossier("alpha-beta"))
        self.commit("add related slug", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("**0 of 3 solved in this repository.**", page)
        self.assertLess(page.index("### Problem alpha\n"), page.index("### Problem alpha\\-beta\n"))
        self.assertLess(page.index("### Problem alpha\\-beta\n"), page.index("### Problem beta\n"))

    def test_no_dossiers_is_explicit_and_not_a_resolution_report(self) -> None:
        self.write("Blueprint/D5/S1/Example.md", "# Example\n")
        self.commit("pre-dossier repository", "2026-07-08T09:00:00+00:00")
        build_site.build_site(self.upstream, self.output)
        page = (self.output / "open-problems.md").read_text(encoding="utf-8")
        self.assertIn("**0 of 0 solved in this repository.**", page)
        self.assertIn(
            "a problem with no record here may still have been solved by someone else.",
            " ".join(page.splitlines()),
        )


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
