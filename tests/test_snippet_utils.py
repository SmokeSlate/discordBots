import unittest

from SmokeBot.snippet_utils import should_dispatch_prefix_snippets


class SnippetDispatchGateTests(unittest.TestCase):
    def test_allows_human_messages(self):
        self.assertTrue(
            should_dispatch_prefix_snippets(
                is_bot_message=False,
                is_self_message=False,
            )
        )

    def test_allows_other_bot_messages(self):
        self.assertTrue(
            should_dispatch_prefix_snippets(
                is_bot_message=True,
                is_self_message=False,
            )
        )

    def test_blocks_self_messages_to_avoid_loops(self):
        self.assertFalse(
            should_dispatch_prefix_snippets(
                is_bot_message=True,
                is_self_message=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
