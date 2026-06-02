"""Thread utilities for SmokeBot."""

from __future__ import annotations

from typing import Optional

import discord

# discord.py 2.x uses raw ints for auto_archive_duration: 60, 1440, 4320, 10080
_ARCHIVE_THRESHOLDS = [
    (60, 60),
    (1440, 1440),
    (4320, 4320),
    (10080, 10080),
]


def resolve_archive_duration(minutes: int) -> int:
    for threshold, value in _ARCHIVE_THRESHOLDS:
        if minutes <= threshold:
            return value
    return 10080


async def get_or_create_message_thread(
    message: discord.Message,
    name: str,
    archive_minutes: int = 60,
) -> Optional[discord.Thread]:
    """Return the existing thread on a message or create a new public thread."""
    if hasattr(message, "thread") and message.thread is not None:
        return message.thread

    if not isinstance(message.channel, discord.TextChannel):
        return None

    try:
        return await message.create_thread(
            name=name[:100],
            auto_archive_duration=resolve_archive_duration(archive_minutes),
        )
    except (discord.Forbidden, discord.HTTPException):
        return None
