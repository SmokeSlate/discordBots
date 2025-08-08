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
import re
from typing import Optional
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

ADMIN_OVERRIDE_ID = 823654955025956895

reaction_roles = {}
ticket_data = {}
snippets = {}  # unified format after migration

# =====================================================
# Permissions Checkers
# =====================================================

def has_permissions_or_override(interaction: discord.Interaction) -> bool:
    return (interaction.user.id == ADMIN_OVERRIDE_ID or
            interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.manage_messages)

def has_mod_permissions_or_override(interaction: discord.Interaction) -> bool:
    return (interaction.user.id == ADMIN_OVERRIDE_ID or
            interaction.user.guild_permissions.administrator or
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

# =====================================================
# Snippet Storage with Migration
# New unified format per guild:
# snippets[guild_id] = {
#   "trigger": {"content": "text with {1}", "dynamic": True/False},
#   ...
# }
# =====================================================

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
                # Ensure required keys exist
                if "content" not in value:
                    raw[guild_id][trigger]["content"] = ""
                    migrated = True
                if "dynamic" not in value:
                    raw[guild_id][trigger]["dynamic"] = False
                    migrated = True

    if migrated:
        write_json('snippets.json', raw)

    snippets = raw

def save_snippets():
    write_json('snippets.json', snippets)

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
# Snippet Commands (with migration + dynamic placeholders)
# =====================================================

@bot.tree.command(name="addsnippet", description="Add a static snippet")
@app_commands.describe(trigger="Trigger word (no !)", content="Content to send")
async def add_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    snippets.setdefault(gid, {})[trigger] = {"content": content, "dynamic": False}
    save_snippets()
    await interaction.response.send_message(f"✅ Static snippet `!{trigger}` added.", ephemeral=True)

@bot.tree.command(name="adddynamicsnippet", description="Add a dynamic snippet with placeholders {1}, {2}...")
@app_commands.describe(trigger="Trigger word (no !)", content="Content with placeholders like {1}, {2}, ...")
async def add_dynamic_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    trigger = trigger.lstrip("!")
    gid = str(interaction.guild.id)
    snippets.setdefault(gid, {})[trigger] = {"content": content, "dynamic": True}
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
        snippets[gid][trigger]["content"] = content
        snippets[gid][trigger]["dynamic"] = False
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
        snippets[gid][trigger]["content"] = content
        snippets[gid][trigger]["dynamic"] = bool(dynamic)
        save_snippets()
        return await interaction.response.send_message(f"✅ Dynamic snippet `!{trigger}` updated.", ephemeral=True)
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
# on_message handler (pins → then snippets with placeholders)
# =====================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Pinned message reposting
    await handle_pin_repost(message)

    # Snippet handling (messages starting with "!")
    if not message.content.startswith("!"):
        return

    gid = str(message.guild.id)
    parts = message.content.split()
    if not parts:
        return

    trigger = parts[0][1:]

    if gid in snippets and trigger in snippets[gid]:
        entry = snippets[gid][trigger]
        content = entry["content"]
        is_dynamic = entry.get("dynamic", False)

        # Delete the original trigger message to keep channels clean
        try:
            await message.delete()
        except discord.Forbidden:
            pass

        if is_dynamic:
            # Replace placeholders {1}, {2}, ... globally.
            args = parts[1:]
            for i, arg in enumerate(args, start=1):
                content = content.replace(f"{{{i}}}", arg)
            # Remove any unused {n} placeholders
            content = re.sub(r"\{\d+\}", "", content)

        # If the snippet was used as a reply, mention the original author first.
        if message.reference and message.reference.message_id:
            try:
                replied = await message.channel.fetch_message(message.reference.message_id)
                content = f"{replied.author.mention} {content}"
            except discord.NotFound:
                pass

        try:
            await message.channel.send(content)
        except discord.Forbidden:
            await message.channel.send(f"⚠️ I don't have permission to send messages! Snippet would be: {content}")

    # Keep slash commands working
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

            pin_content = f"📌 **PINNED MESSAGE**\n\n{current_pin['content']}"
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

        pin_content = f"📌 **PINNED MESSAGE**\n\n{content}"
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

@bot.tree.command(name="clear", description="Delete a specified number of messages")
@app_commands.describe(amount="Number of messages to delete (max 100)")
async def clear_messages(interaction: discord.Interaction, amount: int):
    if not has_mod_permissions_or_override(interaction):
        return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    try:
        if amount > 100:
            return await interaction.response.send_message("❌ Cannot delete more than 100 messages at once!", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        sleep(0.1)
        await interaction.followup.send(f"✅ Deleted {len(deleted)} messages!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to delete messages!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Failed to delete messages: {e}", ephemeral=True)

# =====================================================
# Help Command
# =====================================================

@bot.tree.command(name="help", description="Display all available commands")
async def help_mod(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Bot Commands",
        description="Here are all available slash commands:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🎫 Ticket System",
        value="`/ticket` • Post ticket menu\n"
              "`/listtickets` • List open tickets\n"
              "`/ticketstats` • Ticket stats\n"
              "`/addticketcategory` • Add category\n"
              "`/removeticketcategory` • Remove category\n"
              "`/listticketcategories` • List categories",
        inline=False
    )
    embed.add_field(
        name="📝 Snippet Commands",
        value="`/addsnippet <trigger> <content>` • Add static\n"
              "`/adddynamicsnippet <trigger> <content>` • Add dynamic with {1},{2},...\n"
              "`/editsnippet <trigger> <content>` • Edit static\n"
              "`/editdynamicsnippet <trigger> <content> [dynamic]` • Edit/toggle dynamic\n"
              "`/listsnippets` • List all snippets",
        inline=False
    )
    embed.add_field(
        name="📌 Pin Commands",
        value="`/setpin <content>` • Set pin-at-bottom\n"
              "`/removepin [message_id]` • Remove pin\n"
              "`/listpins` • List pins in channel",
        inline=False
    )
    embed.add_field(
        name="⚡ Reaction Roles",
        value="`/reactionrole <message_id> <emoji> <role>` • Add\n"
              "`/removereactionrole <message_id> <emoji>` • Remove",
        inline=False
    )
    embed.add_field(
        name="🔨 Moderation",
        value="`/timeout <member> <minutes> [reason]`\n"
              "`/untimeout <member> [reason]`\n"
              "`/kick <member> [reason]`\n"
              "`/ban <member> [reason]`\n"
              "`/unban <user_id> [reason]`\n"
              "`/slowmode <seconds>`\n"
              "`/clear <amount>`",
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