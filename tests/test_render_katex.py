from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

import test_build_site as fixtures
from scripts import math_scan, render_katex

ROOT = fixtures.ROOT
NODE_MODULES = ROOT / "node_modules" / "katex" / "package.json"


def spans(text: str) -> list[tuple[str, bool]]:
    content = text.encode("utf-8")
    return [(content[start:end].decode("utf-8"), display)
            for start, end, display in math_scan.math_spans(content)]


class MathScanTests(unittest.TestCase):
    def test_pairs_inline_and_display_formulas_outside_code(self) -> None:
        text = (
            "Let $x$ and $$y\nz$$ but `not $a$` and ``no $$b$$ `` here.\n"
            "```\n$fenced$ and $$fenced$$\n```\n"
            "~~~text\n$also fenced$\n~~~\n"
            "    ```\n    $indented four is text$\n"
            "Escaped \\$5 and \\\\$real$ and $$a\\$b$$ done.\n"
        )
        self.assertEqual(spans(text), [
            ("$x$", False), ("$$y\nz$$", True), ("$indented four is text$", False),
            ("$real$", False), ("$$a\\$b$$", True),
        ])
        self.assertEqual(spans("$$\\$$ never closes\n"), [])

    def test_front_matter_is_metadata_not_math(self) -> None:
        note = "---\nclaim: \"$a(n)$ for n>1\"\nurl: x\n---\n\n# Title $x$\n"
        self.assertEqual(spans(note), [("$x$", False)])
        # Without a closing line there is no front matter, only prose.
        self.assertEqual(spans("---\nclaim: $a$\n"), [("$a$", False)])
        self.assertEqual(spans("Text\n---\n$b$\n---\n"), [("$b$", False)])

    def test_inline_formula_must_close_on_its_line_and_display_may_span_lines(self) -> None:
        self.assertEqual(spans("open $x\nnot closed$ here\n"), [])
        self.assertEqual(spans("$$a\n\nb$$\n"), [("$$a\n\nb$$", True)])
        self.assertEqual(spans("$$unterminated\n"), [])
        self.assertEqual(spans("$a$$b$\n"), [("$a$", False), ("$b$", False)])

    def test_span_offsets_index_the_original_bytes(self) -> None:
        content = "é `$c$` $x$ ✓ $$y$$\n".encode("utf-8")
        found = math_scan.math_spans(content)
        self.assertEqual([content[s:e] for s, e, _ in found], [b"$x$", b"$$y$$"])
        self.assertEqual([d for _, _, d in found], [False, True])

    def test_github_inline_math_adjacent_to_chinese_and_code(self) -> None:
        text = (
            "$`X_i=\\theta_i+\\epsilon_i`$，其中 $`\\epsilon_i\\sim N(0,1)`$。\n"
            "Ordinary $x$ and $$y$$; ``code $`z`$`` and \\$`escaped`$ and $next$ here.\n"
            "```\n$`fenced`$\n```\n"
        )
        expected = [
            ("$`X_i=\\theta_i+\\epsilon_i`$", False),
            ("$`\\epsilon_i\\sim N(0,1)`$", False),
            ("$x$", False), ("$$y$$", True), ("$next$", False),
        ]
        self.assertEqual(spans(text), expected)
        self.assertEqual(fixtures.verify_site.markdown_math_token_count(text.encode("utf-8")),
                         len(expected))
        self.assertEqual(spans("$a$`code $b` and $c$"),
                         [("$a$", False), ("$c$", False)])
        self.assertEqual(spans("$a$$`b`$"),
                         [("$a$", False), ("$`b`$", False)])

    def test_verifier_counts_the_same_spans(self) -> None:
        text = "$a$ `$b$` $$c$$\n```\n$d$\n```\n\\$e\n"
        self.assertEqual(fixtures.verify_site.markdown_math_token_count(text.encode()),
                         len(spans(text)))
        self.assertEqual(len(spans(text)), 2)


class RenderKatexTests(unittest.TestCase):
    def test_pinned_katex_package_is_installed(self) -> None:
        # `npm ci` is a build input like mdBook itself; a missing package must fail, not skip.
        self.assertTrue(NODE_MODULES.is_file(), "run `npm ci` in the repository root")

    def render(self, chapters: dict[str, str]) -> dict[str, str]:
        book = {"items": [
            {"Chapter": {"name": name, "content": content, "sub_items": [
                {"Chapter": {"name": name + "/sub", "content": content, "sub_items": []}},
            ]}} for name, content in chapters.items()
        ] + ["Separator", {"PartTitle": "Part"}]}
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_katex.py")],
            input=json.dumps([{"root": str(ROOT)}, book]), capture_output=True, text=True,
            cwd=ROOT, check=True,
        )
        rendered = json.loads(result.stdout)
        out = {}
        for item in rendered["items"]:
            if isinstance(item, dict) and "Chapter" in item:
                out[item["Chapter"]["name"]] = item["Chapter"]["content"]
                for sub in item["Chapter"]["sub_items"]:
                    out[sub["Chapter"]["name"]] = sub["Chapter"]["content"]
        return out

    def test_supports_html_only(self) -> None:
        for renderer, code in (("html", 0), ("epub", 1)):
            with self.subTest(renderer=renderer):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "render_katex.py"), "supports", renderer],
                    capture_output=True, cwd=ROOT,
                )
                self.assertEqual(result.returncode, code)

    def sources(self, content: str) -> list[tuple[str, bool, bool]]:
        """(decoded TeX, display, lenient) of each source element, in order."""
        import html, re
        return [
            (html.unescape(text), bool(display), bool(lenient))
            for display, lenient, text in re.findall(
                r'<span class="katex-source"( data-display="true")?( data-lenient="true")?>([^<]*)</span>',
                content,
            )
        ]

    def test_leaves_each_formula_as_source_and_leaves_text_and_code_alone(self) -> None:
        content = (
            "Inline $x^2$ then display\n\n$$\\sum_{i=1}^n i$$\n\n"
            "`code $k$` and\n```\n$fenced$\n```\nand \\$5 stays.\n"
        )
        out = self.render({"page": content})
        for name in ("page", "page/sub"):
            with self.subTest(name=name):
                rendered = out[name]
                self.assertTrue(rendered.startswith(render_katex.asset_header(None)))
                self.assertEqual(self.sources(rendered),
                                 [("x^2", False, False), ("\\sum_{i=1}^n i", True, False)])
                self.assertNotIn("$$", rendered)
                self.assertIn("`code $k$`", rendered)
                self.assertIn("$fenced$", rendered)
                self.assertIn("\\$5 stays.", rendered)
                # Each element is one line, so a blank line never splits the HTML block.
                for element in rendered.split('<span class="katex-source"')[1:]:
                    self.assertNotIn("\n", element.split("</span>")[0])

    def test_source_text_is_inert_markdown_and_keeps_line_breaks(self) -> None:
        import html, re
        tex = "f'(x) = \"a\" -- b ... |x| *e* _f_ [a](b) <c> & \\% d \\\\ % comment\r\n  + e\tg"
        element = render_katex.source_html(tex, True, True)
        match = re.fullmatch(
            r'<span class="katex-source" data-display="true" data-lenient="true">([^<]*)</span>', element
        )
        self.assertIsNotNone(match)
        text = match.group(1)
        # Markdown runs after the preprocessor: no raw ASCII punctuation or line break reaches it.
        self.assertRegex(text, r"^[^\x00-\x1f\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]*(&#[0-9]+;[^\x00-\x1f\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]*)*$")
        # A % comment still ends at its line once the browser decodes the text.
        self.assertEqual(html.unescape(text), tex.replace("\r\n", "\n"))

    def test_asset_header_is_relative_to_the_chapter(self) -> None:
        for path, root in ((None, ""), ("index.md", ""), ("_nav/x.md", "../"),
                           ("Blueprint/D5/S3/Page.md", "../../../")):
            with self.subTest(path=path):
                header = render_katex.asset_header(path)
                self.assertEqual(header, (
                    f'<link rel="stylesheet" href="{root}katex/katex.min.css">\n'
                    f'<script defer src="{root}katex/katex.min.js"></script>\n'
                    f'<script defer src="{root}katex/katex-page.js"></script>\n\n'
                ))

    def test_deep_nesting_that_overflowed_the_quickjs_stack_is_checked_and_kept(self) -> None:
        depth = 400
        tex = "\\left(" * depth + "x" + "\\right)" * depth
        out = self.render({"deep": "$$" + tex + "$$\n"})
        self.assertEqual(self.sources(out["deep"]), [(tex, True, False)])
        self.assertNotIn("katex-fallback", out["deep"])

    def test_github_inline_math_becomes_two_source_elements(self) -> None:
        content = "$`X_i=\\theta_i+\\epsilon_i`$，其中 $`\\epsilon_i\\sim N(0,1)`$。\n"
        rendered = self.render({"Library/Dynamics/abraham2024sharp.md": content})[
            "Library/Dynamics/abraham2024sharp.md"]
        self.assertEqual(self.sources(rendered), [
            ("X_i=\\theta_i+\\epsilon_i", False, False), ("\\epsilon_i\\sim N(0,1)", False, False),
        ])
        self.assertIn("，其中 ", rendered)
        self.assertNotIn("$`", rendered)
        self.assertNotIn("`$", rendered)

    def test_chapters_without_math_are_unchanged(self) -> None:
        out = self.render({"plain": "# Title\n\nNo math here.\n"})
        self.assertEqual(out["plain"], "# Title\n\nNo math here.\n")

    def test_a_chapter_with_only_fallbacks_loads_no_katex_assets(self) -> None:
        content, _stderr = self.run_chapter("bad $\\frac{x}{$\n")
        self.assertIn("katex-fallback", content)
        self.assertNotIn("katex/", content)

    def test_katex_parse_errors_fall_back_to_escaped_source_and_warn(self) -> None:
        import html, re
        sources = ["\\frac{x}{_*a*", "x^{1}^{2}+\\{1\\}`[b](c)`"]
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_katex.py")],
            input=json.dumps([{}, {"items": [{"Chapter": {
                "name": "Bad", "path": "Blueprint/Bad.md",
                "content": f"ok $x$ bad ${sources[0]}$ and\n\n$${sources[1]}$$\n", "sub_items": [],
            }}]}]),
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        content = json.loads(result.stdout)["items"][0]["Chapter"]["content"]
        self.assertTrue(content.startswith(render_katex.asset_header("Blueprint/Bad.md")))
        self.assertEqual(self.sources(content), [("x", False, False)])
        fallbacks = re.findall(r'<code class="katex-fallback"( data-display="true")?>([^<]*)</code>', content)
        self.assertEqual([html.unescape(text) for _, text in fallbacks], sources)
        self.assertEqual([bool(display) for display, _ in fallbacks], [False, True])
        # Markdown runs after the preprocessor: no raw ASCII punctuation may reach it.
        for _, text in fallbacks:
            self.assertRegex(text, r"^[^\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]*(&#[0-9]+;[^\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]*)*$")
        self.assertNotIn("$", content)
        warnings = [line for line in result.stderr.splitlines() if line.startswith("::warning ")]
        self.assertEqual(len(warnings), 2)
        self.assertIn("Blueprint/Bad.md: formula 1:", warnings[0])
        self.assertIn("Unexpected end of input", warnings[0])
        self.assertIn("Blueprint/Bad.md: formula 2:", warnings[1])
        self.assertIn("Double superscript", warnings[1])

    def run_chapter(self, content: str) -> tuple[str, str]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_katex.py")],
            input=json.dumps([{}, {"items": [{"Chapter": {
                "name": "Quote", "path": "Library/Quote.md", "content": content, "sub_items": [],
            }}]}]),
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["items"][0]["Chapter"]["content"], result.stderr

    def test_package_macros_and_markdown_escaped_backslashes_render(self) -> None:
        # Upstream 3b4cd0c6 quotes papers that use physics/dsfont/mathtools commands, and
        # some notes escape TeX backslashes for Markdown (`\\mathcal`, `\\\\`).
        formulas = ["\\Tr(\\rho) = \\tr \\rho", "\\ketbra{0}{1} + \\floor{x} + \\mathds{1}",
                    "\\Tilde{x} + \\mbox{if } \\norm{v}", "\\\\mathcal{Q}^+_\\\\alpha",
                    "\\begin{bmatrix} A &X \\\\\\\\ X^* &B \\end{bmatrix}"]
        content, stderr = self.run_chapter("".join(f"${formula}$\n" for formula in formulas))
        # The page gets the TeX that rendered: the escaping undone where only that renders.
        self.assertEqual([tex for tex, _, _ in self.sources(content)], [
            "\\Tr(\\rho) = \\tr \\rho", "\\ketbra{0}{1} + \\floor{x} + \\mathds{1}",
            "\\Tilde{x} + \\mbox{if } \\norm{v}", "\\mathcal{Q}^+_\\alpha",
            "\\begin{bmatrix} A &X \\\\\\\\ X^* &B \\end{bmatrix}",
        ])
        self.assertNotIn("katex-fallback", content)
        self.assertNotIn("data-lenient", content)
        self.assertNotIn("::warning", stderr)

    def test_paper_local_macros_are_marked_lenient(self) -> None:
        content, stderr = self.run_chapter("$\\qc(x) + \\scrT_1$ and $y$\n")
        self.assertEqual(self.sources(content),
                         [("\\qc(x) + \\scrT_1", False, True), ("y", False, False)])
        self.assertNotIn("katex-fallback", content)
        self.assertNotIn("::warning", stderr)
        self.assertIn("render-katex: 1 formulas show an undefined macro's name in red", stderr)

    def test_katex_internal_errors_on_one_formula_fall_back_and_warn(self) -> None:
        # KaTeX 0.16.4 throws a plain Error ("Font metrics not found") for astral
        # characters such as U+1D4D4; that one formula must not stop the site.
        source = "2\U0001d4d4(1)=\\log 2"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_katex.py")],
            input=json.dumps([{}, {"items": [{"Chapter": {
                "name": "Astral", "path": "Problems/astral.md",
                "content": f"ok $x$ then ${source}$\n", "sub_items": [],
            }}]}]),
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        content = json.loads(result.stdout)["items"][0]["Chapter"]["content"]
        self.assertEqual(self.sources(content), [("x", False, False)])
        self.assertEqual(content.count('class="katex-fallback"'), 1)
        warnings = [line for line in result.stderr.splitlines() if line.startswith("::warning ")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Problems/astral.md: formula 1:", warnings[0])
        self.assertIn("Font metrics", warnings[0])

    def test_renderer_failures_other_than_parse_errors_still_fail_the_build(self) -> None:
        with unittest.mock.patch.object(render_katex.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "katex-render: formula 0: RangeError: boom")
            with self.assertRaisesRegex(render_katex.RenderError, "boom"):
                render_katex.preprocess_book({"items": [{"Chapter": {
                    "name": "P", "path": "P.md", "content": "$x$\n", "sub_items": []}}]})

    def test_missing_package_is_reported_as_an_install_step_not_a_render_error(self) -> None:
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("katex_render.js", "katex_page.js"):
                shutil.copyfile(ROOT / "scripts" / name, Path(temporary) / name)
            result = subprocess.run(
                ["node", str(Path(temporary) / "katex_render.js")],
                input='[{"tex": "x", "display": false}]',
                capture_output=True, text=True, cwd=temporary,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("npm ci", result.stderr)

    def test_pinned_katex_package_supplies_the_published_assets(self) -> None:
        version = json.loads(NODE_MODULES.read_text(encoding="utf-8"))["version"]
        self.assertEqual(version, render_katex.KATEX_VERSION)
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"]["node_modules/katex"]["version"], version)
        self.assertTrue(lock["packages"]["node_modules/katex"]["integrity"].startswith("sha512-"))
        assets = render_katex.katex_assets()
        dist = ROOT / "node_modules" / "katex" / "dist"
        self.assertEqual(assets["katex.min.js"], dist / "katex.min.js")
        self.assertEqual(assets["katex.min.css"], dist / "katex.min.css")
        self.assertEqual(assets["katex-page.js"], ROOT / "scripts" / "katex_page.js")
        self.assertIn("MIT License", assets["LICENSE"].read_text(encoding="utf-8"))
        # Every font the stylesheet names is published next to it.
        import re
        stylesheet = (dist / "katex.min.css").read_text(encoding="utf-8")
        named = set(re.findall(r"url\((fonts/[^)]+)\)", stylesheet))
        self.assertTrue(named)
        self.assertLessEqual(named, set(assets))

    def test_page_script_contract_under_node(self) -> None:
        subprocess.run(["node", "--test", str(ROOT / "tests/katex_page.test.cjs")], check=True, cwd=ROOT)


if __name__ == "__main__":
    unittest.main()
