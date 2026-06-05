import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT_PATH = REPO_ROOT / "SmokeBot" / "update_bot.sh"


class UpdateBotScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script_text = UPDATE_SCRIPT_PATH.read_text(encoding="utf-8")

    def test_downloads_required_python_modules(self):
        for required_file in ("main.py", "storage.py", "auto_update.py", "chat_utils.py"):
            with self.subTest(required_file=required_file):
                self.assertIn(f'"{required_file}"', self.script_text)


if __name__ == "__main__":
    unittest.main()
