# =====================================================
# Discord Bot
# - Custom ticket categories (SQLite single-file storage + slash cmds)
# - No "used /ticket" banner (ephemeral confirmations)
# - Pins, reaction roles, moderation, help
# - Snippet system (static & dynamic with placeholders)
# - Snippet migration to new JSON format
# =====================================================

import asyncio
import base64
import builtins
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

try:
    from .auto_update import apply_git_update, get_git_update_status
    from .storage import migrate_legacy_json_files, read_json, write_json
except ImportError:
    from auto_update import apply_git_update, get_git_update_status
    from storage import migrate_legacy_json_files, read_json, write_json

# =====================================================
# Token Loader
# =====================================================

def load_token():
    try:
        with open('token.txt', 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        print("Error: token.txt not found!")
        return None

# =====================================================
# Bot Configuration
# =====================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)
logger = logging.getLogger("smokebot")
script_api_runner: Optional[web.AppRunner] = None
script_api_sites: List[web.BaseSite] = []
script_api_lock = asyncio.Lock()
discord_token_cache: Dict[str, Tuple[float, dict]] = {}

reaction_roles = {}
ticket_data = {}
snippets = {}  # unified format after migration
auto_replies = {}
autoreply_cooldowns = {}
giveaways = {}
script_triggers = {}
auto_update_task = None
auto_update_lock = asyncio.Lock()

ALLOWED_SCRIPT_GUILDS = {1385295315245989999, 1102679144178921522}
TRUSTED_SCRIPT_USER_ID = 823654955025956895
AUTO_UPDATE_ENABLED = os.getenv("SMOKEBOT_AUTO_UPDATE", "false").strip().lower() in {"1", "true", "yes", "on"}
AUTO_UPDATE_INTERVAL_SECONDS = max(300, int(os.getenv("SMOKEBOT_AUTO_UPDATE_INTERVAL_SECONDS", "900")))
AUTO_UPDATE_REMOTE = os.getenv("SMOKEBOT_AUTO_UPDATE_REMOTE", "origin").strip() or "origin"
AUTO_UPDATE_BRANCH = os.getenv("SMOKEBOT_AUTO_UPDATE_BRANCH", "main").strip() or "main"
AUTO_UPDATE_REPO_DIR = os.getenv("SMOKEBOT_AUTO_UPDATE_REPO_DIR", ".").strip() or "."

# =====================================================
# Permissions Checkers
# =====================================================

def has_permissions_or_override(interaction: discord.Interaction) -> bool:
    return (interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.manage_messages)

def has_mod_permissions_or_override(interaction: discord.Interaction) -> bool:
    return (interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.moderate_members or
            interaction.user.guild_permissions.ban_members or
            interaction.user.guild_permissions.kick_members)

# =====================================================
# Generic file helpers for other data
# =====================================================

def save_reaction_roles():
    write_json('reaction_roles.json', reaction_roles)

def load_reaction_roles():
    global reaction_roles
    reaction_roles = read_json('reaction_roles.json', {})

def save_ticket_data():
    write_json('ticket_data.json', ticket_data)

def load_ticket_data():
    global ticket_data
    ticket_data = read_json('ticket_data.json', {})

def load_pinned_messages():
    return read_json('pinned_messages.json', {})

def load_giveaways():
    global giveaways
    giveaways = read_json('giveaways.json', {})

def save_giveaways():
    write_json('giveaways.json', giveaways)

# =====================================================
# Giveaway Helpers and Views
# =====================================================

def build_giveaway_embed(data: dict) -> discord.Embed:
    ended = data.get("ended", False)
    try:
        end_dt = datetime.fromisoformat(data.get("end_time", datetime.utcnow().isoformat()))
    except ValueError:
        end_dt = datetime.utcnow()
    end_ts = int(end_dt.timestamp())

    color = discord.Color.red() if ended else discord.Color.blurple()
    embed = discord.Embed(title=f"🎉 Giveaway: {data.get('prize', 'Prize')}", color=color)

    host_id = data.get("host_id")
    host_line = f"Hosted by: <@{host_id}>" if host_id else "Hosted by: Unknown"
    winner_count = data.get("winner_count", 1)
    required_role_id = data.get("required_role_id")
    try:
        required_role_id_int = int(required_role_id) if required_role_id else None
    except (TypeError, ValueError):
        required_role_id_int = None

    lines = [host_line, f"Winners: **{winner_count}**"]
    if required_role_id_int:
        lines.append(f"Required role: <@&{required_role_id_int}>")
    if ended:
        lines.append(f"Ended: <t:{end_ts}:R> (<t:{end_ts}:f>)")
    else:
        lines.append(f"Ends: <t:{end_ts}:R> (<t:{end_ts}:f>)")

    embed.description = "\n".join(lines)

    details = data.get("description")
    if details:
        embed.add_field(name="Details", value=details, inline=False)

    participants = data.get("participants", [])
    embed.add_field(name="Entries", value=str(len(participants)), inline=False)

    if ended:
        winner_ids = data.get("final_winners", [])
        if winner_ids:
            mentions = [f"<@{wid}>" for wid in winner_ids]
            embed.add_field(name="Winner(s)", value="\n".join(mentions), inline=False)
        else:
            embed.add_field(name="Winner(s)", value="No valid entries", inline=False)

    footer_text = "Giveaway concluded" if ended else "Click the button below to enter!"
    embed.set_footer(text=footer_text)

    return embed


async def update_giveaway_message(giveaway_id: str):
    data = giveaways.get(giveaway_id)
    if not data:
        return

    channel_id = data.get("channel_id")
    try:
        channel_id_int = int(channel_id)
    except (TypeError, ValueError):
        return

    channel = bot.get_channel(channel_id_int)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id_int)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    try:
        message = await channel.fetch_message(int(giveaway_id))
    except (discord.NotFound, discord.HTTPException):
        return

    try:
        await message.edit(embed=build_giveaway_embed(data))
    except discord.HTTPException:
        pass


async def conclude_giveaway(giveaway_id: str):
    data = giveaways.get(giveaway_id)
    if not data or data.get("ended"):
        return

    channel_id = data.get("channel_id")
    try:
        channel_id_int = int(channel_id)
    except (TypeError, ValueError):
        channel_id_int = None

    channel = bot.get_channel(channel_id_int) if channel_id_int else None
    if channel is None and channel_id_int:
        try:
            channel = await bot.fetch_channel(channel_id_int)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None

    message = None
    if channel is not None:
        try:
            message = await channel.fetch_message(int(giveaway_id))
        except (discord.NotFound, discord.HTTPException):
            message = None

    participants = data.get("participants", [])
    winner_count = max(1, int(data.get("winner_count", 1)))
    if len(participants) < winner_count:
        winner_count = len(participants)

    if winner_count > 0:
        winners = random.sample(participants, k=winner_count)
    else:
        winners = []

    data["ended"] = True
    data["final_winners"] = winners
    save_giveaways()

    if message is not None:
        try:
            await message.edit(embed=build_giveaway_embed(data), view=GiveawayJoinView(giveaway_id, disabled=True))
        except discord.HTTPException:
            pass

    if channel is not None:
        try:
            if winners:
                mentions = ", ".join(f"<@{wid}>" for wid in winners)
                announcement = (
                    f"🎉 Giveaway ended for **{data.get('prize', 'a prize')}**!\n"
                    f"Winners: {mentions}"
                )
                host_id = data.get("host_id")
                if host_id:
                    announcement += f"\nHosted by <@{host_id}>"
            else:
                announcement = f"😕 Giveaway for **{data.get('prize', 'a prize')}** ended with no valid entries."

            if message is not None:
                announcement += f"\n{message.jump_url}"

            await channel.send(announcement)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def schedule_giveaway_end(giveaway_id: str):
    await bot.wait_until_ready()
    data = giveaways.get(giveaway_id)
    if not data or data.get("ended"):
        return

    try:
        end_dt = datetime.fromisoformat(data.get("end_time", datetime.utcnow().isoformat()))
    except ValueError:
        end_dt = datetime.utcnow()

    delay = (end_dt - datetime.utcnow()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    await conclude_giveaway(giveaway_id)


class GiveawayJoinButton(discord.ui.Button):
    def __init__(self, giveaway_id: str, *, disabled: bool = False):
        super().__init__(
            label="Enter Giveaway",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway_join:{giveaway_id}",
            disabled=disabled
        )
        self.giveaway_id = giveaway_id

    async def callback(self, interaction: discord.Interaction):
        data = giveaways.get(self.giveaway_id)
        if not data:
            await interaction.response.send_message("❌ This giveaway could not be found.", ephemeral=True)
            return

        if data.get("ended"):
            await interaction.response.send_message("⏰ This giveaway has already ended.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("❌ This giveaway can only be joined from a server.", ephemeral=True)
            return

        required_role_id = data.get("required_role_id")
        if required_role_id:
            try:
                required_role = interaction.guild.get_role(int(required_role_id))
            except (TypeError, ValueError):
                required_role = None
            if required_role and required_role not in getattr(interaction.user, "roles", []):
                await interaction.response.send_message(
                    f"❌ You need the {required_role.mention} role to enter this giveaway.",
                    ephemeral=True
                )
                return

        user_id = str(interaction.user.id)
        participants = data.setdefault("participants", [])
        if user_id in participants:
            await interaction.response.send_message("✅ You're already entered in this giveaway!", ephemeral=True)
            return

        participants.append(user_id)
        save_giveaways()

        await interaction.response.send_message("🎉 You've entered the giveaway!", ephemeral=True)
        await update_giveaway_message(self.giveaway_id)


class GiveawayJoinView(discord.ui.View):
    def __init__(self, giveaway_id: str, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(GiveawayJoinButton(giveaway_id, disabled=disabled))

# =====================================================
# Snippet Storage with Migration
# New unified format per guild:
# snippets[guild_id] = {
#   "trigger": {"content": "text with {1}", "dynamic": True/False},
#   ...
# }
# =====================================================

def ensure_snippet_defaults(entry: dict) -> dict:
    entry.setdefault("content", "")
    entry.setdefault("dynamic", False)
    return entry


def load_snippets():
    """Load snippets and migrate any old formats to the unified object format."""
    global snippets
    raw = read_json('snippets.json', {})
    migrated = False

    for guild_id, triggers in raw.items():
        for trigger, value in list(triggers.items()):
            if isinstance(value, str):
                # Old format: just a string → convert to object static
                raw[guild_id][trigger] = {"content": value, "dynamic": False}
                migrated = True
            elif isinstance(value, dict):
                before = dict(value)
                ensure_snippet_defaults(raw[guild_id][trigger])
                if raw[guild_id][trigger] != before:
                    migrated = True

            raw[guild_id][trigger] = ensure_snippet_defaults(raw[guild_id][trigger])

    if migrated:
        write_json('snippets.json', raw)

    snippets = raw

def save_snippets():
    write_json('snippets.json', snippets)


def extract_numeric_id(token: str) -> Optional[int]:
    match = re.search(r"\d+", token or "")
    return int(match.group(0)) if match else None


def parse_role_input(guild: discord.Guild, raw: Optional[str]) -> Tuple[List[str], List[str]]:
    if not raw:
        return [], []

    values = []
    invalid = []
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        rid = extract_numeric_id(token)
        role = guild.get_role(rid) if rid else None
        if role:
            values.append(str(role.id))
        else:
            invalid.append(token)
    return values, invalid


def parse_channel_input(guild: discord.Guild, raw: Optional[str]) -> Tuple[List[str], List[str]]:
    if not raw:
        return [], []

    values = []
    invalid = []
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        cid = extract_numeric_id(token)
        channel = guild.get_channel_or_thread(cid) if cid else None
        if channel:
            values.append(str(channel.id))
        else:
            invalid.append(token)
    return values, invalid


def parse_duration_string(raw: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    if raw is None:
        return None, None

    text = raw.strip().lower()
    if not text or text in {"none", "off", "clear", "disable", "disabled"}:
        return 0, None

    if text.isdigit():
        seconds = int(text)
        if seconds < 0:
            return None, "Duration cannot be negative."
        return seconds, None

    total = 0
    matches = list(re.finditer(r"(\d+)([smhdw])", text))
    if not matches:
        return None, "Use formats like 30s, 5m, 2h, 1d, or 1w (combinable)."

    for match in matches:
        amount = int(match.group(1))
        unit = match.group(2)
        multiplier = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800,
        }[unit]
        total += amount * multiplier

    return total, None


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "disabled"

    parts = []
    remainder = seconds
    days, remainder = divmod(remainder, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, remainder = divmod(remainder, 60)
    secs = remainder

    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


async def render_snippet_content(
    message: discord.Message,
    entry: dict,
    args: List[str],
) -> str:
    content = entry.get("content", "")
    if entry.get("dynamic"):
        for i, value in enumerate(args, start=1):
            content = content.replace(f"{{{i}}}", value)
        content = re.sub(r"\{\d+\}", "", content)

    if "{ping}" in content:
        mention_target = None
        if message.reference and message.reference.message_id:
            try:
                replied = await message.channel.fetch_message(message.reference.message_id)
                mention_target = replied.author
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                mention_target = None
        if not mention_target:
            mention_target = message.author

        replacement = mention_target.mention if mention_target else ""
        content = content.replace("{ping}", replacement)

    return content


def build_reply_reference(message: discord.Message) -> Optional[discord.MessageReference]:
    """Return the message reference the user replied to, if any."""

    if not message.reference:
        return None

    ref = message.reference
    return discord.MessageReference(
        message_id=ref.message_id,
        channel_id=ref.channel_id or (ref.resolved.channel.id if ref.resolved else None),
        guild_id=ref.guild_id,
        fail_if_not_exists=False,
    )


async def dispatch_snippet(
    message: discord.Message,
    trigger: str,
    entry: dict,
    args: List[str],
    *,
    delete_trigger: bool = False,
) -> bool:
    if delete_trigger:
        try:
            await message.delete()
        except discord.Forbidden:
            pass

    content = await render_snippet_content(message, entry, args)

    reference = build_reply_reference(message)
    mention_author = reference is not None

    try:
        await message.channel.send(
            content,
            reference=reference,
            mention_author=mention_author,
        )
    except discord.Forbidden:
        try:
            await message.channel.send(
                f"⚠️ I don't have permission to send messages! Snippet would be: {content}"
            )
        except discord.HTTPException:
            return False
    except discord.HTTPException:
        return False
    return True

# =====================================================
# Auto Reply Storage and Helpers
# =====================================================


def ensure_autoreply_defaults(entry: dict) -> dict:
    entry.setdefault("pattern", "")
    entry.setdefault("response", "")
    entry.setdefault("dynamic", False)
    entry.setdefault("match_type", "regex")
    entry.setdefault("case_sensitive", False)
    entry.setdefault("snippet", "")
    entry.setdefault("include_roles", [])
    entry.setdefault("exclude_roles", [])
    entry.setdefault("include_channels", [])
    entry.setdefault("exclude_channels", [])
    entry.setdefault("cooldown_seconds", 0)
    entry.setdefault("cooldown_scope", "guild")
    return entry


def load_auto_replies():
    global auto_replies
    raw = read_json("auto_replies.json", {})
    migrated = False

    for guild_id, replies in list(raw.items()):
        if isinstance(replies, list):
            converted: Dict[str, dict] = {}
            for idx, item in enumerate(replies):
                entry: dict
                name: str

                if isinstance(item, dict):
                    entry = dict(item)
                    name = str(
                        entry.get("pattern")
                        or entry.get("name")
                        or f"entry_{idx}"
                    )
                    if not entry.get("pattern"):
                        entry["pattern"] = name
                elif isinstance(item, (list, tuple)):
                    if not item:
                        continue
                    pattern = str(item[0])
                    response = str(item[1]) if len(item) > 1 else pattern
                    name = pattern or f"entry_{idx}"
                    entry = {
                        "pattern": pattern,
                        "response": response,
                        "dynamic": False,
                    }
                else:
                    name = f"entry_{idx}"
                    entry = {
                        "pattern": str(item),
                        "response": str(item),
                        "dynamic": False,
                    }

                original_name = name
                suffix = 1
                while name in converted:
                    suffix += 1
                    name = f"{original_name}_{suffix}"

                converted[name] = ensure_autoreply_defaults(entry)

            raw[guild_id] = converted
            replies = converted
            migrated = True

        for name, data in list(replies.items()):
            if not isinstance(data, dict):
                raw[guild_id][name] = ensure_autoreply_defaults({
                    "pattern": name,
                    "response": str(data),
                    "dynamic": False,
                })
                migrated = True
            else:
                before = dict(data)
                raw[guild_id][name] = ensure_autoreply_defaults(data)
                if not raw[guild_id][name].get("pattern"):
                    raw[guild_id][name]["pattern"] = name
                match_type = str(raw[guild_id][name].get("match_type") or "regex").lower()
                if match_type not in {"regex", "contains"}:
                    raw[guild_id][name]["match_type"] = "regex"
                    migrated = True
                if raw[guild_id][name].get("snippet") is None:
                    raw[guild_id][name]["snippet"] = ""
                    migrated = True
                if not isinstance(raw[guild_id][name].get("case_sensitive"), bool):
                    raw[guild_id][name]["case_sensitive"] = bool(
                        raw[guild_id][name].get("case_sensitive")
                    )
                    migrated = True
                if raw[guild_id][name] != before:
                    migrated = True

    if migrated:
        write_json("auto_replies.json", raw)

    auto_replies = raw


def save_auto_replies():
    write_json("auto_replies.json", auto_replies)


def member_role_ids(member: discord.Member) -> List[str]:
    return [str(role.id) for role in getattr(member, "roles", []) if getattr(role, "id", None)]


def auto_reply_restrictions_pass(entry: dict, message: discord.Message) -> bool:
    if not message.guild:
        return False

    author_roles = set(member_role_ids(message.author))
    include_roles = set(entry.get("include_roles") or [])
    exclude_roles = set(entry.get("exclude_roles") or [])
    include_channels = set(entry.get("include_channels") or [])
    exclude_channels = set(entry.get("exclude_channels") or [])

    if include_roles and not (author_roles & include_roles):
        return False

    if exclude_roles and (author_roles & exclude_roles):
        return False

    channel_id = str(getattr(message.channel, "id", ""))
    if include_channels and channel_id not in include_channels:
        return False

    if exclude_channels and channel_id in exclude_channels:
        return False

    return True


def determine_autoreply_cooldown_bucket(entry: dict, message: discord.Message) -> Tuple[Optional[str], int]:
    seconds = int(entry.get("cooldown_seconds") or 0)
    if seconds <= 0 or not message.guild:
        return None, seconds

    scope = str(entry.get("cooldown_scope") or "guild").lower()
    if scope == "user":
        bucket = f"user:{message.author.id}"
    elif scope == "member":
        bucket = f"member:{message.guild.id}:{message.author.id}"
    elif scope == "channel":
        bucket = f"channel:{message.channel.id}"
    elif scope == "channel_user":
        bucket = f"channel_user:{message.channel.id}:{message.author.id}"
    elif scope == "category":
        category_id = getattr(message.channel, "category_id", None)
        if category_id:
            bucket = f"category:{message.guild.id}:{category_id}"
        else:
            bucket = f"guild:{message.guild.id}"
    elif scope == "category_user":
        category_id = getattr(message.channel, "category_id", None)
        if category_id:
            bucket = f"category_user:{message.guild.id}:{category_id}:{message.author.id}"
        else:
            bucket = f"channel_user:{message.channel.id}:{message.author.id}"
    elif scope == "thread":
        thread_id = getattr(message.channel, "id", None)
        bucket = f"thread:{thread_id}" if thread_id else f"channel:{message.channel.id}"
    elif scope == "role":
        roles = member_role_ids(message.author)
        primary = roles[0] if roles else "norole"
        bucket = f"role:{message.guild.id}:{primary}"
    else:
        bucket = f"guild:{message.guild.id}"

    return bucket, seconds


def autoreply_on_cooldown(guild_id: str, name: str, bucket: Optional[str], seconds: int) -> bool:
    if not bucket or seconds <= 0:
        return False

    now = datetime.utcnow().timestamp()
    trigger_cooldowns = autoreply_cooldowns.setdefault(guild_id, {}).setdefault(name, {})
    last = trigger_cooldowns.get(bucket)
    if last and now - last < seconds:
        return True
    return False


def mark_autoreply_cooldown(guild_id: str, name: str, bucket: Optional[str]):
    if not bucket:
        return
    trigger_cooldowns = autoreply_cooldowns.setdefault(guild_id, {}).setdefault(name, {})
    trigger_cooldowns[bucket] = datetime.utcnow().timestamp()


async def render_auto_reply_content(
    message: discord.Message,
    entry: dict,
    match: Optional[re.Match],
) -> str:
    content = entry.get("response", "")

    if entry.get("dynamic"):
        groups = match.groups() if match else ()
        for i, value in enumerate(groups, start=1):
            replacement = value if value is not None else ""
            content = content.replace(f"{{{i}}}", replacement)
        content = re.sub(r"\{\d+\}", "", content)

    if "{ping}" in content:
        content = content.replace("{ping}", message.author.mention)

    return content


async def dispatch_auto_reply(
    message: discord.Message,
    name: str,
    entry: dict,
    match: Optional[re.Match],
) -> bool:
    if not message.guild:
        return False

    if not auto_reply_restrictions_pass(entry, message):
        return False

    gid = str(message.guild.id)
    bucket, seconds = determine_autoreply_cooldown_bucket(entry, message)
    if autoreply_on_cooldown(gid, name, bucket, seconds):
        return False

    snippet_trigger = str(entry.get("snippet") or "").strip()
    if snippet_trigger:
        guild_snippets = snippets.get(gid, {})
        snippet_entry = guild_snippets.get(snippet_trigger)
        if snippet_entry:
            args: List[str] = []
            if entry.get("dynamic") and match:
                args = [(group if group is not None else "") for group in match.groups()]
            handled = await dispatch_snippet(
                message,
                snippet_trigger,
                snippet_entry,
                args,
                delete_trigger=False,
            )
            if handled:
                mark_autoreply_cooldown(gid, name, bucket)
            return handled

    content = await render_auto_reply_content(message, entry, match)
    if not content:
        return False

    reference = build_reply_reference(message)
    mention_author = reference is not None

    try:
        await message.channel.send(
            content,
            reference=reference,
            mention_author=mention_author,
        )
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False

    mark_autoreply_cooldown(gid, name, bucket)
    return True

# =====================================================
# Script Triggers (Python snippets)
# =====================================================


def ensure_script_trigger_defaults(entry: dict) -> dict:
    entry.setdefault("event", "message")
    entry.setdefault("pattern", "")
    entry.setdefault("match_type", "contains")
    entry.setdefault("channel_ids", [])
    entry.setdefault("code", "")
    entry.setdefault("enabled", True)
    return entry


def load_script_triggers():
    global script_triggers
    raw = read_json("script_triggers.json", {})
    migrated = False

    for guild_id, triggers in list(raw.items()):
        if not isinstance(triggers, dict):
            raw[guild_id] = {}
            migrated = True
            continue

        for name, entry in list(triggers.items()):
            if not isinstance(entry, dict):
                raw[guild_id][name] = ensure_script_trigger_defaults({
                    "pattern": str(name),
                    "code": str(entry),
                })
                migrated = True
                continue

            before = dict(entry)
            raw[guild_id][name] = ensure_script_trigger_defaults(entry)
            match_type = str(raw[guild_id][name].get("match_type") or "contains").lower()
            if match_type not in {"regex", "contains", "exact"}:
                raw[guild_id][name]["match_type"] = "contains"
                migrated = True
            event_name = str(raw[guild_id][name].get("event") or "message").lower()
            if event_name not in {"message", "message_all", "reply", "reaction_add", "reaction_remove", "member_join", "member_leave"}:
                raw[guild_id][name]["event"] = "message"
                migrated = True
            channel_ids = raw[guild_id][name].get("channel_ids", [])
            if not isinstance(channel_ids, list):
                raw[guild_id][name]["channel_ids"] = []
                migrated = True
            else:
                normalized_channels = []
                for channel_id in channel_ids:
                    try:
                        normalized_channels.append(int(channel_id))
                    except (TypeError, ValueError):
                        continue
                if normalized_channels != channel_ids:
                    raw[guild_id][name]["channel_ids"] = normalized_channels
                    migrated = True
            if raw[guild_id][name] != before:
                migrated = True

    if migrated:
        write_json("script_triggers.json", raw)

    script_triggers = raw


def save_script_triggers():
    write_json("script_triggers.json", script_triggers)


def _script_api_allowed_origins() -> List[str]:
    raw = os.getenv("SCRIPT_MANAGER_ORIGIN", "*")
    allowed = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return allowed or ["*"]


def _script_manager_site_origin() -> str:
    configured = os.getenv("SCRIPT_MANAGER_SITE_ORIGIN", "").strip()
    if configured:
        return configured.rstrip("/")

    for origin in _script_api_allowed_origins():
        if origin != "*":
            return origin.rstrip("/")
    return "https://bot.sm0ke.org"


def _script_manager_public_base() -> str:
    return os.getenv("SCRIPT_MANAGER_PUBLIC_BASE", "https://botapi.sm0ke.org").strip().rstrip("/")


def _script_manager_discord_client_id() -> str:
    return (
        os.getenv("SCRIPT_MANAGER_DISCORD_CLIENT_ID", "").strip()
        or os.getenv("DISCORD_CLIENT_ID", "").strip()
        or "1375925201191178300"
    )


def _script_manager_discord_client_secret() -> str:
    return (
        os.getenv("SCRIPT_MANAGER_DISCORD_CLIENT_SECRET", "").strip()
        or os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    )


def _script_manager_callback_url() -> str:
    return f"{_script_manager_public_base()}/api/script-auth/callback"


def _script_manager_default_return_url() -> str:
    return f"{_script_manager_site_origin()}/scripts/"


def _script_manager_is_allowed_return_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        allowed = urllib.parse.urlparse(_script_manager_site_origin())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc == allowed.netloc


def _script_manager_encode_state(return_url: str) -> str:
    return base64.urlsafe_b64encode(return_url.encode("utf-8")).decode("ascii")


def _script_manager_decode_state(state: str) -> str:
    padded = state + "=" * (-len(state) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def _script_api_response_headers(request: web.Request) -> Dict[str, str]:
    origin = request.headers.get("Origin", "").strip()
    allowed_origins = _script_api_allowed_origins()
    allow_origin: Optional[str] = None

    if "*" in allowed_origins:
        allow_origin = origin or "*"
    elif origin and origin in allowed_origins:
        allow_origin = origin
    elif not origin and allowed_origins:
        allow_origin = allowed_origins[0]

    headers = {
        "Access-Control-Allow-Headers": "Authorization, Content-Type, X-API-Key",
        "Access-Control-Allow-Methods": "GET, PUT, DELETE, OPTIONS",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }
    if allow_origin:
        headers["Access-Control-Allow-Origin"] = allow_origin
    return headers


def _script_api_response(request: web.Request, payload: Any, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, headers=_script_api_response_headers(request))


def _script_api_bot_has_guild(guild_id: str) -> bool:
    try:
        guild_int = int(guild_id)
    except (TypeError, ValueError):
        return False
    return bot.get_guild(guild_int) is not None


def _discord_has_manage_guild_permissions(guild_payload: dict) -> bool:
    permissions_raw = guild_payload.get("permissions")
    try:
        permissions = int(str(permissions_raw))
    except (TypeError, ValueError):
        return False
    ADMINISTRATOR = 0x00000008
    MANAGE_GUILD = 0x00000020
    return bool(permissions & ADMINISTRATOR or permissions & MANAGE_GUILD)


def _extract_discord_bearer_token(request: web.Request) -> str:
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


async def _discord_get_user_identity(access_token: str) -> dict:
    now = time.time()
    cached = discord_token_cache.get(access_token)
    if cached and cached[0] > now:
        return cached[1]

    def _request():
        request = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "SmokeBot Script Manager/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "Discord identity lookup failed with status %s: %s",
                exc.code,
                body or "<empty body>",
            )
            raise

    identity = await asyncio.to_thread(_request)
    discord_token_cache[access_token] = (now + 30, identity)
    return identity


async def _script_api_get_member_for_guild(user_id: str, guild_id: str) -> Optional[discord.Member]:
    try:
        guild_int = int(guild_id)
        user_int = int(user_id)
    except (TypeError, ValueError):
        return None

    guild = bot.get_guild(guild_int)
    if guild is None:
        return None

    member = guild.get_member(user_int)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_int)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def _script_api_authorize_request(request: web.Request) -> Tuple[bool, Optional[str], Optional[dict]]:
    access_token = _extract_discord_bearer_token(request)
    if access_token:
        try:
            identity = await _discord_get_user_identity(access_token)
            user_id = str(identity.get("id") or "").strip()
            if not user_id:
                return False, "Discord authentication failed", None
            return True, None, identity
        except Exception:
            logger.exception("Failed Discord OAuth validation for script manager request.")
            return False, "Discord authentication failed", None

    configured = os.getenv("SCRIPT_MANAGER_API_KEY", "").strip()
    provided = request.headers.get("X-API-Key", "").strip()
    if configured and provided == configured:
        return True, None, None
    return False, "Unauthorized", None


async def _script_api_can_manage_guild(request: web.Request, guild_id: str) -> Tuple[bool, Optional[str]]:
    if not _script_api_bot_has_guild(guild_id):
        return False, "Guild not managed by bot"
    if not _script_api_guild_allowed(guild_id):
        return False, "Script triggers are not enabled for this server"

    authorized, error, identity = await _script_api_authorize_request(request)
    if not authorized:
        return False, error or "Unauthorized"

    if identity:
        member = await _script_api_get_member_for_guild(str(identity.get("id") or ""), guild_id)
        if member is None:
            return False, "You do not have access to this server"
        if not (member.guild_permissions.administrator or member.guild_permissions.manage_guild):
            return False, "You need Manage Server permission"

    return True, None


def _script_api_guild_allowed(guild_id: str) -> bool:
    try:
        guild_int = int(guild_id)
    except (TypeError, ValueError):
        return False
    return guild_int in ALLOWED_SCRIPT_GUILDS


def _sanitize_script_entry(payload: dict) -> dict:
    entry = ensure_script_trigger_defaults(payload if isinstance(payload, dict) else {})
    channel_ids = entry.get("channel_ids") or []
    valid_channel_ids = []
    for channel_id in channel_ids:
        try:
            valid_channel_ids.append(int(channel_id))
        except (TypeError, ValueError):
            continue
    entry["channel_ids"] = list(dict.fromkeys(valid_channel_ids))
    entry["code"] = str(entry.get("code") or "")
    entry["pattern"] = str(entry.get("pattern") or "")
    entry["event"] = str(entry.get("event") or "message")
    entry["match_type"] = str(entry.get("match_type") or "contains")
    entry["enabled"] = bool(entry.get("enabled", True))
    return entry


async def script_api_options(_request: web.Request) -> web.Response:
    return _script_api_response(_request, {"ok": True})


async def script_api_health(request: web.Request) -> web.Response:
    return _script_api_response(
        request,
        {
            "ok": True,
            "bot_ready": bot.is_ready(),
            "guild_count": len(bot.guilds) if bot.is_ready() else 0,
        },
    )


async def script_auth_login(request: web.Request) -> web.Response:
    return_to = request.query.get("return_to", "").strip() or _script_manager_default_return_url()
    if not _script_manager_is_allowed_return_url(return_to):
        return web.Response(text="Invalid return_to", status=400)

    client_id = _script_manager_discord_client_id()
    if not client_id:
        return web.Response(text="Discord client id is not configured.", status=500)

    auth_url = urllib.parse.urlparse("https://discord.com/oauth2/authorize")
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": _script_manager_callback_url(),
            "scope": "identify guilds",
            "prompt": "consent",
            "state": _script_manager_encode_state(return_to),
        }
    )
    raise web.HTTPFound(f"{auth_url.scheme}://{auth_url.netloc}{auth_url.path}?{query}")


async def script_auth_callback(request: web.Request) -> web.Response:
    code = request.query.get("code", "").strip()
    state = request.query.get("state", "").strip()
    error = request.query.get("error", "").strip()

    try:
        return_to = _script_manager_decode_state(state) if state else _script_manager_default_return_url()
    except Exception:
        return_to = _script_manager_default_return_url()

    if not _script_manager_is_allowed_return_url(return_to):
        return_to = _script_manager_default_return_url()

    if error:
        raise web.HTTPFound(f"{return_to}#oauth_error={urllib.parse.quote(error)}")

    client_id = _script_manager_discord_client_id()
    client_secret = _script_manager_discord_client_secret()
    if not code or not client_id or not client_secret:
        raise web.HTTPFound(f"{return_to}#oauth_error={urllib.parse.quote('configuration_error')}")

    form_body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _script_manager_callback_url(),
        }
    ).encode("utf-8")
    basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")

    token_request = urllib.request.Request(
        "https://discord.com/api/v10/oauth2/token",
        data=form_body,
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "SmokeBot Script Manager/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(token_request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.warning("Discord token exchange failed with status %s: %s", exc.code, body or "<empty body>")
        raise web.HTTPFound(f"{return_to}#oauth_error={urllib.parse.quote('token_exchange_failed')}")
    except Exception:
        logger.exception("Discord token exchange failed unexpectedly.")
        raise web.HTTPFound(f"{return_to}#oauth_error={urllib.parse.quote('token_exchange_failed')}")

    access_token = str(payload.get("access_token") or "").strip()
    scope = str(payload.get("scope") or "").strip()
    expires_in = str(payload.get("expires_in") or "").strip()

    if not access_token:
        raise web.HTTPFound(f"{return_to}#oauth_error={urllib.parse.quote('missing_access_token')}")

    fragment = urllib.parse.urlencode(
        {
            "access_token": access_token,
            "scope": scope,
            "expires_in": expires_in,
        }
    )
    raise web.HTTPFound(f"{return_to}#{fragment}")


async def script_api_get_triggers(request: web.Request) -> web.Response:
    guild_id = request.match_info.get("guild_id", "")
    allowed, error = await _script_api_can_manage_guild(request, guild_id)
    if not allowed:
        return _script_api_response(request, {"error": error}, status=403 if error and "Unauthorized" not in error else 401)

    return _script_api_response(request, {"guild_id": guild_id, "triggers": script_triggers.get(guild_id, {})})


async def script_api_upsert_trigger(request: web.Request) -> web.Response:
    guild_id = request.match_info.get("guild_id", "")
    trigger_name = request.match_info.get("name", "").strip()
    if not trigger_name:
        return _script_api_response(request, {"error": "Missing trigger name"}, status=400)
    allowed, error = await _script_api_can_manage_guild(request, guild_id)
    if not allowed:
        return _script_api_response(request, {"error": error}, status=403 if error and "Unauthorized" not in error else 401)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _script_api_response(request, {"error": "Invalid JSON payload"}, status=400)

    entry = _sanitize_script_entry(body)
    async with script_api_lock:
        guild_triggers = script_triggers.setdefault(guild_id, {})
        guild_triggers[trigger_name] = entry
        save_script_triggers()

    return _script_api_response(request, {"ok": True, "name": trigger_name, "entry": entry})


async def script_api_delete_trigger(request: web.Request) -> web.Response:
    guild_id = request.match_info.get("guild_id", "")
    trigger_name = request.match_info.get("name", "").strip()
    if not trigger_name:
        return _script_api_response(request, {"error": "Missing trigger name"}, status=400)
    allowed, error = await _script_api_can_manage_guild(request, guild_id)
    if not allowed:
        return _script_api_response(request, {"error": error}, status=403 if error and "Unauthorized" not in error else 401)

    async with script_api_lock:
        guild_triggers = script_triggers.get(guild_id, {})
        if trigger_name in guild_triggers:
            del guild_triggers[trigger_name]
            save_script_triggers()
            return _script_api_response(request, {"ok": True, "name": trigger_name})

    return _script_api_response(request, {"error": "Script trigger not found"}, status=404)


async def script_api_list_manageable_guilds(request: web.Request) -> web.Response:
    authorized, error, identity = await _script_api_authorize_request(request)
    if not authorized:
        return _script_api_response(request, {"error": error or "Unauthorized"}, status=401)

    manageable = []
    user_id = str((identity or {}).get("id") or "").strip()
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if not user_id:
            continue
        if not _script_api_bot_has_guild(guild_id):
            continue
        if not _script_api_guild_allowed(guild_id):
            continue
        member = await _script_api_get_member_for_guild(user_id, guild_id)
        if member is None:
            continue
        if not (member.guild_permissions.administrator or member.guild_permissions.manage_guild):
            continue
        manageable.append(
            {
                "id": guild_id,
                "name": guild.name or guild_id,
                "icon": str(guild.icon.url) if guild.icon else None,
            }
        )

    return _script_api_response(request, {"guilds": manageable})


async def start_script_manager_api():
    global script_api_runner, script_api_sites
    if script_api_runner is not None:
        return

    app = web.Application()
    app.router.add_get("/healthz", script_api_health)
    app.router.add_get("/api/script-auth/login", script_auth_login)
    app.router.add_get("/api/script-auth/callback", script_auth_callback)
    app.router.add_route("OPTIONS", "/api/script-triggers/guilds", script_api_options)
    app.router.add_route("OPTIONS", "/api/script-triggers/{guild_id}", script_api_options)
    app.router.add_route("OPTIONS", "/api/script-triggers/{guild_id}/{name}", script_api_options)
    app.router.add_get("/api/script-triggers/guilds", script_api_list_manageable_guilds)
    app.router.add_get("/api/script-triggers/{guild_id}", script_api_get_triggers)
    app.router.add_put("/api/script-triggers/{guild_id}/{name}", script_api_upsert_trigger)
    app.router.add_delete("/api/script-triggers/{guild_id}/{name}", script_api_delete_trigger)

    script_api_runner = web.AppRunner(app)
    await script_api_runner.setup()
    hosts_raw = os.getenv("SCRIPT_MANAGER_API_HOST", "0.0.0.0,::").strip() or "0.0.0.0,::"
    hosts = [host.strip() for host in hosts_raw.split(",") if host.strip()]
    port = int(os.getenv("SCRIPT_MANAGER_API_PORT", "8080"))
    started_hosts: List[str] = []

    for host in hosts:
        try:
            site = web.TCPSite(script_api_runner, host=host, port=port)
            await site.start()
            script_api_sites.append(site)
            started_hosts.append(host)
        except OSError:
            logger.exception("Failed to bind script manager API on http://%s:%s", host, port)

    if not started_hosts:
        raise RuntimeError(f"Failed to bind script manager API on any configured host for port {port}")

    logger.info("Script manager API listening on http port %s via hosts: %s", port, ", ".join(started_hosts))


def script_guild_allowed(guild: Optional[discord.Guild]) -> bool:
    return guild is not None and guild.id in ALLOWED_SCRIPT_GUILDS


def message_trigger_match(entry: dict, message: discord.Message) -> Optional[re.Match]:
    pattern = str(entry.get("pattern") or "")
    if not pattern:
        return None

    match_type = str(entry.get("match_type") or "contains").lower()
    if match_type == "exact":
        return re.match(r"^.*$", message.content) if message.content == pattern else None

    if match_type == "contains":
        return re.match(r"^.*$", message.content) if pattern in message.content else None

    try:
        return re.search(pattern, message.content)
    except re.error:
        return None


def trigger_match_text(entry: dict, text: str) -> Optional[re.Match]:
    pattern = str(entry.get("pattern") or "")
    if not pattern:
        return None

    match_type = str(entry.get("match_type") or "contains").lower()
    if match_type == "exact":
        return re.match(r"^.*$", text) if text == pattern else None

    if match_type == "contains":
        return re.match(r"^.*$", text) if pattern in text else None

    try:
        return re.search(pattern, text)
    except re.error:
        return None


def script_entry_channel_allowed(entry: dict, channel_id: Optional[int]) -> bool:
    allowed = entry.get("channel_ids") or []
    if not allowed:
        return True
    if channel_id is None:
        return False
    try:
        return int(channel_id) in {int(cid) for cid in allowed}
    except (TypeError, ValueError):
        return False


def parse_script_channel_ids(raw: Optional[str], guild: discord.Guild) -> List[int]:
    if not raw:
        return []

    text = str(raw).strip()
    if not text or text.lower() in {"all", "*", "none", "clear", "reset"}:
        return []

    channel_ids: List[int] = []
    for token in [part.strip() for part in text.split(",") if part.strip()]:
        match = re.search(r"\d+", token)
        if not match:
            continue
        channel_id = int(match.group(0))
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        channel_ids.append(channel_id)

    deduped = []
    seen = set()
    for channel_id in channel_ids:
        if channel_id in seen:
            continue
        deduped.append(channel_id)
        seen.add(channel_id)
    return deduped


async def run_script_trigger(
    name: str,
    entry: dict,
    *,
    guild: discord.Guild,
    match: Optional[re.Match] = None,
    event_name: str = "message",
    message: Optional[discord.Message] = None,
    reaction: Optional[discord.Reaction] = None,
    user: Optional[discord.abc.User] = None,
    member: Optional[discord.Member] = None,
) -> bool:
    if not guild or not entry.get("enabled", True):
        return False

    code = str(entry.get("code") or "").strip()
    if not code:
        return False

    source_channel = message.channel if message else (reaction.message.channel if reaction else None)
    source_author = message.author if message else user
    source_content = message.content if message else ""
    logger.info(
        "Running script trigger '%s' event=%s guild=%s message_id=%s author_id=%s",
        name,
        event_name,
        guild.id if guild else "unknown",
        message.id if message else None,
        source_author.id if source_author else None,
    )
    referenced_message = None
    if message and message.reference:
        referenced_message = getattr(message.reference, "resolved", None)
        reference_message_id = message.reference.message_id or getattr(referenced_message, "id", None)
        if referenced_message is None and source_channel is not None and reference_message_id is not None:
            try:
                referenced_message = await source_channel.fetch_message(reference_message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                referenced_message = None

    async def _send_async(content: str, channel_id: Optional[int] = None):
        target = source_channel
        if channel_id:
            target = guild.get_channel(channel_id)
            if target is None:
                try:
                    target = await bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "Script trigger '%s' could not resolve send target channel_id=%s",
                        name,
                        channel_id,
                    )
                    return None
        if target is None:
            logger.warning("Script trigger '%s' send target is None", name)
            return None
        try:
            return await target.send(str(content))
        except discord.HTTPException:
            logger.exception(
                "Script trigger '%s' failed sending message to channel_id=%s",
                name,
                getattr(target, "id", None),
            )
            return None

    async def _reply_async(content: str):
        if not message:
            logger.warning("Script trigger '%s' reply helper called without message", name)
            return None
        try:
            return await message.reply(str(content), mention_author=False)
        except discord.HTTPException:
            logger.exception(
                "Script trigger '%s' failed replying to message_id=%s",
                name,
                message.id,
            )
            return None

    async def _react_async(emoji: str):
        if not message:
            logger.warning("Script trigger '%s' react helper called without message", name)
            return None
        try:
            return await message.add_reaction(emoji)
        except discord.HTTPException:
            logger.exception(
                "Script trigger '%s' failed reacting to message_id=%s emoji=%s",
                name,
                message.id,
                emoji,
            )
            return None

    async def _send_embed_async(
        title: Optional[str] = None,
        description: Optional[str] = None,
        *,
        color: Optional[int] = None,
        channel_id: Optional[int] = None,
        fields: Optional[List[Tuple[str, str, bool]]] = None,
        footer: Optional[str] = None,
    ):
        target = source_channel
        if channel_id:
            target = guild.get_channel(channel_id)
            if target is None:
                try:
                    target = await bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return None
        if target is None:
            return None
        embed_color = discord.Color(color) if isinstance(color, int) else None
        embed = discord.Embed(title=title or None, description=description or None, color=embed_color)
        if fields:
            for field_name, field_value, inline in fields:
                embed.add_field(name=str(field_name), value=str(field_value), inline=bool(inline))
        if footer:
            embed.set_footer(text=str(footer))
        try:
            return await target.send(embed=embed)
        except discord.HTTPException:
            logger.exception(
                "Script trigger '%s' failed sending embed to channel_id=%s",
                name,
                getattr(target, "id", None),
            )
            return None

    async def _dm_async(user_id: int, content: str):
        try:
            user = await bot.fetch_user(int(user_id))
        except (discord.NotFound, discord.HTTPException, ValueError):
            return None
        try:
            return await user.send(str(content))
        except discord.HTTPException:
            return None

    async def _edit_message_async(message_id: int, content: Optional[str] = None):
        if not source_channel:
            logger.warning("Script trigger '%s' edit helper called without source channel", name)
            return None
        try:
            target_message = await source_channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            logger.exception(
                "Script trigger '%s' failed fetching message_id=%s for edit",
                name,
                message_id,
            )
            return None
        try:
            return await target_message.edit(content=str(content) if content is not None else None)
        except discord.HTTPException:
            logger.exception(
                "Script trigger '%s' failed editing message_id=%s",
                name,
                message_id,
            )
            return None

    async def _delete_message_async(message_id: Optional[int] = None):
        if not source_channel:
            return None
        target_id = message_id or (message.id if message else None)
        if target_id is None:
            return None
        try:
            target_message = await source_channel.fetch_message(int(target_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            return None
        try:
            return await target_message.delete()
        except discord.HTTPException:
            return None

    async def _kick_member_async(member_id: int, reason: str = "No reason provided"):
        if not guild:
            return None
        try:
            member = guild.get_member(int(member_id))
        except (TypeError, ValueError):
            return None
        if not member:
            return None
        try:
            await member.kick(reason=str(reason))
            return member
        except discord.HTTPException:
            return None

    async def _ban_member_async(
        user_id: int,
        reason: str = "No reason provided",
        delete_message_seconds: int = 0,
    ):
        if not guild:
            return None

        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None

        target = guild.get_member(user_id_int)
        if target is None:
            try:
                target = await bot.fetch_user(user_id_int)
            except (discord.NotFound, discord.HTTPException):
                return None

        delete_seconds = max(0, min(int(delete_message_seconds), 604800))

        try:
            await guild.ban(
                target,
                reason=str(reason),
                delete_message_seconds=delete_seconds,
            )
            return target
        except (discord.HTTPException, ValueError):
            return None

    async def _unban_user_async(user_id: int, reason: str = "No reason provided"):
        if not guild:
            return None
        try:
            user = await bot.fetch_user(int(user_id))
        except (discord.NotFound, discord.HTTPException, ValueError):
            return None
        try:
            await guild.unban(user, reason=str(reason))
            return user
        except discord.HTTPException:
            return None

    async def _timeout_member_async(member_id: int, minutes: int, reason: str = "No reason provided"):
        if not guild:
            return None
        try:
            member = guild.get_member(int(member_id))
            minutes_int = int(minutes)
        except (TypeError, ValueError):
            return None
        if not member:
            return None

        if minutes_int <= 0:
            until = None
        else:
            until = datetime.utcnow() + timedelta(minutes=minutes_int)

        try:
            await member.timeout(until, reason=str(reason))
            return member
        except discord.HTTPException:
            return None

    async def _clear_messages_async(
        amount: int,
        from_user_id: Optional[int] = None,
        contains: Optional[str] = None,
        starts_after: Optional[int] = None,
        ends_before: Optional[int] = None,
        include_bots: bool = True,
        only_bots: bool = False,
        attachments_only: bool = False,
        role_id: Optional[int] = None,
        scan_limit: Optional[int] = None,
    ):
        if not guild or not source_channel:
            return []

        try:
            amount_int = int(amount)
        except (TypeError, ValueError):
            return []
        if amount_int <= 0:
            return []

        before_msg = None
        after_msg = None

        try:
            if ends_before is not None:
                before_msg = await source_channel.fetch_message(int(ends_before))
            if starts_after is not None:
                after_msg = await source_channel.fetch_message(int(starts_after))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
            return []

        if scan_limit is None:
            scan_limit_int = min(max(amount_int * 10, amount_int), 5000)
        else:
            try:
                scan_limit_int = max(1, min(int(scan_limit), 5000))
            except (TypeError, ValueError):
                scan_limit_int = min(max(amount_int * 10, amount_int), 5000)

        contains_lower = str(contains).lower() if contains is not None else None
        target_user_id = int(from_user_id) if from_user_id is not None else None
        role = guild.get_role(int(role_id)) if role_id is not None else None
        counter = {"n": 0}

        def check(m: discord.Message) -> bool:
            if m.pinned:
                return False
            if only_bots and not m.author.bot:
                return False
            if not only_bots and not include_bots and m.author.bot:
                return False
            if target_user_id is not None and m.author.id != target_user_id:
                return False
            if role:
                member = guild.get_member(m.author.id)
                if not member or role not in member.roles:
                    return False
            if attachments_only and len(m.attachments) == 0:
                return False
            if contains_lower and contains_lower not in (m.content or "").lower():
                return False
            if counter["n"] >= amount_int:
                return False
            counter["n"] += 1
            return True

        try:
            return await source_channel.purge(
                limit=scan_limit_int,
                check=check,
                before=before_msg,
                after=after_msg,
                bulk=True,
            )
        except discord.HTTPException:
            return []

    async def _search_messages_async(
        query: Optional[str] = None,
        *,
        limit: int = 50,
        from_user_id: Optional[int] = None,
        include_bots: bool = True,
        attachments_only: bool = False,
    ) -> List[dict]:
        if not guild or not source_channel:
            return []

        try:
            limit_int = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit_int = 50

        query_lower = str(query).lower() if query else None
        target_user_id = int(from_user_id) if from_user_id is not None else None
        results: List[dict] = []

        try:
            async for item in source_channel.history(limit=limit_int):
                if target_user_id is not None and item.author.id != target_user_id:
                    continue
                if not include_bots and item.author.bot:
                    continue
                if attachments_only and not item.attachments:
                    continue
                if query_lower and query_lower not in (item.content or "").lower():
                    continue

                results.append(
                    {
                        "id": item.id,
                        "author_id": item.author.id,
                        "author_name": str(item.author),
                        "content": item.content,
                        "created_at": item.created_at.isoformat(),
                        "jump_url": item.jump_url,
                        "attachment_count": len(item.attachments),
                    }
                )
        except discord.HTTPException:
            return results

        return results

    async def _set_slowmode_async(seconds: int, reason: str = "No reason provided"):
        try:
            seconds_int = max(0, min(int(seconds), 21600))
        except (TypeError, ValueError):
            return None
        try:
            if not source_channel:
                return None
            await source_channel.edit(slowmode_delay=seconds_int, reason=str(reason))
            return seconds_int
        except discord.HTTPException:
            return None

    async def _add_role_async(member_id: int, role_id: int, reason: str = "No reason provided"):
        try:
            target_member = guild.get_member(int(member_id))
            target_role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            return None
        if not target_member or not target_role:
            return None
        try:
            await target_member.add_roles(target_role, reason=str(reason))
            return target_member
        except discord.HTTPException:
            return None

    async def _remove_role_async(member_id: int, role_id: int, reason: str = "No reason provided"):
        try:
            target_member = guild.get_member(int(member_id))
            target_role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            return None
        if not target_member or not target_role:
            return None
        try:
            await target_member.remove_roles(target_role, reason=str(reason))
            return target_member
        except discord.HTTPException:
            return None

    async def _http_request_async(
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        body: Optional[str] = None,
        json_body=None,
        timeout: int = 15,
    ):
        target_url = str(url or "").strip()
        if not target_url:
            return {"ok": False, "status": 0, "text": "", "json": None, "headers": {}, "error": "Missing URL"}

        request_headers = {}
        if isinstance(headers, dict):
            request_headers = {str(key): str(value) for key, value in headers.items()}
        request_headers.setdefault("User-Agent", "SmokeBot/1.0 (+https://github.com/SmokeSlate/discordBots)")
        request_headers.setdefault("Accept", "application/json,text/plain,*/*")

        payload_bytes = None
        if json_body is not None:
            request_headers.setdefault("Content-Type", "application/json")
            payload_bytes = json.dumps(json_body).encode("utf-8")
        elif body is not None:
            payload_bytes = str(body).encode("utf-8")

        try:
            timeout_seconds = max(1, int(timeout))
        except (TypeError, ValueError):
            timeout_seconds = 15

        def _run_request():
            request = urllib.request.Request(
                target_url,
                data=payload_bytes,
                headers=request_headers,
                method=str(method or "GET").upper(),
            )
            response_text = ""
            response_status = 0
            response_headers = {}

            try:
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    response_status = getattr(response, "status", None) or response.getcode() or 0
                    response_headers = dict(response.headers.items())
                    response_text = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                response_status = exc.code or 0
                response_headers = dict(exc.headers.items()) if exc.headers else {}
                response_text = exc.read().decode("utf-8", errors="replace")
            except Exception as exc:
                return {
                    "ok": False,
                    "status": 0,
                    "text": "",
                    "json": None,
                    "headers": {},
                    "error": str(exc),
                }

            parsed_json = None
            if response_text:
                try:
                    parsed_json = json.loads(response_text)
                except json.JSONDecodeError:
                    parsed_json = None

            return {
                "ok": 200 <= response_status < 300,
                "status": response_status,
                "text": response_text,
                "json": parsed_json,
                "headers": response_headers,
            }

        return await asyncio.to_thread(_run_request)

    def _schedule(coro):
        return asyncio.create_task(coro)

    def send(content: str, channel_id: Optional[int] = None):
        return _schedule(_send_async(content, channel_id))

    def reply(content: str):
        return _schedule(_reply_async(content))

    def react(emoji: str):
        return _schedule(_react_async(emoji))

    def send_embed(
        title: Optional[str] = None,
        description: Optional[str] = None,
        *,
        color: Optional[int] = None,
        channel_id: Optional[int] = None,
        fields: Optional[List[Tuple[str, str, bool]]] = None,
        footer: Optional[str] = None,
    ):
        return _schedule(_send_embed_async(
            title,
            description,
            color=color,
            channel_id=channel_id,
            fields=fields,
            footer=footer,
        ))

    def dm(user_id: int, content: str):
        return _schedule(_dm_async(user_id, content))

    def edit_message(message_id: int, content: Optional[str] = None):
        return _schedule(_edit_message_async(message_id, content))

    def delete_message(message_id: Optional[int] = None):
        return _schedule(_delete_message_async(message_id))

    def kick_member(member_id: int, reason: str = "No reason provided"):
        return _schedule(_kick_member_async(member_id, reason))

    def ban_member(
        user_id: int,
        reason: str = "No reason provided",
        delete_message_seconds: int = 0,
    ):
        return _schedule(_ban_member_async(user_id, reason, delete_message_seconds))

    def unban_user(user_id: int, reason: str = "No reason provided"):
        return _schedule(_unban_user_async(user_id, reason))

    def timeout_member(member_id: int, minutes: int, reason: str = "No reason provided"):
        return _schedule(_timeout_member_async(member_id, minutes, reason))

    def clear_messages(
        amount: int,
        from_user_id: Optional[int] = None,
        contains: Optional[str] = None,
        starts_after: Optional[int] = None,
        ends_before: Optional[int] = None,
        include_bots: bool = True,
        only_bots: bool = False,
        attachments_only: bool = False,
        role_id: Optional[int] = None,
        scan_limit: Optional[int] = None,
    ):
        return _schedule(
            _clear_messages_async(
                amount,
                from_user_id=from_user_id,
                contains=contains,
                starts_after=starts_after,
                ends_before=ends_before,
                include_bots=include_bots,
                only_bots=only_bots,
                attachments_only=attachments_only,
                role_id=role_id,
                scan_limit=scan_limit,
            )
        )

    def search_messages(
        query: Optional[str] = None,
        *,
        limit: int = 50,
        from_user_id: Optional[int] = None,
        include_bots: bool = True,
        attachments_only: bool = False,
    ):
        return _schedule(
            _search_messages_async(
                query,
                limit=limit,
                from_user_id=from_user_id,
                include_bots=include_bots,
                attachments_only=attachments_only,
            )
        )

    def set_slowmode(seconds: int, reason: str = "No reason provided"):
        return _schedule(_set_slowmode_async(seconds, reason))

    def add_role(member_id: int, role_id: int, reason: str = "No reason provided"):
        return _schedule(_add_role_async(member_id, role_id, reason))

    def remove_role(member_id: int, role_id: int, reason: str = "No reason provided"):
        return _schedule(_remove_role_async(member_id, role_id, reason))

    def http_request(
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        body: Optional[str] = None,
        json_body=None,
        timeout: int = 15,
    ):
        return _schedule(_http_request_async(url, method, headers, body, json_body, timeout))

    safe_builtins = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "len": len,
        "range": range,
        "min": min,
        "max": max,
        "sum": sum,
        "sorted": sorted,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "print": print,
    }

    trusted_actor_id = source_author.id if source_author else None
    is_trusted_user = trusted_actor_id == TRUSTED_SCRIPT_USER_ID
    builtins_scope = builtins.__dict__ if is_trusted_user else safe_builtins

    globals_dict = {
        "__builtins__": builtins_scope,
        "send": send,
        "reply": reply,
        "react": react,
        "send_embed": send_embed,
        "dm": dm,
        "edit_message": edit_message,
        "delete_message": delete_message,
        "kick_member": kick_member,
        "ban_member": ban_member,
        "unban_user": unban_user,
        "timeout_member": timeout_member,
        "clear_messages": clear_messages,
        "search_messages": search_messages,
        "set_slowmode": set_slowmode,
        "add_role": add_role,
        "remove_role": remove_role,
        "http_request": http_request,
        "message": message,
        "referenced_message": referenced_message,
        "author": source_author,
        "channel": source_channel,
        "guild": guild,
        "content": source_content,
        "reaction": reaction,
        "user": user,
        "member": member,
        "event": event_name,
        "match": match,
        "random": random,
        "re": re,
        "asyncio": asyncio,
    }

    if is_trusted_user:
        globals_dict.update(
            {
                "bot": bot,
                "discord": discord,
                "os": os,
                "datetime": datetime,
                "timedelta": timedelta,
            }
        )

    try:
        # Use one shared namespace so top-level defs remain visible to
        # async tasks/functions created by user scripts.
        exec(code, globals_dict, globals_dict)
        script_entry = globals_dict.get("__script_async_entry__")
        if script_entry is not None:
            if asyncio.iscoroutine(script_entry) or isinstance(script_entry, asyncio.Future):
                await script_entry
            elif callable(script_entry):
                result = script_entry()
                if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                    await result
        logger.info(
            "Completed script trigger '%s' event=%s guild=%s message_id=%s",
            name,
            event_name,
            guild.id if guild else "unknown",
            message.id if message else None,
        )
    except Exception as exc:
        logger.exception("Error running script trigger '%s' in guild %s", name, guild.id)
        try:
            error_text = f"⚠️ Script error in `{name}`: {exc}"
            if source_channel:
                await source_channel.send(error_text[:1900])
        except discord.HTTPException:
            pass
        return False

    return True

# =====================================================
# Ticket Categories (Customizable)
# =====================================================

def default_ticket_categories():
    return {
        "tech_support": {
            "label": "🛠️ Technical Support",
            "desc": "Get help with technical issues",
            "emoji": "🛠️"
        },
        "general_question": {
            "label": "❓ General Questions",
            "desc": "Ask general questions",
            "emoji": "❓"
        },
        "report_issue": {
            "label": "🚨 Report Issue",
            "desc": "Report a problem or bug",
            "emoji": "🚨"
        },
        "feature_request": {
            "label": "💡 Feature Request",
            "desc": "Suggest a new feature",
            "emoji": "💡"
        },
        "staff_application": {
            "label": "👥 Staff Application",
            "desc": "Apply to join the staff team",
            "emoji": "👥"
        },
        "other": {
            "label": "📋 Other",
            "desc": "Something else not listed above",
            "emoji": "📋"
        }
    }

ticket_categories = read_json("ticket_categories.json", default_ticket_categories)

def save_ticket_categories(categories):
    write_json("ticket_categories.json", categories)


async def perform_auto_update_check():
    """Fetch remote updates and restart the process after a successful update."""
    async with auto_update_lock:
        status = await asyncio.to_thread(
            get_git_update_status,
            AUTO_UPDATE_REPO_DIR,
            AUTO_UPDATE_REMOTE,
            AUTO_UPDATE_BRANCH,
        )

        if not status.get("ok"):
            reason = status.get("reason", "unknown")
            details = status.get("details", "no details")
            print(f"[auto-update] status check failed ({reason}): {details}")
            return

        if status.get("up_to_date", True):
            print("[auto-update] no updates available")
            return

        print(
            "[auto-update] update available "
            f"{status.get('local_sha', 'unknown')} -> {status.get('remote_sha', 'unknown')}"
        )
        update_result = await asyncio.to_thread(
            apply_git_update,
            AUTO_UPDATE_REPO_DIR,
            AUTO_UPDATE_REMOTE,
            AUTO_UPDATE_BRANCH,
        )
        if not update_result.get("ok"):
            print(
                "[auto-update] failed to apply update: "
                f"{update_result.get('stderr') or update_result.get('stdout') or 'unknown error'}"
            )
            return

        print("[auto-update] update applied successfully, restarting bot process")
        os.execv(sys.executable, [sys.executable, *sys.argv])


async def auto_update_loop():
    """Periodically poll for git updates and restart when an update is applied."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await perform_auto_update_check()
        except Exception as exc:
            print(f"[auto-update] unexpected error: {exc}")
        await asyncio.sleep(AUTO_UPDATE_INTERVAL_SECONDS)


def initialize_runtime_state():
    migration_summary = migrate_legacy_json_files()
    if migration_summary["migrated"]:
        print(
            "[storage] migrated legacy JSON files into SQLite: "
            + ", ".join(migration_summary["migrated"])
        )
    for path, error in migration_summary["errors"].items():
        print(f"[storage] failed to migrate {path}: {error}")

    load_reaction_roles()
    load_ticket_data()
    load_snippets()
    load_auto_replies()
    load_giveaways()
    load_script_triggers()

# =====================================================
# Ready Event (register persistent views, sync commands)
# =====================================================

@bot.event
async def on_ready():
    global auto_update_task
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')

    for message_id, data in list(giveaways.items()):
        if data.get("ended"):
            continue
        try:
            bot.add_view(GiveawayJoinView(message_id))
        except Exception as e:
            print(f"Failed to add giveaway view for {message_id}: {e}")
        asyncio.create_task(schedule_giveaway_end(message_id))

    # Persistent UI so dropdown/buttons survive restarts
    try:
        bot.add_view(TicketMenuView())     # persistent view (timeout=None)
        bot.add_view(TicketControlView())  # persistent ticket controls
    except Exception as e:
        print(f"Failed to add persistent views: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    if AUTO_UPDATE_ENABLED and auto_update_task is None:
        auto_update_task = asyncio.create_task(auto_update_loop())
        print(
            "[auto-update] enabled "
            f"(interval={AUTO_UPDATE_INTERVAL_SECONDS}s, remote={AUTO_UPDATE_REMOTE}, branch={AUTO_UPDATE_BRANCH})"
        )
    elif not AUTO_UPDATE_ENABLED:
        print("[auto-update] disabled (set SMOKEBOT_AUTO_UPDATE=true to enable)")

# =====================================================
# Ticket System (dynamic categories + ephemeral confirmations)
# =====================================================

class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = []
        for key, data in ticket_categories.items():
            try:
                options.append(discord.SelectOption(
                    label=data.get("label", key),
                    description=(data.get("desc") or "")[:100],
                    emoji=data.get("emoji"),
                    value=key
                ))
            except Exception:
                # Fallback if emoji invalid etc.
                options.append(discord.SelectOption(
                    label=data.get("label", key),
                    description=(data.get("desc") or "")[:100],
                    value=key
                ))

        super().__init__(
            placeholder="Choose a ticket category...",
            options=options,
            min_values=1,
            max_values=1,
            custom_id="ticket_category_select_v1"  # persistent
        )

    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        cat = ticket_categories.get(selected_category)
        category_display = cat["label"] if cat else selected_category

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        # Prevent duplicate open tickets
        if guild_id in ticket_data:
            for thread_id, data in ticket_data[guild_id].items():
                if data['user_id'] == user_id and data['status'] == 'open':
                    thread = interaction.guild.get_thread(int(thread_id))
                    if thread:
                        return await interaction.response.send_message(
                            f"❌ You already have an open ticket: {thread.mention}",
                            ephemeral=True
                        )

        try:
            thread_name = f"{category_display} - {interaction.user.display_name}"
            thread = await interaction.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                reason=f"Ticket created by {interaction.user}"
            )
            await thread.add_user(interaction.user)

            if guild_id not in ticket_data:
                ticket_data[guild_id] = {}
            ticket_data[guild_id][str(thread.id)] = {
                'user_id': user_id,
                'category': selected_category,
                'status': 'open',
                'created_at': datetime.utcnow().isoformat(),
                'channel_id': str(interaction.channel.id)
            }
            save_ticket_data()

            embed = discord.Embed(
                title=f"🎫 New Ticket - {category_display}",
                description=(
                    f"Hello {interaction.user.mention}! Thank you for creating a ticket.\n\n"
                    f"**Category:** {category_display}\n"
                    f"**Status:** Open\n\n"
                    f"Please describe your issue or question in detail. A staff member will assist you shortly!"
                ),
                color=discord.Color.green()
            )
            view = TicketControlView()
            await thread.send(embed=embed, view=view)

            # Ephemeral confirmation only — prevents public "used /ticket" message
            await interaction.response.send_message(
                f"✅ Your ticket has been created: {thread.mention}",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to create threads!",
                ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Failed to create ticket: {e}",
                ephemeral=True
            )

class TicketMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        thread_id = str(interaction.channel.id)

        if guild_id not in ticket_data or thread_id not in ticket_data[guild_id]:
            return await interaction.response.send_message("❌ Ticket data not found!", ephemeral=True)

        info = ticket_data[guild_id][thread_id]
        is_ticket_owner = str(interaction.user.id) == info['user_id']
        is_staff = has_mod_permissions_or_override(interaction)
        if not (is_ticket_owner or is_staff):
            return await interaction.response.send_message("❌ You don't have permission to close this ticket!", ephemeral=True)

        info['status'] = 'closed'
        info['closed_at'] = datetime.utcnow().isoformat()
        info['closed_by'] = str(interaction.user.id)
        save_ticket_data()

        embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=f"This ticket has been closed by {interaction.user.mention}.\n"
                        f"Closed at: <t:{int(datetime.utcnow().timestamp())}:f>",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

        await asyncio.sleep(5)
        try:
            await interaction.channel.edit(archived=True, locked=True)
        except discord.Forbidden:
            pass

    @discord.ui.button(label="📋 Add Note", style=discord.ButtonStyle.secondary, custom_id="add_note")
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_mod_permissions_or_override(interaction):
            return await interaction.response.send_message("❌ Only staff can add notes to tickets!", ephemeral=True)
        await interaction.response.send_modal(TicketNoteModal())

class TicketNoteModal(discord.ui.Modal, title="Add Ticket Note"):
    note = discord.ui.TextInput(
        label="Note",
        placeholder="Enter your note here...",
        style=discord.TextStyle.paragraph,
        max_length=2000
    )
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📋 Staff Note",
            description=self.note.value,
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Added by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ticket", description="Create a ticket menu")
async def create_ticket_menu(interaction: discord.Interaction):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

    # Defer ephemerally so no public 'used /ticket' message appears
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Need help? Create a support ticket by selecting a category below!\n\n"
                    "Your ticket will be created as a **private thread** that only you and staff can see.",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Select a category from the dropdown menu below")

    view = TicketMenuView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.followup.send("✅ Ticket menu posted.", ephemeral=True)

@bot.tree.command(name="ticketstats", description="View ticket statistics")
async def ticket_stats(interaction: discord.Interaction):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

    guild_id = str(interaction.guild.id)
    if guild_id not in ticket_data or not ticket_data[guild_id]:
        return await interaction.response.send_message("❌ No ticket data found for this server!", ephemeral=True)

    tickets = ticket_data[guild_id]
    total_tickets = len(tickets)
    open_tickets = sum(1 for t in tickets.values() if t['status'] == 'open')
    closed_tickets = sum(1 for t in tickets.values() if t['status'] == 'closed')

    # Count by category
    categories_count = {}
    for t in tickets.values():
        cat_key = t['category']
        categories_count[cat_key] = categories_count.get(cat_key, 0) + 1

    category_text = ""
    for cat_key, count in categories_count.items():
        label = ticket_categories.get(cat_key, {}).get("label", cat_key)
        category_text += f"{label}: {count}\n"

    embed = discord.Embed(title="🎫 Ticket Statistics", color=discord.Color.blue())
    embed.add_field(name="📊 Overview", value=f"**Total Tickets:** {total_tickets}\n**Open:** {open_tickets}\n**Closed:** {closed_tickets}", inline=False)
    if category_text:
        embed.add_field(name="📋 By Category", value=category_text, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="listtickets", description="List all open tickets")
async def list_tickets(interaction: discord.Interaction):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

    guild_id = str(interaction.guild.id)
    if guild_id not in ticket_data:
        return await interaction.response.send_message("❌ No tickets found for this server!", ephemeral=True)

    open_tickets = []
    for thread_id, data in ticket_data[guild_id].items():
        if data['status'] == 'open':
            thread = interaction.guild.get_thread(int(thread_id))
            if thread:
                user = bot.get_user(int(data['user_id']))
                category_label = ticket_categories.get(data['category'], {}).get("label", data['category'])
                user_name = user.display_name if user else "Unknown User"
                try:
                    dt = datetime.fromisoformat(data['created_at'])
                except Exception:
                    dt = datetime.utcnow()
                created_ts = int(dt.timestamp())
                open_tickets.append(f"{thread.mention} - {category_label}\n👤 {user_name} • <t:{created_ts}:R>")

    if not open_tickets:
        return await interaction.response.send_message("✅ No open tickets found!", ephemeral=True)

    embed = discord.Embed(
        title="🎫 Open Tickets",
        description="\n\n".join(open_tickets[:10]),
        color=discord.Color.green()
    )
    if len(open_tickets) > 10:
        embed.set_footer(text=f"Showing 10 of {len(open_tickets)} open tickets")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Category management
@bot.tree.command(name="addticketcategory", description="Add a new ticket category")
@app_commands.describe(
    key="Unique ID for category (no spaces)",
    label="Display name (can include emoji prefix)",
    description="Short description",
    emoji="Emoji character (optional, like 🛠️)"
)
async def add_ticket_category(interaction: discord.Interaction, key: str, label: str, description: str, emoji: Optional[str] = None):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    ticket_categories[key] = {"label": label, "desc": description, "emoji": emoji or ""}
    save_ticket_categories(ticket_categories)
    await interaction.response.send_message(f"✅ Added category `{label}` (`{key}`)", ephemeral=True)

@bot.tree.command(name="removeticketcategory", description="Remove a ticket category")
@app_commands.describe(key="The category key to remove")
async def remove_ticket_category(interaction: discord.Interaction, key: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    if ticket_categories.pop(key, None):
        save_ticket_categories(ticket_categories)
        await interaction.response.send_message(f"✅ Removed category `{key}`", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Category not found.", ephemeral=True)

@bot.tree.command(name="listticketcategories", description="List all ticket categories")
async def list_ticket_categories(interaction: discord.Interaction):
    if not ticket_categories:
        return await interaction.response.send_message("No categories set.", ephemeral=True)
    embed = discord.Embed(title="🎫 Ticket Categories", color=discord.Color.blue())
    for k, v in ticket_categories.items():
        name = f"{(v.get('emoji')+' ') if v.get('emoji') else ''}{v.get('label', k)} (`{k}`)"
        embed.add_field(name=name, value=v.get("desc", ""), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =====================================================
# Giveaway Command
# =====================================================

@bot.tree.command(name="giveaway", description="Start a giveaway with optional role restrictions")
@app_commands.describe(
    prize="Prize being given away",
    duration_minutes="Duration of the giveaway in minutes",
    winner_count="Number of winners to draw",
    role="Role required to participate (optional)",
    channel="Channel to post the giveaway message in",
    details="Additional details shown in the giveaway embed"
)
async def start_giveaway(
    interaction: discord.Interaction,
    prize: str,
    duration_minutes: app_commands.Range[int, 1, 10080],
    winner_count: app_commands.Range[int, 1, 50],
    role: Optional[discord.Role] = None,
    channel: Optional[discord.TextChannel] = None,
    details: Optional[str] = None
):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to start giveaways.", ephemeral=True)

    if channel and channel.guild.id != interaction.guild.id:
        return await interaction.response.send_message("❌ Please choose a channel from this server.", ephemeral=True)

    description_text = details.strip() if details and details.strip() else None

    await interaction.response.defer(ephemeral=True)

    target_channel = channel or interaction.channel
    if target_channel is None:
        return await interaction.followup.send("❌ Unable to determine the channel to post the giveaway.", ephemeral=True)

    end_time = datetime.utcnow() + timedelta(minutes=duration_minutes)

    data = {
        "guild_id": str(interaction.guild.id),
        "channel_id": str(target_channel.id),
        "host_id": str(interaction.user.id),
        "prize": prize,
        "end_time": end_time.isoformat(),
        "winner_count": int(winner_count),
        "required_role_id": str(role.id) if role else None,
        "description": description_text,
        "participants": [],
        "ended": False
    }

    embed = build_giveaway_embed(data)

    try:
        message = await target_channel.send(embed=embed)
    except discord.Forbidden:
        return await interaction.followup.send("❌ I don't have permission to send messages in that channel.", ephemeral=True)
    except discord.HTTPException as e:
        return await interaction.followup.send(f"❌ Failed to create giveaway: {e}", ephemeral=True)

    giveaway_id = str(message.id)
    data["message_id"] = giveaway_id
    giveaways[giveaway_id] = data
    save_giveaways()

    view = GiveawayJoinView(giveaway_id)
    try:
        await message.edit(view=view)
    except discord.HTTPException:
        pass
    else:
        bot.add_view(view)

    asyncio.create_task(schedule_giveaway_end(giveaway_id))

    end_ts = int(end_time.timestamp())
    await interaction.followup.send(
        f"✅ Giveaway posted in {target_channel.mention}! Ends <t:{end_ts}:R>.",
        ephemeral=True
    )

# =====================================================
# Snippet Commands (with migration + dynamic placeholders)
# =====================================================

class SnippetEditModal(discord.ui.Modal):
    """Modal that allows editing snippet content with multiline support."""

    def __init__(
        self,
        *,
        guild_id: str,
        trigger: str,
        initial_content: str = "",
        dynamic: Optional[bool] = None,
        existed: bool = True,
    ) -> None:
        title = f"Snippet: !{trigger}" if trigger else "Snippet Editor"
        super().__init__(title=title[:45])  # Discord limits modal titles to 45 chars

        self.guild_id = guild_id
        self.trigger = trigger
        self.dynamic = dynamic
        self.existed = existed

        self.content_input = discord.ui.TextInput(
            label="Snippet Content",
            style=discord.TextStyle.paragraph,
            default=initial_content[:1900],  # stay well under 2000 char limit
            max_length=1900,
            placeholder="Enter the snippet text. Supports new lines.",
            required=True,
        )

        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        content = str(self.content_input.value).strip()

        if not content:
            return await interaction.response.send_message(
                "❌ Snippet content cannot be empty.", ephemeral=True
            )

        guild_snippets = snippets.setdefault(self.guild_id, {})
        existing_entry = ensure_snippet_defaults(guild_snippets.get(self.trigger, {}))
        dynamic_flag = (
            existing_entry.get("dynamic", False)
            if self.dynamic is None
            else bool(self.dynamic)
        )

        existing_entry["content"] = content
        existing_entry["dynamic"] = dynamic_flag
        guild_snippets[self.trigger] = ensure_snippet_defaults(existing_entry)
        save_snippets()

        action = "updated" if self.existed else "created"
        await interaction.response.send_message(
            f"✅ Snippet `!{self.trigger}` {action}.", ephemeral=True
        )


@bot.tree.command(name="addsnippet", description="Add a static snippet")
@app_commands.describe(trigger="Trigger word (no !)", content="Content to send")
async def add_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    entry = ensure_snippet_defaults({"content": content, "dynamic": False})
    snippets.setdefault(gid, {})[trigger] = entry
    save_snippets()
    await interaction.response.send_message(f"✅ Static snippet `!{trigger}` added.", ephemeral=True)

@bot.tree.command(name="adddynamicsnippet", description="Add a dynamic snippet with placeholders {1}, {2}...")
@app_commands.describe(trigger="Trigger word (no !)", content="Content with placeholders like {1}, {2}, ...")
async def add_dynamic_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    entry = ensure_snippet_defaults({"content": content, "dynamic": True})
    snippets.setdefault(gid, {})[trigger] = entry
    save_snippets()
    await interaction.response.send_message(f"✅ Dynamic snippet `!{trigger}` added.", ephemeral=True)

@bot.tree.command(name="editdynamicsnippet", description="Edit a dynamic snippet (or toggle dynamic mode)")
@app_commands.describe(trigger="Trigger word (no !)", content="New content", dynamic="True/False for dynamic mode")
async def edit_dynamic_snippet(interaction: discord.Interaction, trigger: str, content: str, dynamic: Optional[bool] = True):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    if gid in snippets and trigger in snippets[gid]:
        entry = ensure_snippet_defaults(snippets[gid][trigger])
        entry["content"] = content
        entry["dynamic"] = bool(dynamic)
        snippets[gid][trigger] = entry
        save_snippets()
        return await interaction.response.send_message(f"✅ Dynamic snippet `!{trigger}` updated.", ephemeral=True)
    await interaction.response.send_message("❌ Snippet not found.", ephemeral=True)


@bot.tree.command(
    name="editsnippet",
    description="Open the default form to edit snippet content with multiline support",
)
@app_commands.describe(
    trigger="Trigger word (no !)",
    dynamic="Optional override for dynamic mode (leave blank to keep current)",
)
async def edit_snippet_form(
    interaction: discord.Interaction, trigger: str, dynamic: Optional[bool] = None
):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    existing = snippets.get(gid, {}).get(trigger)

    if existing:
        initial_content = existing.get("content", "")
        default_dynamic = existing.get("dynamic", False)
        existed = True
    else:
        initial_content = ""
        default_dynamic = False
        existed = False

    modal = SnippetEditModal(
        guild_id=gid,
        trigger=trigger,
        initial_content=initial_content,
        dynamic=dynamic if dynamic is not None else default_dynamic,
        existed=existed,
    )

    await interaction.response.send_modal(modal)


@bot.tree.command(name="removesnippet", description="Remove a static snippet")
@app_commands.describe(trigger="Trigger word (no !)")
async def remove_snippet(interaction: discord.Interaction, trigger: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    if gid in snippets and trigger in snippets[gid] and not snippets[gid][trigger].get("dynamic"):
        del snippets[gid][trigger]
        save_snippets()
        return await interaction.response.send_message(f"✅ Snippet `!{trigger}` removed.", ephemeral=True)
    await interaction.response.send_message("❌ Snippet not found.", ephemeral=True)

@bot.tree.command(name="removedynamicsnippet", description="Remove a dynamic snippet")
@app_commands.describe(trigger="Trigger word (no !)")
async def remove_dynamic_snippet(interaction: discord.Interaction, trigger: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    if gid in snippets and trigger in snippets[gid] and snippets[gid][trigger].get("dynamic"):
        del snippets[gid][trigger]
        save_snippets()
        return await interaction.response.send_message(f"✅ Dynamic snippet `!{trigger}` removed.", ephemeral=True)
    await interaction.response.send_message("❌ Snippet not found.", ephemeral=True)

@bot.tree.command(name="listsnippets", description="List snippets for this server")
async def list_snippets(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    if gid not in snippets or not snippets[gid]:
        return await interaction.response.send_message("No snippets found.", ephemeral=True)
    embed = discord.Embed(title="📝 Snippets", color=discord.Color.green())
    for trig, data in snippets[gid].items():
        label = "(Dynamic)" if data.get("dynamic") else "(Static)"
        preview = data["content"][:50] + "..." if len(data["content"]) > 50 else data["content"]
        embed.add_field(name=f"!{trig} {label}", value=preview or "-", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =====================================================
# Auto Reply Commands
# =====================================================


autoreply_group = app_commands.Group(
    name="autoreply",
    description="Manage auto replies",
)


@autoreply_group.command(name="set", description="Create or update an auto reply")
@app_commands.describe(
    name="Name of the auto reply",
    pattern="Text to match (regex or substring)",
    response="Message to send when the pattern matches",
    dynamic="Treat capture groups as {1}, {2}, ... in the response",
    snippet="Name of a snippet to send instead of a text response",
    case_sensitive="For contains mode, require matching case",
)
@app_commands.choices(
    match_type=[
        app_commands.Choice(name="Regex", value="regex"),
        app_commands.Choice(name="Contains", value="contains"),
    ]
)
async def set_autoreply(
    interaction: discord.Interaction,
    name: str,
    pattern: str,
    response: Optional[str] = None,
    dynamic: Optional[bool] = None,
    match_type: Optional[app_commands.Choice[str]] = None,
    snippet: Optional[str] = None,
    case_sensitive: Optional[bool] = None,
):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Auto reply name cannot be empty.", ephemeral=True)

    pattern = pattern.strip()
    if not pattern:
        return await interaction.response.send_message("❌ Pattern cannot be empty.", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_replies = auto_replies.setdefault(gid, {})
    existed = name in guild_replies
    entry = ensure_autoreply_defaults(guild_replies.get(name, {}))

    selected_match_type = match_type.value if match_type else str(entry.get("match_type") or "regex")
    if selected_match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            return await interaction.response.send_message(f"❌ Invalid regex: {exc}", ephemeral=True)
    elif selected_match_type != "contains":
        selected_match_type = "regex"

    snippet_to_assign = None
    if snippet is not None:
        snippet_text = snippet.strip()
        if not snippet_text or snippet_text.lower() in {"none", "clear", "reset", "off"}:
            snippet_to_assign = ""
        else:
            snippet_name = snippet_text.lstrip("!")
            guild_snippets = snippets.get(gid, {})
            if snippet_name not in guild_snippets:
                return await interaction.response.send_message(
                    "❌ Snippet not found in this server.", ephemeral=True
                )
            snippet_to_assign = snippet_name

    if response is None and not existed and not snippet_to_assign:
        return await interaction.response.send_message(
            "❌ Provide a response or choose a snippet to send.", ephemeral=True
        )

    if response is not None:
        if not response.strip() and not snippet_to_assign:
            return await interaction.response.send_message(
                "❌ Response cannot be empty unless a snippet is used.",
                ephemeral=True,
            )
        entry["response"] = response

    if snippet_to_assign is not None:
        entry["snippet"] = snippet_to_assign

    entry["pattern"] = pattern
    entry["dynamic"] = entry.get("dynamic", False) if dynamic is None else bool(dynamic)
    entry["match_type"] = selected_match_type
    if selected_match_type == "contains":
        if case_sensitive is None:
            entry["case_sensitive"] = bool(entry.get("case_sensitive"))
        else:
            entry["case_sensitive"] = bool(case_sensitive)
    else:
        entry["case_sensitive"] = False

    guild_replies[name] = ensure_autoreply_defaults(entry)
    save_auto_replies()

    action = "updated" if existed else "created"
    await interaction.response.send_message(f"✅ Auto reply `{name}` {action}.", ephemeral=True)


@autoreply_group.command(name="remove", description="Delete an auto reply")
@app_commands.describe(name="Name of the auto reply to remove")
async def remove_autoreply(interaction: discord.Interaction, name: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Auto reply name cannot be empty.", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_replies = auto_replies.get(gid, {})
    if name in guild_replies:
        del guild_replies[name]
        save_auto_replies()
        return await interaction.response.send_message(f"✅ Auto reply `{name}` removed.", ephemeral=True)

    await interaction.response.send_message("❌ Auto reply not found.", ephemeral=True)


@autoreply_group.command(name="list", description="List auto replies for this server")
async def list_autoreplies(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    guild_replies = auto_replies.get(gid)

    if not guild_replies:
        return await interaction.response.send_message("No auto replies configured.", ephemeral=True)

    embed = discord.Embed(title="🤖 Auto Replies", color=discord.Color.blurple())
    for name, entry in guild_replies.items():
        pattern = entry.get("pattern") or "(no pattern)"
        dynamic_flag = "Dynamic" if entry.get("dynamic") else "Static"
        cooldown_seconds = int(entry.get("cooldown_seconds") or 0)
        scope = entry.get("cooldown_scope", "guild")
        match_type = str(entry.get("match_type") or "regex").title()
        snippet_name = entry.get("snippet") or ""
        case_sensitive = bool(entry.get("case_sensitive"))
        summary = [f"Pattern: `{pattern}`", f"Match: {match_type}", dynamic_flag]
        if cooldown_seconds:
            summary.append(f"Cooldown: {format_duration(cooldown_seconds)} ({scope})")
        include_roles = entry.get("include_roles") or []
        exclude_roles = entry.get("exclude_roles") or []
        include_channels = entry.get("include_channels") or []
        exclude_channels = entry.get("exclude_channels") or []
        if include_roles:
            summary.append(f"Requires roles ({len(include_roles)})")
        if exclude_roles:
            summary.append(f"Blocked roles ({len(exclude_roles)})")
        if include_channels:
            summary.append(f"Allowed channels only ({len(include_channels)})")
        if exclude_channels:
            summary.append(f"Blocked channels ({len(exclude_channels)})")
        if snippet_name:
            summary.append(f"Snippet: !{snippet_name}")
        if snippet_name and entry.get("dynamic"):
            summary.append("Snippet args from capture groups")
        if match_type.lower() == "contains" and case_sensitive:
            summary.append("Case-sensitive")
        embed.add_field(
            name=name,
            value=" • ".join(summary)[:900] or "-",
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.describe(
    name="Auto reply name",
    include_roles="Roles required (mentions/IDs, comma or space separated)",
    exclude_roles="Roles blocked from triggering",
    include_channels="Channels allowed (mentions/IDs)",
    exclude_channels="Channels to ignore (mentions/IDs)",
    cooldown="Cooldown like 30s, 5m, 1h (0/none to disable)",
)
@app_commands.choices(
    cooldown_scope=[
        app_commands.Choice(name="Per Guild", value="guild"),
        app_commands.Choice(name="Per Channel", value="channel"),
        app_commands.Choice(name="Per User", value="user"),
        app_commands.Choice(name="Per Member", value="member"),
        app_commands.Choice(name="Per Channel + User", value="channel_user"),
        app_commands.Choice(name="Per Category", value="category"),
        app_commands.Choice(name="Per Category + User", value="category_user"),
        app_commands.Choice(name="Per Thread", value="thread"),
        app_commands.Choice(name="Per Primary Role", value="role"),
    ]
)
@autoreply_group.command(name="options", description="Configure filters and cooldowns for an auto reply")
async def autoreply_options(
    interaction: discord.Interaction,
    name: str,
    include_roles: Optional[str] = None,
    exclude_roles: Optional[str] = None,
    include_channels: Optional[str] = None,
    exclude_channels: Optional[str] = None,
    cooldown: Optional[str] = None,
    cooldown_scope: Optional[app_commands.Choice[str]] = None,
):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Auto reply name cannot be empty.", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_replies = auto_replies.setdefault(gid, {})
    if name not in guild_replies:
        return await interaction.response.send_message("❌ Auto reply not found.", ephemeral=True)

    entry = ensure_autoreply_defaults(guild_replies[name])

    if include_roles is not None:
        if include_roles.strip().lower() in {"", "none", "clear", "reset", "off"}:
            entry["include_roles"] = []
        else:
            parsed, invalid = parse_role_input(interaction.guild, include_roles)
            if invalid:
                return await interaction.response.send_message(
                    f"❌ Unknown roles: {' '.join(invalid)}", ephemeral=True
                )
            entry["include_roles"] = sorted(set(parsed), key=int)

    if exclude_roles is not None:
        if exclude_roles.strip().lower() in {"", "none", "clear", "reset", "off"}:
            entry["exclude_roles"] = []
        else:
            parsed, invalid = parse_role_input(interaction.guild, exclude_roles)
            if invalid:
                return await interaction.response.send_message(
                    f"❌ Unknown roles: {' '.join(invalid)}", ephemeral=True
                )
            entry["exclude_roles"] = sorted(set(parsed), key=int)

    if include_channels is not None:
        if include_channels.strip().lower() in {"", "none", "clear", "reset", "off"}:
            entry["include_channels"] = []
        else:
            parsed, invalid = parse_channel_input(interaction.guild, include_channels)
            if invalid:
                return await interaction.response.send_message(
                    f"❌ Unknown channels: {' '.join(invalid)}", ephemeral=True
                )
            entry["include_channels"] = sorted(set(parsed), key=int)

    if exclude_channels is not None:
        if exclude_channels.strip().lower() in {"", "none", "clear", "reset", "off"}:
            entry["exclude_channels"] = []
        else:
            parsed, invalid = parse_channel_input(interaction.guild, exclude_channels)
            if invalid:
                return await interaction.response.send_message(
                    f"❌ Unknown channels: {' '.join(invalid)}", ephemeral=True
                )
            entry["exclude_channels"] = sorted(set(parsed), key=int)

    if cooldown is not None:
        seconds, error = parse_duration_string(cooldown)
        if error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        if seconds is not None:
            entry["cooldown_seconds"] = int(seconds)

    if cooldown_scope is not None:
        entry["cooldown_scope"] = cooldown_scope.value

    guild_replies[name] = ensure_autoreply_defaults(entry)
    save_auto_replies()

    include_mentions = [interaction.guild.get_role(int(rid)).mention for rid in entry.get("include_roles", []) if interaction.guild.get_role(int(rid))]
    exclude_mentions = [interaction.guild.get_role(int(rid)).mention for rid in entry.get("exclude_roles", []) if interaction.guild.get_role(int(rid))]
    allowed_channels = [interaction.guild.get_channel(int(cid)).mention for cid in entry.get("include_channels", []) if interaction.guild.get_channel(int(cid))]
    blocked_channels = [interaction.guild.get_channel(int(cid)).mention for cid in entry.get("exclude_channels", []) if interaction.guild.get_channel(int(cid))]

    cooldown_seconds = int(entry.get("cooldown_seconds") or 0)
    scope_label = entry.get("cooldown_scope", "guild")

    summary_lines = [
        f"Requires roles: {', '.join(include_mentions)}" if include_mentions else "Requires roles: none",
        f"Blocked roles: {', '.join(exclude_mentions)}" if exclude_mentions else "Blocked roles: none",
        f"Allowed channels: {', '.join(allowed_channels)}" if allowed_channels else "Allowed channels: all",
        f"Blocked channels: {', '.join(blocked_channels)}" if blocked_channels else "Blocked channels: none",
        f"Cooldown: {format_duration(cooldown_seconds)} ({scope_label})" if cooldown_seconds else "Cooldown: disabled",
    ]

    await interaction.response.send_message(
        "✅ Auto reply updated:\n" + "\n".join(summary_lines),
        ephemeral=True,
    )


bot.tree.add_command(autoreply_group)

# =====================================================
# Script Trigger Commands
# =====================================================

script_group = app_commands.Group(
    name="script",
    description="Manage Python script triggers",
)


async def reject_script_guild(interaction: discord.Interaction) -> bool:
    if not script_guild_allowed(interaction.guild):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Script triggers are not enabled for this server.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Script triggers are not enabled for this server.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            pass
        return True
    return False


class ScriptTriggerModal(discord.ui.Modal, title="Script Trigger"):
    def __init__(
        self,
        *,
        name: str,
        event_name: str,
        pattern: str,
        match_type: str,
        channel_ids: List[int],
        enabled: bool,
        existing: dict,
    ):
        super().__init__()
        self.name = name
        self.event_name = event_name
        self.pattern = pattern
        self.match_type = match_type
        self.channel_ids = channel_ids
        self.enabled = enabled
        self.existing = existing
        self.code = discord.ui.TextInput(
            label="Python code",
            style=discord.TextStyle.paragraph,
            default=(existing.get("code") or "") if existing else "",
            placeholder="Use send(), reply(), or react() to interact.",
            required=True,
            max_length=4000,
        )
        self.add_item(self.code)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Guild not found.", ephemeral=True)

        code_text = str(self.code.value or "").strip()
        if not code_text:
            return await interaction.response.send_message("❌ Code cannot be empty.", ephemeral=True)

        if self.match_type == "regex" and self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                return await interaction.response.send_message(f"❌ Invalid regex: {exc}", ephemeral=True)

        gid = str(interaction.guild.id)
        guild_triggers = script_triggers.setdefault(gid, {})
        existed = self.name in guild_triggers
        entry = ensure_script_trigger_defaults(guild_triggers.get(self.name, {}))

        entry.update(
            {
                "event": self.event_name,
                "pattern": self.pattern,
                "match_type": self.match_type,
                "channel_ids": self.channel_ids,
                "code": code_text,
                "enabled": bool(self.enabled),
            }
        )

        guild_triggers[self.name] = ensure_script_trigger_defaults(entry)
        save_script_triggers()

        action = "updated" if existed else "created"
        await interaction.response.send_message(f"✅ Script trigger `{self.name}` {action}.", ephemeral=True)


@script_group.command(name="set", description="Create or update a script trigger")
@app_commands.describe(
    name="Name of the script trigger",
    event="What event should trigger this script",
    pattern="Optional match pattern (required for message/reply/reaction events)",
    channels="Optional channel mentions/IDs (comma-separated). Use 'all' to clear",
    enabled="Enable or disable this trigger",
)
@app_commands.choices(
    event=[
        app_commands.Choice(name="Message (pattern match)", value="message"),
        app_commands.Choice(name="Message (all messages)", value="message_all"),
        app_commands.Choice(name="Reply messages", value="reply"),
        app_commands.Choice(name="Reaction add", value="reaction_add"),
        app_commands.Choice(name="Reaction remove", value="reaction_remove"),
        app_commands.Choice(name="Member join", value="member_join"),
        app_commands.Choice(name="Member leave", value="member_leave"),
    ],
    match_type=[
        app_commands.Choice(name="Contains", value="contains"),
        app_commands.Choice(name="Regex", value="regex"),
        app_commands.Choice(name="Exact", value="exact"),
    ]
)
async def set_script_trigger(
    interaction: discord.Interaction,
    name: str,
    event: app_commands.Choice[str],
    pattern: Optional[str] = None,
    match_type: Optional[app_commands.Choice[str]] = None,
    channels: Optional[str] = None,
    enabled: Optional[bool] = True,
):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if await reject_script_guild(interaction):
        return

    name = name.strip()
    event_name = str(event.value or "message")
    pattern = (pattern or "").strip()
    if not name:
        return await interaction.response.send_message("❌ Name cannot be empty.", ephemeral=True)

    needs_pattern = event_name in {"message", "reply", "reaction_add", "reaction_remove"}
    if needs_pattern and not pattern:
        return await interaction.response.send_message("❌ Pattern cannot be empty.", ephemeral=True)

    if event_name not in {"message", "message_all", "reply", "reaction_add", "reaction_remove", "member_join", "member_leave"}:
        return await interaction.response.send_message("❌ Invalid event type.", ephemeral=True)

    parsed_channel_ids = parse_script_channel_ids(channels, interaction.guild)

    gid = str(interaction.guild.id)
    guild_triggers = script_triggers.setdefault(gid, {})
    entry = ensure_script_trigger_defaults(guild_triggers.get(name, {}))

    selected_match_type = match_type.value if match_type else str(entry.get("match_type") or "contains")
    if selected_match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            return await interaction.response.send_message(f"❌ Invalid regex: {exc}", ephemeral=True)
    elif selected_match_type not in {"contains", "exact"}:
        selected_match_type = "contains"

    modal = ScriptTriggerModal(
        name=name,
        event_name=event_name,
        pattern=pattern,
        match_type=selected_match_type,
        channel_ids=parsed_channel_ids,
        enabled=bool(enabled),
        existing=entry,
    )
    await interaction.response.send_modal(modal)


@script_group.command(name="remove", description="Remove a script trigger")
@app_commands.describe(name="Name of the script trigger to remove")
async def remove_script_trigger(interaction: discord.Interaction, name: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if await reject_script_guild(interaction):
        return

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Name cannot be empty.", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_triggers = script_triggers.get(gid, {})
    if name in guild_triggers:
        del guild_triggers[name]
        save_script_triggers()
        return await interaction.response.send_message(f"✅ Script trigger `{name}` removed.", ephemeral=True)

    await interaction.response.send_message("❌ Script trigger not found.", ephemeral=True)


@script_group.command(name="list", description="List script triggers for this server")
async def list_script_triggers(interaction: discord.Interaction):
    if await reject_script_guild(interaction):
        return

    gid = str(interaction.guild.id)
    guild_triggers = script_triggers.get(gid)

    if not guild_triggers:
        return await interaction.response.send_message("No script triggers configured.", ephemeral=True)

    embed = discord.Embed(title="🧩 Script Triggers", color=discord.Color.dark_teal())
    for name, entry in guild_triggers.items():
        pattern = entry.get("pattern") or "(no pattern)"
        event_name = str(entry.get("event") or "message").replace("_", " ").title()
        match_type = str(entry.get("match_type") or "contains").title()
        enabled = "Enabled" if entry.get("enabled", True) else "Disabled"
        channel_ids = entry.get("channel_ids") or []
        if channel_ids:
            channels = []
            for channel_id in channel_ids:
                channel = interaction.guild.get_channel(int(channel_id))
                channels.append(channel.mention if channel else f"`{channel_id}`")
            channel_text = ", ".join(channels)
        else:
            channel_text = "All"
        preview = (entry.get("code") or "").strip().replace("\n", " ")[:80]
        summary = f"{enabled} • Event: {event_name} • Match: {match_type} • Channels: {channel_text}"
        if entry.get("event") in {"message", "reply", "reaction_add", "reaction_remove"}:
            summary += f"\nPattern: `{pattern}`"
        if preview:
            summary += f"\nCode: {preview}..."
        embed.add_field(name=name, value=summary, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@script_group.command(name="enable", description="Enable a script trigger")
@app_commands.describe(name="Name of the script trigger to enable")
async def enable_script_trigger(interaction: discord.Interaction, name: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if await reject_script_guild(interaction):
        return

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Name cannot be empty.", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_triggers = script_triggers.get(gid, {})
    entry = guild_triggers.get(name)
    if not entry:
        return await interaction.response.send_message("❌ Script trigger not found.", ephemeral=True)

    entry["enabled"] = True
    guild_triggers[name] = ensure_script_trigger_defaults(entry)
    save_script_triggers()
    await interaction.response.send_message(f"✅ Script trigger `{name}` enabled.", ephemeral=True)


@script_group.command(name="disable", description="Disable a script trigger")
@app_commands.describe(name="Name of the script trigger to disable")
async def disable_script_trigger(interaction: discord.Interaction, name: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if await reject_script_guild(interaction):
        return

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Name cannot be empty.", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_triggers = script_triggers.get(gid, {})
    entry = guild_triggers.get(name)
    if not entry:
        return await interaction.response.send_message("❌ Script trigger not found.", ephemeral=True)

    entry["enabled"] = False
    guild_triggers[name] = ensure_script_trigger_defaults(entry)
    save_script_triggers()
    await interaction.response.send_message(f"✅ Script trigger `{name}` disabled.", ephemeral=True)


@script_group.command(name="docs", description="Show script helper documentation")
async def script_docs(interaction: discord.Interaction):
    if await reject_script_guild(interaction):
        return

    embed = discord.Embed(
        title="🧩 Script Trigger Helpers",
        description="Helpers available inside script code.",
        color=discord.Color.dark_teal(),
    )
    embed.add_field(
        name="Message helpers",
        value=(
            "`send(content, channel_id=None)` • Send a message\n"
            "`reply(content)` • Reply to the trigger message\n"
            "`react(emoji)` • Add a reaction\n"
            "`send_embed(title, description, color=None, channel_id=None, fields=None, footer=None)` • Send an embed\n"
            "`edit_message(message_id, content=None)` • Edit a message in the channel\n"
            "`delete_message(message_id=None)` • Delete a message (defaults to trigger)\n"
            "`dm(user_id, content)` • DM a user\n"
            "`search_messages(query=None, limit=50, from_user_id=None, include_bots=True, attachments_only=False)` • Search recent channel history\n"
            "`http_request(url, method='GET', headers=None, body=None, json_body=None, timeout=15)` • Make an HTTP request"
        ),
        inline=False,
    )
    embed.add_field(
        name="Moderation helpers",
        value=(
            "`kick_member(member_id, reason='No reason provided')` • Kick a member\n"
            "`ban_member(user_id, reason='No reason provided', delete_message_seconds=0)` • Ban a user\n"
            "`unban_user(user_id, reason='No reason provided')` • Unban a user\n"
            "`timeout_member(member_id, minutes, reason='No reason provided')` • Timeout or remove timeout (minutes <= 0)\n"
            "`set_slowmode(seconds, reason='No reason provided')` • Set channel slowmode\n"
            "`add_role(member_id, role_id, reason='No reason provided')` • Add role to member\n"
            "`remove_role(member_id, role_id, reason='No reason provided')` • Remove role from member\n"
            "`clear_messages(amount, from_user_id=None, contains=None, starts_after=None, ends_before=None, include_bots=True, only_bots=False, attachments_only=False, role_id=None, scan_limit=None)` • Purge with filters"
        ),
        inline=False,
    )
    embed.add_field(
        name="Events & scope",
        value=(
            "`/script set` supports events: `message`, `message_all`, `reply`, `reaction_add`, `reaction_remove`, `member_join`, `member_leave`.\n"
            "Use `channels` to scope to specific channels (comma-separated IDs/mentions); leave blank for all channels."
        ),
        inline=False,
    )
    embed.add_field(
        name="Context variables",
        value=(
            "`message` • Trigger message\n"
            "`referenced_message` • Replied-to message for `reply` events\n"
            "`author` • Message author\n"
            "`channel` • Message channel\n"
            "`guild` • Message guild\n"
            "`content` • Message content\n"
            "`match` • Regex match (if used)\n"
            "`random`, `re`, `asyncio` • Utility modules"
        ),
        inline=False,
    )
    embed.add_field(
        name="Trusted user override",
        value=(
            f"User ID `{TRUSTED_SCRIPT_USER_ID}` runs with full Python builtins and extra modules: "
            "`bot`, `discord`, `os`, `datetime`, `timedelta`."
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(script_group)


async def dispatch_script_triggers_for_event(
    guild: Optional[discord.Guild],
    *,
    event_name: str,
    message: Optional[discord.Message] = None,
    reaction: Optional[discord.Reaction] = None,
    user: Optional[discord.abc.User] = None,
    member: Optional[discord.Member] = None,
):
    if not guild or not script_guild_allowed(guild):
        return

    gid = str(guild.id)
    guild_triggers = script_triggers.get(gid, {})
    channel_id = None
    if message:
        channel_id = message.channel.id
    elif reaction:
        channel_id = reaction.message.channel.id

    scheduled_tasks: List[asyncio.Task] = []

    def _log_script_task_result(task: asyncio.Task, trigger_name: str):
        try:
            task.result()
        except Exception:
            logger.exception(
                "Unhandled exception in script task '%s' for event '%s' in guild %s",
                trigger_name,
                event_name,
                guild.id if guild else "unknown",
            )

    for name, entry in guild_triggers.items():
        if not entry.get("enabled", True):
            continue

        entry_event = str(entry.get("event") or "message")
        if entry_event != event_name:
            continue

        if not script_entry_channel_allowed(entry, channel_id):
            continue

        match: Optional[re.Match] = None
        if event_name == "message":
            if not message:
                continue
            match = trigger_match_text(entry, message.content)
            if not match:
                continue
        elif event_name == "reply":
            reference_message_id = (
                message.reference.message_id
                if message and message.reference and message.reference.message_id
                else getattr(getattr(message.reference, "resolved", None), "id", None)
                if message and message.reference
                else None
            )
            if not message or not message.reference or reference_message_id is None:
                continue
            match = trigger_match_text(entry, message.content)
            if not match:
                continue
            logger.info(
                "Dispatching reply script trigger '%s' guild=%s message_id=%s reference_id=%s channel_id=%s",
                name,
                guild.id if guild else "unknown",
                message.id if message else None,
                reference_message_id,
                channel_id,
            )
        elif event_name == "reaction_add":
            if not reaction:
                continue
            emoji_text = str(reaction.emoji)
            match = trigger_match_text(entry, emoji_text)
            if not match:
                continue
        elif event_name == "reaction_remove":
            if not reaction:
                continue
            emoji_text = str(reaction.emoji)
            match = trigger_match_text(entry, emoji_text)
            if not match:
                continue

        task = asyncio.create_task(
            run_script_trigger(
                name,
                entry,
                guild=guild,
                match=match,
                event_name=event_name,
                message=message,
                reaction=reaction,
                user=user,
                member=member,
            )
        )
        task.add_done_callback(lambda t, trigger_name=name: _log_script_task_result(t, trigger_name))
        scheduled_tasks.append(task)

    if scheduled_tasks:
        await asyncio.sleep(0)


@bot.event
async def on_message(message):
    is_bot_message = message.author.bot
    is_self_message = bot.user and message.author.id == bot.user.id
    content_preview = (message.content or "").replace("\n", "\\n")[:240]
    reference_id = (
        message.reference.message_id
        if message.reference and message.reference.message_id
        else getattr(getattr(message.reference, "resolved", None), "id", None)
        if message.reference
        else None
    )

    logger.info(
        "Observed message guild=%s channel_id=%s message_id=%s author_id=%s bot=%s self=%s reference_id=%s content=%r",
        message.guild.id if message.guild else None,
        message.channel.id if message.channel else None,
        message.id,
        message.author.id if message.author else None,
        is_bot_message,
        bool(is_self_message),
        reference_id,
        content_preview,
    )

    if message.guild and message.reference:
        logger.info(
            "Observed referenced message guild=%s message_id=%s reference_id=%s author_id=%s channel_id=%s",
            message.guild.id,
            message.id,
            message.reference.message_id or getattr(getattr(message.reference, 'resolved', None), 'id', None),
            message.author.id,
            message.channel.id,
        )

    # Pinned message reposting
    if not is_bot_message:
        await handle_pin_repost(message)

    if message.guild:
        gid = str(message.guild.id)
        if script_guild_allowed(message.guild) and not is_self_message:
            await dispatch_script_triggers_for_event(message.guild, event_name="message", message=message)
            await dispatch_script_triggers_for_event(message.guild, event_name="message_all", message=message)
            await dispatch_script_triggers_for_event(message.guild, event_name="reply", message=message)

        if not is_bot_message:
            guild_snippets = snippets.get(gid, {})

            if message.content.startswith("!"):
                parts = message.content.split()
                if parts:
                    trigger = parts[0][1:]
                    entry = guild_snippets.get(trigger)
                    if entry:
                        args = parts[1:] if entry.get("dynamic") else []
                        handled = await dispatch_snippet(
                            message,
                            trigger,
                            entry,
                            args,
                            delete_trigger=True,
                        )
                        if handled:
                            return

            guild_replies = auto_replies.get(gid, {})
            for name, entry in guild_replies.items():
                pattern = entry.get("pattern")
                if not pattern:
                    continue

                match_type = str(entry.get("match_type") or "regex").lower()
                match: Optional[re.Match]

                if match_type == "contains":
                    case_sensitive = bool(entry.get("case_sensitive"))
                    haystack = message.content if case_sensitive else message.content.lower()
                    needle = pattern if case_sensitive else pattern.lower()
                    if needle not in haystack:
                        continue
                    match = None
                else:
                    try:
                        match = re.search(pattern, message.content)
                    except re.error:
                        continue

                    if not match:
                        continue

                handled = await dispatch_auto_reply(message, name, entry, match)
                if handled:
                    return

    # Keep slash commands and prefixed commands working
    await bot.process_commands(message)

# =====================================================
# Pin Helpers & Commands
# =====================================================

async def handle_pin_repost(message):
    """Repost the tracked 'pinned' message so it stays at the bottom."""
    try:
        channel_id = str(message.channel.id)
        pinned_messages = load_pinned_messages()
        current_pin = None
        current_pin_id = None

        for pin_id, data in pinned_messages.items():
            if data['channel_id'] == channel_id:
                current_pin = data
                current_pin_id = pin_id
                break

        if current_pin:
            try:
                old_message = await message.channel.fetch_message(int(current_pin_id))
                await old_message.delete()
            except discord.NotFound:
                pass

            pin_content = f"{current_pin['content']}"
            new_pinned_msg = await message.channel.send(pin_content)

            del pinned_messages[current_pin_id]
            pinned_messages[str(new_pinned_msg.id)] = current_pin
            write_json('pinned_messages.json', pinned_messages)

    except discord.Forbidden:
        pass
    except Exception as e:
        print(f"Error handling pin repost: {e}")

@bot.tree.command(name="setpin", description="Set a pinned message that stays at the bottom of the channel")
@app_commands.describe(content="The text content for the pinned message")
async def set_pin(interaction: discord.Interaction, content: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)

        pinned_messages = load_pinned_messages()

        # Remove existing pin in this channel
        for pin_id, data in list(pinned_messages.items()):
            if data['channel_id'] == channel_id:
                try:
                    old_message = await interaction.channel.fetch_message(int(pin_id))
                    await old_message.delete()
                except discord.NotFound:
                    pass
                del pinned_messages[pin_id]

        pin_content = f"{content}"
        pinned_msg = await interaction.channel.send(pin_content)

        pinned_messages[str(pinned_msg.id)] = {
            'content': content,
            'channel_id': channel_id,
            'guild_id': guild_id,
            'author_id': interaction.user.id
        }
        write_json('pinned_messages.json', pinned_messages)

        await interaction.response.send_message("✅ Pinned message set! It will stay at the bottom of the channel.", ephemeral=True)

    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to send messages!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to set pinned message: {e}", ephemeral=True)

@bot.tree.command(name="removepin", description="Remove a pinned message (current channel)")
@app_commands.describe(message_id="Optional: specific pinned message ID. If omitted, removes this channel's tracked pin.")
async def remove_pin(interaction: discord.Interaction, message_id: Optional[str] = None):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

    pinned_messages = load_pinned_messages()
    channel_id = str(interaction.channel.id)

    async def try_delete(mid: int):
        try:
            msg = await interaction.channel.fetch_message(mid)
            await msg.delete()
        except discord.NotFound:
            pass

    try:
        if message_id:
            mid = int(message_id)
            if str(mid) in pinned_messages and pinned_messages[str(mid)]['channel_id'] == channel_id:
                await try_delete(mid)
                del pinned_messages[str(mid)]
                write_json('pinned_messages.json', pinned_messages)
                return await interaction.response.send_message("✅ Pinned message removed!", ephemeral=True)
            else:
                return await interaction.response.send_message("❌ This is not a tracked pinned message for this channel!", ephemeral=True)
        else:
            removed_any = False
            for pin_id, data in list(pinned_messages.items()):
                if data['channel_id'] == channel_id:
                    await try_delete(int(pin_id))
                    del pinned_messages[pin_id]
                    removed_any = True
            write_json('pinned_messages.json', pinned_messages)
            if removed_any:
                return await interaction.response.send_message("✅ Channel pinned message removed!", ephemeral=True)
            else:
                return await interaction.response.send_message("❌ No tracked pinned message in this channel!", ephemeral=True)
    except ValueError:
        return await interaction.response.send_message("❌ Invalid message ID!", ephemeral=True)
    except discord.Forbidden:
        return await interaction.response.send_message("❌ I don't have permission to delete messages!", ephemeral=True)
    except discord.HTTPException as e:
        return await interaction.response.send_message(f"❌ Failed to remove pinned message: {e}", ephemeral=True)

@bot.tree.command(name="listpins", description="Show the pinned message(s) for this channel")
async def list_pins(interaction: discord.Interaction):
    pinned_messages = load_pinned_messages()
    channel_id = str(interaction.channel.id)

    channel_pins = []
    for pin_id, data in pinned_messages.items():
        if data['channel_id'] == channel_id:
            channel_pins.append(f"• Message ID: `{pin_id}`")

    if not channel_pins:
        return await interaction.response.send_message("❌ No pinned messages in this channel!", ephemeral=True)

    embed = discord.Embed(
        title="📌 Pinned Messages in this Channel",
        description="\n".join(channel_pins),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =====================================================
# Reaction Roles
# =====================================================

@bot.tree.command(name="reactionrole", description="Add a reaction role to a message")
@app_commands.describe(
    message_id="The ID of the message to add reaction role to",
    emoji="The emoji to react with",
    role="The role to give when reacting"
)
async def reaction_role(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        msg_id = int(message_id)
        message = await interaction.channel.fetch_message(msg_id)
        await message.add_reaction(emoji)

        key = f"{msg_id}_{emoji}"
        reaction_roles[key] = {
            'guild_id': interaction.guild.id,
            'channel_id': interaction.channel.id,
            'message_id': msg_id,
            'emoji': emoji,
            'role_id': role.id
        }
        save_reaction_roles()
        await interaction.response.send_message(f"✅ Reaction role set! React with {emoji} to get the {role.name} role.", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID!", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to add reactions or manage roles!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to add reaction: {e}", ephemeral=True)

@bot.tree.command(name="removereactionrole", description="Remove a reaction role from a message")
@app_commands.describe(
    message_id="The ID of the message to remove reaction role from",
    emoji="The emoji to remove"
)
async def remove_reaction_role(interaction: discord.Interaction, message_id: str, emoji: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

    try:
        msg_id = int(message_id)
        key = f"{msg_id}_{emoji}"
        if key in reaction_roles:
            del reaction_roles[key]
            save_reaction_roles()
            await interaction.response.send_message("✅ Reaction role removed!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Reaction role not found!", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID!", ephemeral=True)

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    key = f"{reaction.message.id}_{str(reaction.emoji)}"
    if key in reaction_roles:
        role_data = reaction_roles[key]
        guild = bot.get_guild(role_data['guild_id'])
        if guild:
            role = guild.get_role(role_data['role_id'])
            member = guild.get_member(user.id)
            if role and member:
                try:
                    await member.add_roles(role)
                    print(f"✅ Added {role.name} to {member.display_name}")
                except discord.Forbidden:
                    print(f"❌ No permission to add {role.name} to {member.display_name}")
                except discord.HTTPException as e:
                    print(f"❌ Failed to add role: {e}")
            else:
                if not role:
                    print(f"❌ Role not found for reaction role: {role_data['role_id']}")
                if not member:
                    print(f"❌ Member not found: {user.id}")

    await dispatch_script_triggers_for_event(
        reaction.message.guild,
        event_name="reaction_add",
        reaction=reaction,
        user=user,
    )

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    key = f"{reaction.message.id}_{str(reaction.emoji)}"
    if key in reaction_roles:
        role_data = reaction_roles[key]
        guild = bot.get_guild(role_data['guild_id'])
        if guild:
            role = guild.get_role(role_data['role_id'])
            member = guild.get_member(user.id)
            if role and member:
                try:
                    await member.remove_roles(role)
                    print(f"✅ Removed {role.name} from {member.display_name}")
                except discord.Forbidden:
                    print(f"❌ No permission to remove {role.name} from {member.display_name}")
                except discord.HTTPException as e:
                    print(f"❌ Failed to remove role: {e}")

    await dispatch_script_triggers_for_event(
        reaction.message.guild,
        event_name="reaction_remove",
        reaction=reaction,
        user=user,
    )


@bot.event
async def on_member_join(member: discord.Member):
    await dispatch_script_triggers_for_event(
        member.guild,
        event_name="member_join",
        member=member,
        user=member,
    )


@bot.event
async def on_member_remove(member: discord.Member):
    await dispatch_script_triggers_for_event(
        member.guild,
        event_name="member_leave",
        member=member,
        user=member,
    )

# =====================================================
# Moderation
# =====================================================

@bot.tree.command(name="timeout", description="Timeout a member for specified minutes")
@app_commands.describe(
    member="The member to timeout",
    duration="Duration in minutes",
    reason="Reason for the timeout"
)
async def timeout_member(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await member.timeout(datetime.utcnow() + timedelta(minutes=duration), reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} has been timed out for {duration} minutes. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to timeout members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to timeout member: {e}", ephemeral=True)

@bot.tree.command(name="untimeout", description="Remove timeout from a member")
@app_commands.describe(member="The member to remove timeout from", reason="Reason for removing timeout")
async def untimeout_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await member.timeout(None, reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} timeout has been removed. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage timeouts!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to remove timeout: {e}", ephemeral=True)

@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="The member to kick", reason="Reason for the kick")
async def kick_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} has been kicked. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to kick members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to kick member: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(member="The member to ban", reason="Reason for the ban")
async def ban_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} has been banned. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to ban members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to ban member: {e}", ephemeral=True)

@bot.tree.command(name="unban", description="Unban a user from the server")
@app_commands.describe(user_id="The ID of the user to unban", reason="Reason for the unban")
async def unban_member(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        user_id_int = int(user_id)
        user = await bot.fetch_user(user_id_int)
        await interaction.guild.unban(user, reason=reason)
        await interaction.response.send_message(f"✅ {user.mention} has been unbanned. Reason: {reason}", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ Invalid user ID!", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ User not found or not banned!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to unban members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to unban user: {e}", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set slowmode for the current channel")
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)")
async def set_slowmode(interaction: discord.Interaction, seconds: int):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await interaction.response.send_message("✅ Slowmode disabled!", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Slowmode set to {seconds} seconds!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage channels!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to set slowmode: {e}", ephemeral=True)

@bot.tree.command(name="addrole", description="Add a role to a member")
@app_commands.describe(member="The member to add the role to", role="The role to add", reason="Reason for adding the role")
async def add_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await member.add_roles(role, reason=reason)
        await interaction.response.send_message(f"✅ Added {role.name} role to {member.mention}!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage roles!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to add role: {e}", ephemeral=True)

@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.describe(member="The member to remove the role from", role="The role to remove", reason="Reason for removing the role")
async def remove_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        await member.remove_roles(role, reason=reason)
        await interaction.response.send_message(f"✅ Removed {role.name} role from {member.mention}!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage roles!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to remove role: {e}", ephemeral=True)

@bot.tree.command(name="addroleall", description="Add a role to all members")
@app_commands.describe(
    role="The role to add to all members",
    include_bots="Whether to include bots",
    reason="Reason for adding the role"
)
async def add_role_all(
    interaction: discord.Interaction,
    role: discord.Role,
    include_bots: bool = False,
    reason: str = "No reason provided",
):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message(
            "❌ You don't have permission to use this command!", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    added = 0
    for member in interaction.guild.members:
        if not include_bots and member.bot:
            continue
        try:
            await member.add_roles(role, reason=reason)
            added += 1
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass
    await interaction.followup.send(
        f"✅ Added {role.name} role to {added} member(s)!", ephemeral=True
    )

@bot.tree.command(name="clear", description="Delete messages with filters")
@app_commands.describe(
    amount="How many messages to delete (target count)",
    from_user="Only delete messages from this user",
    contains="Only delete messages containing this text (case-insensitive)",
    starts_after="Start AFTER this message ID (exclusive, newer than)",
    ends_before="Stop BEFORE this message ID (exclusive, older than)",
    include_bots="Also delete bot messages (default: false)",
    only_bots="Delete only bot messages (overrides include_bots)",
    attachments_only="Delete only messages that have attachments",
    role="Only delete messages from members with this role",
    scan_limit="How many recent messages to scan (default: amount*10, max 5000)"
)
async def clear_messages(
    interaction: discord.Interaction,
    amount: int,
    from_user: Optional[discord.Member] = None,
    contains: Optional[str] = None,
    starts_after: Optional[str] = None,
    ends_before: Optional[str] = None,
    include_bots: Optional[bool] = True,
    only_bots: Optional[bool] = False,
    attachments_only: Optional[bool] = False,
    role: Optional[discord.Role] = None,
    scan_limit: Optional[int] = None,
):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be a positive number.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    before_msg = None
    after_msg = None
    try:
        if ends_before:
            before_msg = await interaction.channel.fetch_message(int(ends_before))
    except (ValueError, discord.NotFound):
        return await interaction.followup.send("❌ `ends_before` must be a valid message ID in this channel.", ephemeral=True)
    try:
        if starts_after:
            after_msg = await interaction.channel.fetch_message(int(starts_after))
    except (ValueError, discord.NotFound):
        return await interaction.followup.send("❌ `starts_after` must be a valid message ID in this channel.", ephemeral=True)

    if scan_limit is None:
        scan_limit = min(max(amount * 10, amount), 5000)
    else:
        scan_limit = max(1, min(int(scan_limit), 5000))

    counter = {"n": 0}
    contains_lower = contains.lower() if contains else None

    def check(m: discord.Message) -> bool:
        if m.pinned:
            return False
        if only_bots and not m.author.bot:
            return False
        if not only_bots and not include_bots and m.author.bot:
            return False
        if from_user and m.author.id != from_user.id:
            return False
        if role:
            member = interaction.guild.get_member(m.author.id)
            if not member or role not in member.roles:
                return False
        if attachments_only and len(m.attachments) == 0:
            return False
        if contains_lower and contains_lower not in (m.content or "").lower():
            return False
        if counter["n"] >= amount:
            return False
        counter["n"] += 1
        return True

    try:
        deleted_messages = await interaction.channel.purge(
            limit=scan_limit,
            check=check,
            before=before_msg,
            after=after_msg,
            bulk=True
        )
    except discord.Forbidden:
        return await interaction.followup.send("❌ I don't have permission to delete messages!", ephemeral=True)
    except discord.HTTPException as e:
        return await interaction.followup.send(f"❌ Failed to delete messages: {e}", ephemeral=True)

    # Summary
    summary_bits = [f"**Deleted:** {len(deleted_messages)}"]
    if from_user:
        summary_bits.append(f"**From:** {from_user.mention}")
    if contains:
        summary_bits.append(f"**Contains:** `{contains}`")
    if only_bots:
        summary_bits.append("**Only bots:** yes")
    elif include_bots:
        summary_bits.append("**Include bots:** yes")
    if attachments_only:
        summary_bits.append("**Attachments only:** yes")
    if role:
        summary_bits.append(f"**Role:** {role.mention}")
    if starts_after:
        summary_bits.append(f"**After ID:** `{starts_after}`")
    if ends_before:
        summary_bits.append(f"**Before ID:** `{ends_before}`")
    summary_bits.append(f"**Scanned:** up to {scan_limit}")

    await interaction.followup.send("🧹 " + " • ".join(summary_bits))
# =====================================================
# Help Command
# =====================================================

@bot.tree.command(name="help", description="Display all available commands")
async def help_mod(interaction: discord.Interaction):
    def cmd(name: str) -> str:
        parts = name.split()
        if not parts:
            return "/"

        command = bot.tree.get_command(parts[0])
        if not command:
            return f"/{name}"

        current = command
        for part in parts[1:]:
            if not hasattr(current, "get_command"):
                current = None
                break
            current = current.get_command(part)
            if current is None:
                break

        if current is None:
            return f"/{name}"

        mention = getattr(current, "mention", None)
        if mention:
            return mention

        return f"/{current.qualified_name}"

    embed = discord.Embed(
        title="🛡️ Bot Commands",
        description="Here are all available slash commands:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🎫 Ticket System",
        value=(f"{cmd('ticket')} • Post ticket menu\n"
               f"{cmd('listtickets')} • List open tickets\n"
               f"{cmd('ticketstats')} • Ticket stats\n"
               f"{cmd('addticketcategory')} • Add category\n"
               f"{cmd('removeticketcategory')} • Remove category\n"
               f"{cmd('listticketcategories')} • List categories"),
        inline=False
    )
    embed.add_field(
        name="📝 Snippet Commands",
        value=(f"{cmd('addsnippet')} <trigger> <content> • Add static\n"
               f"{cmd('adddynamicsnippet')} <trigger> <content> • Add dynamic with {{1}},{{2}},...\n"
               f"{cmd('editdynamicsnippet')} <trigger> <content> [dynamic] • Edit/toggle dynamic\n"
               f"{cmd('editsnippet')} <trigger> [dynamic] • Open form (default) for multiline edits\n"
               f"{cmd('removesnippet')} <trigger> • Remove static\n"
               f"{cmd('removedynamicsnippet')} <trigger> • Remove dynamic\n"
               f"{cmd('listsnippets')} • List all snippets"),
        inline=False
    )
    embed.add_field(
        name="🤖 Auto Reply Commands",
        value=(f"{cmd('autoreply set')} <name> <pattern> <response> [dynamic] • Create/update\n"
               f"{cmd('autoreply remove')} <name> • Delete\n"
               f"{cmd('autoreply list')} • List configured replies\n"
               f"{cmd('autoreply options')} <name> [filters/cooldown] • Configure filters"),
        inline=False
    )
    embed.add_field(
        name="🧩 Script Triggers",
        value=(f"{cmd('script set')} <name> <pattern> [match_type] • Create/update (opens form)\n"
               f"{cmd('script remove')} <name> • Delete\n"
               f"{cmd('script list')} • List configured scripts\n"
               f"{cmd('script enable')} <name> • Enable a script\n"
               f"{cmd('script disable')} <name> • Disable a script\n"
               f"{cmd('script docs')} • Helper documentation"),
        inline=False
    )
    embed.add_field(
        name="📌 Pin Commands",
        value=(f"{cmd('setpin')} <content> • Set pin-at-bottom\n"
               f"{cmd('removepin')} [message_id] • Remove pin\n"
               f"{cmd('listpins')} • List pins in channel"),
        inline=False
    )
    embed.add_field(
        name="⚡ Reaction Roles",
        value=(f"{cmd('reactionrole')} <message_id> <emoji> <role> • Add\n"
               f"{cmd('removereactionrole')} <message_id> <emoji> • Remove"),
        inline=False
    )
    embed.add_field(
        name="🔨 Moderation",
        value=(f"{cmd('timeout')} <member> <minutes> [reason]\n"
               f"{cmd('untimeout')} <member> [reason]\n"
               f"{cmd('kick')} <member> [reason]\n"
               f"{cmd('ban')} <member> [reason]\n"
               f"{cmd('unban')} <user_id> [reason]\n"
               f"{cmd('slowmode')} <seconds>\n"
               f"{cmd('clear')} <amount>\n"
               f"{cmd('addrole')} <member> <role>\n"
               f"{cmd('removerole')} <member> <role>\n"
               f"{cmd('addroleall')} <role>"),
        inline=False
    )
    embed.add_field(
        name="ℹ️ Snippet Usage",
        value="Use `!trigger` to activate snippets.\n"
              "Reply with `!trigger` to mention the original author.\n"
              "Dynamic snippets support placeholders `{1}`, `{2}`, ...",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =====================================================
# Run
# =====================================================

async def run_service(token: Optional[str]):
    initialize_runtime_state()
    await start_script_manager_api()
    if not token:
        logger.warning("Bot token missing; script manager API is running without Discord connectivity.")
        await asyncio.Event().wait()
        return

    try:
        async with bot:
            await bot.start(token)
    except Exception:
        logger.exception("Discord bot stopped unexpectedly; keeping script manager API online.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    token = load_token()
    asyncio.run(run_service(token))
