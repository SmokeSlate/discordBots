import unittest
from unittest.mock import Mock, patch

from SmokeBot.auto_update import apply_git_update, get_git_update_status


class AutoUpdateTests(unittest.TestCase):
    @patch("SmokeBot.auto_update._run_git")
    def test_get_git_update_status_detects_behind(self, run_git: Mock):
        run_git.side_effect = [
            Mock(returncode=0, stdout="true\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
            Mock(returncode=0, stdout="abc\n", stderr=""),
            Mock(returncode=0, stdout="def\n", stderr=""),
        ]

        status = get_git_update_status(".", "origin", "main")

        self.assertTrue(status["ok"])
        self.assertFalse(status["up_to_date"])
        self.assertEqual("abc", status["local_sha"])
        self.assertEqual("def", status["remote_sha"])

    @patch("SmokeBot.auto_update._run_git")
    def test_apply_git_update_success(self, run_git: Mock):
        run_git.return_value = Mock(returncode=0, stdout="Already up to date.\n", stderr="")

        result = apply_git_update(".", "origin", "main")

        self.assertTrue(result["ok"])
        self.assertEqual(0, result["code"])
        self.assertIn("Already up to date", result["stdout"])


if __name__ == "__main__":
    unittest.main()
