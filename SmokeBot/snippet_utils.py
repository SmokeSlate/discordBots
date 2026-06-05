"""Helpers for snippet dispatch decisions."""


def should_dispatch_prefix_snippets(*, is_bot_message: bool, is_self_message: bool) -> bool:
    """Allow human and third-party bot messages, but never recurse on our own output."""
    return not is_self_message
