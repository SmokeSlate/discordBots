import shutil
import subprocess
import unittest
from pathlib import Path

class ScriptManagerDOMTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for DOM regressions")
    def test_script_manager(self):
        subprocess.run(["node", "tests/test_script_manager.js"], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)
