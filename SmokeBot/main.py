# =====================================================
# Discord Bot
# - Custom ticket categories (JSON storage + slash cmds)
# - No "used /ticket" banner (ephemeral confirmations)
# - Pins, reaction roles, moderation, help
# - Snippet system (static & dynamic with placeholders)
# - Snippet migration to new JSON format
# =====================================================

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import os
import random
import re
from typing import Optional, Tuple, List
from datetime import datetime, timedelta

# =====================================================
# Utility functions for data handling
# =====================================================

def read_json(path, default_factory):
    """Read JSON file, creating it with default content if missing."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        data = default_factory() if callable(default_factory) else default_factory
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        return data

def write_json(path, data):
    """Write JSON to disk with pretty formatting."""
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

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

reaction_roles = {}
ticket_data = {}
snippets = {}  # unified format after migration
auto_replies = {}
autoreply_cooldowns = {}
giveaways = {}

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
    matches = list(re.finditer(r"(\d+)([smhd])", text))
    if not matches:
        return None, "Use formats like 30s, 5m, 2h, or 1d (combinable)."

    for match in matches:
        amount = int(match.group(1))
        unit = match.group(2)
        multiplier = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
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

    try:
        await message.channel.send(content)
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

    for guild_id, replies in raw.items():
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
    match: re.Match,
) -> str:
    content = entry.get("response", "")

    if entry.get("dynamic"):
        groups = match.groups()
        for i, value in enumerate(groups, start=1):
            content = content.replace(f"{{{i}}}", value)
        content = re.sub(r"\{\d+\}", "", content)

    if "{ping}" in content:
        content = content.replace("{ping}", message.author.mention)

    return content


async def dispatch_auto_reply(
    message: discord.Message,
    name: str,
    entry: dict,
    match: re.Match,
) -> bool:
    if not message.guild:
        return False

    if not auto_reply_restrictions_pass(entry, message):
        return False

    gid = str(message.guild.id)
    bucket, seconds = determine_autoreply_cooldown_bucket(entry, message)
    if autoreply_on_cooldown(gid, name, bucket, seconds):
        return False

    content = await render_auto_reply_content(message, entry, match)
    if not content:
        return False

    try:
        await message.channel.send(content)
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False

    mark_autoreply_cooldown(gid, name, bucket)
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

# =====================================================
# Ready Event (register persistent views, sync commands)
# =====================================================

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')

    load_reaction_roles()
    load_ticket_data()
    load_snippets()
    load_auto_replies()
    load_giveaways()

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

@bot.tree.command(name="editsnippet", description="Edit a static snippet")
@app_commands.describe(trigger="Trigger word (no !)", content="New content")
async def edit_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    if gid in snippets and trigger in snippets[gid]:
        entry = ensure_snippet_defaults(snippets[gid][trigger])
        entry["content"] = content
        entry["dynamic"] = False
        snippets[gid][trigger] = entry
        save_snippets()
        return await interaction.response.send_message(f"✅ Snippet `!{trigger}` updated.", ephemeral=True)
    await interaction.response.send_message("❌ Snippet not found.", ephemeral=True)

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
    name="advancededitsnippet",
    description="Open a modal to edit snippet content with multiline support",
)
@app_commands.describe(
    trigger="Trigger word (no !)",
    dynamic="Optional override for dynamic mode (leave blank to keep current)",
)
async def advanced_edit_snippet(
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


@autoreply_group.command(name="set", description="Create or update an auto reply triggered by regex")
@app_commands.describe(
    name="Name of the auto reply",
    pattern="Regex pattern to match messages",
    response="Message to send when the pattern matches",
    dynamic="Treat capture groups as {1}, {2}, ... in the response",
)
async def set_autoreply(
    interaction: discord.Interaction,
    name: str,
    pattern: str,
    response: str,
    dynamic: Optional[bool] = None,
):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    name = name.strip()
    if not name:
        return await interaction.response.send_message("❌ Auto reply name cannot be empty.", ephemeral=True)

    pattern = pattern.strip()
    if not pattern:
        return await interaction.response.send_message("❌ Regex pattern cannot be empty.", ephemeral=True)

    if not response:
        return await interaction.response.send_message("❌ Response cannot be empty.", ephemeral=True)

    try:
        re.compile(pattern)
    except re.error as exc:
        return await interaction.response.send_message(f"❌ Invalid regex: {exc}", ephemeral=True)

    gid = str(interaction.guild.id)
    guild_replies = auto_replies.setdefault(gid, {})
    existed = name in guild_replies
    entry = ensure_autoreply_defaults(guild_replies.get(name, {}))

    entry["pattern"] = pattern
    entry["response"] = response
    entry["dynamic"] = entry.get("dynamic", False) if dynamic is None else bool(dynamic)

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
        summary = [f"Pattern: `{pattern}`", dynamic_flag]
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


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Pinned message reposting
    await handle_pin_repost(message)

    if message.guild:
        gid = str(message.guild.id)
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
               f"{cmd('editsnippet')} <trigger> <content> • Edit static\n"
               f"{cmd('editdynamicsnippet')} <trigger> <content> [dynamic] • Edit/toggle dynamic\n"
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

if __name__ == "__main__":
    token = load_token()
    if token:
        bot.run(token)
    else:
        print("Failed to load token. Please make sure token.txt exists and contains your bot token.")
