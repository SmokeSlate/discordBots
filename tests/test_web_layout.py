import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = REPO_ROOT / "docs" / "assets" / "css" / "smokebot.css"
SCRIPTS_PAGE_PATH = REPO_ROOT / "docs" / "scripts" / "index.html"


def _extract_block(text: str, anchor: str, start: int = 0) -> tuple[str, int]:
    start = text.index(anchor, start)
    brace_start = text.index("{", start)
    depth = 1
    cursor = brace_start + 1

    while depth and cursor < len(text):
        char = text[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        cursor += 1

    if depth != 0:
        raise ValueError(f"Unbalanced CSS block for {anchor!r}")

    return text[brace_start + 1 : cursor - 1], cursor


def _extract_all_blocks(text: str, anchor: str) -> list[str]:
    blocks = []
    cursor = 0

    while True:
        try:
            block, cursor = _extract_block(text, anchor, start=cursor)
        except ValueError:
            break
        blocks.append(block)

    return blocks


class ScriptManagerLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.scripts_page = SCRIPTS_PAGE_PATH.read_text(encoding="utf-8")
        cls.desktop_layout_block, _ = _extract_block(cls.css, "@media (min-width: 1100px)")

    def test_scripts_page_uses_desktop_grid_panels(self):
        for required_class in (
            "manager-workspace",
            "manager-panel--session",
            "manager-panel--editor",
            "manager-panel--library",
        ):
            with self.subTest(required_class=required_class):
                self.assertIn(required_class, self.scripts_page)

    def test_desktop_layout_places_session_editor_and_library_panels(self):
        expected_rules = {
            ".manager-panel--session": ("grid-column: 1 / -1;",),
            ".manager-panel--editor": ("grid-column: 1;", "grid-row: 2;", "position: sticky;"),
            ".manager-panel--library": ("grid-column: 2;", "grid-row: 2;"),
        }

        for selector, declarations in expected_rules.items():
            with self.subTest(selector=selector):
                block = "\n".join(_extract_all_blocks(self.desktop_layout_block, selector))
                for declaration in declarations:
                    self.assertIn(declaration, block)


if __name__ == "__main__":
    unittest.main()
