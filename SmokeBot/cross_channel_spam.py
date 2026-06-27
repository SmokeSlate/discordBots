import asyncio
import hashlib
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Dict, List, Optional, Set, Tuple

import discord


CROSS_SPAM_STAFF_CHANNEL_ID = int(os.getenv("CROSS_SPAM_STAFF_CHANNEL_ID", "0") or 0)
CROSS_SPAM_WINDOW_SECONDS = int(os.getenv("CROSS_SPAM_WINDOW_SECONDS", "45"))
CROSS_SPAM_MIN_CHANNELS = int(os.getenv("CROSS_SPAM_MIN_CHANNELS", "4"))
CROSS_SPAM_ACCESSIBLE_RATIO = float(os.getenv("CROSS_SPAM_ACCESSIBLE_RATIO", "0.75"))
CROSS_SPAM_MAX_TRACKED_PER_USER = int(os.getenv("CROSS_SPAM_MAX_TRACKED_PER_USER", "80"))
CROSS_SPAM_DELETE_LIMIT = int(os.getenv("CROSS_SPAM_DELETE_LIMIT", "50"))
CROSS_SPAM_IGNORE_MANAGE_MESSAGES = os.getenv(
    "CROSS_SPAM_IGNORE_MANAGE_MESSAGES", "true"
).strip().lower() in {"1", "true", "yes", "on"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}


@dataclass
class SpamEntry:
    message: discord.Message
    timestamp: float
    signature: str
    channel_id: int


user_message_cache: DefaultDict[Tuple[int, int], List[SpamEntry]] = defaultdict(list)
active_incidents: Set[Tuple[int, int, str]] = set()


def normalize_text(content: str) -> str:
    content = content or ""
    content = content.strip().lower()
    content = re.sub(r"\s+", " ", content)
    return content


def attachment_is_image(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.lower().startswith("image/"):
        return True
    filename = (attachment.filename or "").lower()
    return any(filename.endswith(ext) for ext in IMAGE_EXTENSIONS)


async def hash_attachment(attachment: discord.Attachment) -> Optional[str]:
    if not attachment_is_image(attachment):
        return None

    try:
        data = await attachment.read(use_cached=True)
    except Exception:
        return f"image-name:{attachment.filename}:{attachment.size}"

    return hashlib.sha256(data).hexdigest()


async def build_message_signature(message: discord.Message) -> Optional[str]:
    text = normalize_text(message.content)
    image_hashes = []

    for attachment in message.attachments:
        image_hash = await hash_attachment(attachment)
        if image_hash:
            image_hashes.append(image_hash)

    if not text and not image_hashes:
        return None

    image_hashes.sort()
    raw_signature = f"text:{text}|images:{','.join(image_hashes)}"
    return hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()


def member_can_see_channel(member: discord.Member, channel: discord.abc.GuildChannel) -> bool:
    permissions = channel.permissions_for(member)
    return bool(permissions.view_channel and permissions.send_messages)


def count_accessible_text_channels(member: discord.Member) -> int:
    count = 0
    for channel in member.guild.channels:
        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            if member_can_see_channel(member, channel):
                count += 1
    return max(count, 1)


def required_channel_count(member: discord.Member) -> int:
    accessible = count_accessible_text_channels(member)
    ratio_required = max(1, round(accessible * CROSS_SPAM_ACCESSIBLE_RATIO))
    return min(accessible, max(CROSS_SPAM_MIN_CHANNELS, ratio_required))


class CrossSpamBanView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Ban User", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("You need Ban Members permission.", ephemeral=True)

        member = interaction.guild.get_member(self.user_id)
        if member is None:
            try:
                user = await interaction.client.fetch_user(self.user_id)
                await interaction.guild.ban(user, reason=f"Cross-channel spam ban by {interaction.user}", delete_message_days=1)
            except discord.HTTPException as exc:
                return await interaction.response.send_message(f"Could not ban user: {exc}", ephemeral=True)
        else:
            try:
                await member.ban(reason=f"Cross-channel spam ban by {interaction.user}", delete_message_days=1)
            except discord.HTTPException as exc:
                return await interaction.response.send_message(f"Could not ban user: {exc}", ephemeral=True)

        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"Banned <@{self.user_id}>.", allowed_mentions=discord.AllowedMentions.none())


async def delete_spam_messages(entries: List[SpamEntry]) -> Tuple[int, int]:
    deleted = 0
    failed = 0

    for entry in entries[:CROSS_SPAM_DELETE_LIMIT]:
        try:
            await entry.message.delete()
            deleted += 1
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            failed += 1
        await asyncio.sleep(0.15)

    return deleted, failed


async def notify_staff(
    bot: discord.Client,
    message: discord.Message,
    entries: List[SpamEntry],
    deleted: int,
    failed: int,
    required_channels: int,
):
    if not CROSS_SPAM_STAFF_CHANNEL_ID:
        return

    staff_channel = message.guild.get_channel(CROSS_SPAM_STAFF_CHANNEL_ID)
    if staff_channel is None:
        try:
            staff_channel = await bot.fetch_channel(CROSS_SPAM_STAFF_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    channel_mentions = []
    for channel_id in sorted({entry.channel_id for entry in entries}):
        channel_mentions.append(f"<# {channel_id}>".replace("<# ", "<#"))

    preview = message.content.strip()[:900] if message.content else "[image-only or attachment-only spam]"

    embed = discord.Embed(
        title="Cross-channel spam detected",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="User", value=f"{message.author.mention}\n`{message.author.id}`", inline=False)
    embed.add_field(name="Channels Hit", value=f"{len(set(e.channel_id for e in entries))}/{required_channels} required", inline=True)
    embed.add_field(name="Messages Deleted", value=str(deleted), inline=True)
    embed.add_field(name="Delete Failures", value=str(failed), inline=True)
    embed.add_field(name="Channels", value=" ".join(channel_mentions)[:1000] or "Unknown", inline=False)
    embed.add_field(name="Content Preview", value=preview or "None", inline=False)
    embed.set_footer(text="Use the button below to ban after review.")

    await staff_channel.send(embed=embed, view=CrossSpamBanView(message.author.id))


async def handle_cross_channel_spam(bot: discord.Client, message: discord.Message) -> bool:
    if message.guild is None:
        return False

    if message.author.bot:
        return False

    if not isinstance(message.author, discord.Member):
        return False

    if CROSS_SPAM_IGNORE_MANAGE_MESSAGES and message.author.guild_permissions.manage_messages:
        return False

    signature = await build_message_signature(message)
    if not signature:
        return False

    now = time.time()
    cache_key = (message.guild.id, message.author.id)
    cache = user_message_cache[cache_key]
    cutoff = now - CROSS_SPAM_WINDOW_SECONDS

    cache[:] = [entry for entry in cache if entry.timestamp >= cutoff]
    cache.append(SpamEntry(message=message, timestamp=now, signature=signature, channel_id=message.channel.id))

    if len(cache) > CROSS_SPAM_MAX_TRACKED_PER_USER:
        del cache[:-CROSS_SPAM_MAX_TRACKED_PER_USER]

    matching_entries = [entry for entry in cache if entry.signature == signature]
    hit_channels = {entry.channel_id for entry in matching_entries}
    required_channels = required_channel_count(message.author)

    if len(hit_channels) < required_channels:
        return False

    incident_key = (message.guild.id, message.author.id, signature)
    if incident_key in active_incidents:
        return True

    active_incidents.add(incident_key)
    try:
        deleted, failed = await delete_spam_messages(matching_entries)
        await notify_staff(bot, message, matching_entries, deleted, failed, required_channels)
        user_message_cache[cache_key] = []
        return True
    finally:
        active_incidents.discard(incident_key)
