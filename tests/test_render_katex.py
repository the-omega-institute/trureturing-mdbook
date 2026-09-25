from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
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

    def test_renders_each_formula_once_and_leaves_text_and_code_alone(self) -> None:
        content = (
            "Inline $x^2$ then display\n\n$$\\sum_{i=1}^n i$$\n\n"
            "`code $k$` and\n```\n$fenced$\n```\nand \\$5 stays.\n"
        )
        out = self.render({"page": content})
        for name in ("page", "page/sub"):
            with self.subTest(name=name):
                rendered = out[name]
                self.assertTrue(rendered.startswith(render_katex.STYLESHEET_HEADER))
                self.assertEqual(rendered.count('class="katex"'), 2)
                self.assertEqual(rendered.count('class="katex-display"'), 1)
                self.assertNotIn("$$", rendered)
                self.assertIn("`code $k$`", rendered)
                self.assertIn("$fenced$", rendered)
                self.assertIn("\\$5 stays.", rendered)
                # Rendered HTML is one line, so a blank line never splits the HTML block.
                for fragment in rendered.split("<span class=\"katex\">")[1:]:
                    self.assertNotIn("\n\n", fragment.split("</span></span>")[0])

    def test_deep_nesting_that_overflowed_the_quickjs_stack_renders(self) -> None:
        depth = 400
        formula = "$$" + "\\left(" * depth + "x" + "\\right)" * depth + "$$"
        out = self.render({"deep": formula + "\n"})
        self.assertEqual(out["deep"].count('class="katex-display"'), 1)
        self.assertNotIn("$$", out["deep"])

    def test_renders_github_inline_math_as_two_real_katex_nodes(self) -> None:
        content = "$`X_i=\\theta_i+\\epsilon_i`$，其中 $`\\epsilon_i\\sim N(0,1)`$。\n"
        rendered = self.render({"Library/Dynamics/abraham2024sharp.md": content})[
            "Library/Dynamics/abraham2024sharp.md"]
        self.assertEqual(rendered.count('class="katex"'), 2)
        self.assertIn("，其中 ", rendered)
        self.assertNotIn("$`", rendered)
        self.assertNotIn("`$", rendered)

    def test_chapters_without_math_are_unchanged_except_the_stylesheet(self) -> None:
        out = self.render({"plain": "# Title\n\nNo math here.\n"})
        self.assertEqual(out["plain"], render_katex.STYLESHEET_HEADER + "# Title\n\nNo math here.\n")

    def test_katex_errors_fail_the_build_and_name_the_chapter(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_katex.py")],
            input=json.dumps([{}, {"items": [{"Chapter": {
                "name": "Bad", "path": "Blueprint/Bad.md", "content": "ok $x$ bad $\\notacommand{x}$\n", "sub_items": [],
            }}]}]),
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Blueprint/Bad.md", result.stderr)
        self.assertIn("notacommand", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_package_is_reported_as_an_install_step_not_a_render_error(self) -> None:
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "katex_render.js"
            shutil.copyfile(ROOT / "scripts" / "katex_render.js", script)
            result = subprocess.run(
                ["node", str(script)], input='[{"tex": "x", "display": false}]',
                capture_output=True, text=True, cwd=temporary,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("npm ci", result.stderr)

    def test_pinned_katex_version_matches_the_stylesheet(self) -> None:
        version = json.loads(NODE_MODULES.read_text(encoding="utf-8"))["version"]
        self.assertIn(f"katex@{version}/dist/katex.min.css", render_katex.STYLESHEET_HEADER)
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"]["node_modules/katex"]["version"], version)
        self.assertTrue(lock["packages"]["node_modules/katex"]["integrity"].startswith("sha512-"))


if __name__ == "__main__":
    unittest.main()
