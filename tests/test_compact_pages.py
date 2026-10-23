from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_build_site as fixtures
from scripts import compact_pages as compact


NOTICE = "<!--! Font Awesome Free 6.2.0 by @fontawesome - License - https://fontawesome.com/license/free -->"
BARS = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512">{NOTICE}<path d="M0 96h448"/></svg>'
LEFT = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 512">{NOTICE}<path d="M9 233l160-160"/></svg>'


def page(main: str) -> str:
    return (
        "<!DOCTYPE HTML>\n<html>\n    <head>\n        <!-- Book generated using mdBook -->\n"
        "        <script>\n            const path_to_root = \"../\";\n        </script>\n    </head>\n"
        f"    <body>\n        <label><span class=fa-svg>{BARS}</span></label>\n"
        f"        <main>\n{main}\n        </main>\n"
        f"        <a rel=\"prev\"><span class=fa-svg>{LEFT}</span></a>\n"
        f"        <a rel=\"prev\"><span class=fa-svg>{LEFT}</span></a>\n    </body>\n</html>\n"
    )


class CompactPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.book = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> Path:
        path = self.book / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_template_icons_point_into_one_sprite_and_content_is_untouched(self) -> None:
        main = (f"    <p>Kept   <!-- a content comment --></p>\n    <span class=fa-svg>{BARS}</span>\n"
                "    <pre>  indented\n      code</pre>")
        nested = self.write("Blueprint/D5/Page.html", page(main))
        top = self.write("index.html", page("<p>Home</p>"))
        result = subprocess.run(
            [sys.executable, str(fixtures.ROOT / "scripts/compact_pages.py"), str(self.book)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(result.stdout)["icons"], 2)
        compacted = nested.read_text(encoding="utf-8")
        start, end = compacted.index("<main>"), compacted.rindex("</main>")
        self.assertEqual(compacted[start + len("<main>"):end], f"\n{main}\n        ")
        template = compacted[:start] + compacted[end:]
        self.assertNotIn("Book generated", template)
        self.assertNotIn("<path", template)
        self.assertNotIn("\n ", template)
        self.assertIn('const path_to_root = "../";\n', template)
        uses = [line for line in template.splitlines() if "<use " in line]
        self.assertEqual(len(uses), 3)
        self.assertTrue(all('href="../../fa-icons.svg#i' in line for line in uses))
        self.assertIn('href="fa-icons.svg#i', top.read_text(encoding="utf-8"))
        sprite = (self.book / "fa-icons.svg").read_text(encoding="utf-8")
        self.assertEqual(sprite.count("<symbol "), 2)
        self.assertEqual(sprite.count("Font Awesome Free"), 1)
        for line in uses:
            name = line.split("#", 1)[1].split('"', 1)[0]
            self.assertIn(f'<symbol id="{name}"', sprite)
        # The release gate resolves each icon reference to the sprite.
        fixtures.verify_site.validate_links(self.book, [b"Blueprint/D5/Page.html", b"index.html"])
        # Running again changes nothing and keeps the sprite.
        before = {path: path.read_bytes() for path in self.book.rglob("*") if path.is_file()}
        compact.compact_pages(self.book)
        self.assertEqual({path: path.read_bytes() for path in self.book.rglob("*") if path.is_file()}, before)

    def test_pages_without_main_and_license_comments_stay(self) -> None:
        toc = self.write("toc.html", f"<ol>\n    <li>One</li>\n</ol>\n<!-- note -->{BARS}\n")
        compact.compact_pages(self.book)
        self.assertEqual(toc.read_text(encoding="utf-8"), f"<ol>\n    <li>One</li>\n</ol>\n<!-- note -->{BARS}\n")
        self.assertFalse((self.book / "fa-icons.svg").exists())
        self.assertEqual(compact.compact_template(f"  <!--! kept -->\n  <!-- dropped -->x", "", compact.Sprite()),
                         "  <!--! kept -->\nx")

    def test_missing_sprite_fails_the_gate(self) -> None:
        self.write("index.html", page("<p>Home</p>"))
        compact.compact_pages(self.book)
        (self.book / "fa-icons.svg").unlink()
        with self.assertRaisesRegex(fixtures.verify_site.VerificationError, "fa-icons.svg"):
            fixtures.verify_site.validate_links(self.book, [b"index.html"])


if __name__ == "__main__":
    unittest.main()
