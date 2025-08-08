import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import os
from typing import Optional
from io import BytesIO

# Load token from file
def load_token():
    try:
        with open('token.txt', 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        print("Error: token.txt file not found!")
        return None

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Special user ID with override permissions
ADMIN_OVERRIDE_ID = 823654955025956895

# Data storage
reaction_roles = {}
snippets = {}
ticket_data = {}

def save_reaction_roles():
    """Save reaction roles to file"""
    with open('reaction_roles.json', 'w') as f:
        json.dump(reaction_roles, f, indent=2)

def load_reaction_roles():
    """Load reaction roles from file"""
    global reaction_roles
    try:
        with open('reaction_roles.json', 'r') as f:
            reaction_roles = json.load(f)
    except FileNotFoundError:
        reaction_roles = {}

def save_snippets():
    """Save snippets to file"""
    with open('snippets.json', 'w') as f:
        json.dump(snippets, f, indent=2)

def load_snippets():
    """Load snippets from file"""
    global snippets
    try:
        with open('snippets.json', 'r') as f:
            snippets = json.load(f)
    except FileNotFoundError:
        snippets = {}

def save_ticket_data():
    """Save ticket data to file"""
    with open('ticket_data.json', 'w') as f:
        json.dump(ticket_data, f, indent=2)

def load_ticket_data():
    """Load ticket data from file"""
    global ticket_data
    try:
        with open('ticket_data.json', 'r') as f:
            ticket_data = json.load(f)
    except FileNotFoundError:
        ticket_data = {}

def has_permissions_or_override(interaction: discord.Interaction) -> bool:
    """Check if user has admin permissions or is the override user"""
    return (interaction.user.id == ADMIN_OVERRIDE_ID or 
            interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.manage_messages)

def has_mod_permissions_or_override(interaction: discord.Interaction) -> bool:
    """Check if user has moderation permissions or is the override user"""
    return (interaction.user.id == ADMIN_OVERRIDE_ID or 
            interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.moderate_members or
            interaction.user.guild_permissions.ban_members or
            interaction.user.guild_permissions.kick_members)

def load_pinned_messages():
    """Load pinned messages from file"""
    try:
        with open('pinned_messages.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    load_reaction_roles()
    load_snippets()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# Re-pin messages when bot starts (optional feature)
async def repin_messages():
    """Re-pin messages that should stay at bottom"""
    pinned_data = load_pinned_messages()
    for pin_id, data in pinned_data.items():
        try:
            guild = bot.get_guild(int(data['guild_id']))
            if guild:
                channel = guild.get_channel(int(data['channel_id']))
                if channel:
                    # Check if pinned message still exists
                    try:
                        await channel.fetch_message(int(pin_id))
                    except discord.NotFound:
                        # Message was deleted, remove from tracking
                        del pinned_data[pin_id]
        except:
            continue
    
    # Save cleaned data
    with open('pinned_messages.json', 'w') as f:
        json.dump(pinned_data, f, indent=2)

# SNIPPET COMMANDS
@bot.tree.command(name="addsnippet", description="Add a new snippet")
@app_commands.describe(
    trigger="The trigger word (without !)",
    content="The content to send when triggered"
)
async def add_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    # Remove ! if user included it
    trigger = trigger.lstrip('!')
    
    guild_id = str(interaction.guild.id)
    if guild_id not in snippets:
        snippets[guild_id] = {}
    
    snippets[guild_id][trigger] = content
    save_snippets()
    
    await interaction.response.send_message(f"✅ Snippet `!{trigger}` created successfully!", ephemeral=True)

@bot.tree.command(name="removesnippet", description="Remove a snippet")
@app_commands.describe(trigger="The trigger word to remove (without !)")
async def remove_snippet(interaction: discord.Interaction, trigger: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    # Remove ! if user included it
    trigger = trigger.lstrip('!')
    
    guild_id = str(interaction.guild.id)
    if guild_id in snippets and trigger in snippets[guild_id]:
        del snippets[guild_id][trigger]
        save_snippets()
        await interaction.response.send_message(f"✅ Snippet `!{trigger}` removed successfully!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Snippet `!{trigger}` not found!", ephemeral=True)

@bot.tree.command(name="listsnippets", description="List all snippets")
async def list_snippets(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    
    if guild_id not in snippets or not snippets[guild_id]:
        await interaction.response.send_message("No snippets found for this server!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📝 Available Snippets",
        description="Here are all the snippets for this server:",
        color=discord.Color.green()
    )
    
    snippet_list = []
    for trigger, content in snippets[guild_id].items():
        # Truncate long content for display
        display_content = content[:50] + "..." if len(content) > 50 else content
        snippet_list.append(f"`!{trigger}` - {display_content}")
    
    # Split into chunks if too many snippets
    chunk_size = 10
    for i in range(0, len(snippet_list), chunk_size):
        chunk = snippet_list[i:i+chunk_size]
        embed.add_field(
            name=f"Snippets {i//chunk_size + 1}",
            value="\n".join(chunk),
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="editsnippet", description="Edit an existing snippet")
@app_commands.describe(
    trigger="The trigger word to edit (without !)",
    content="The new content for the snippet"
)
async def edit_snippet(interaction: discord.Interaction, trigger: str, content: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    # Remove ! if user included it
    trigger = trigger.lstrip('!')
    
    guild_id = str(interaction.guild.id)
    if guild_id in snippets and trigger in snippets[guild_id]:
        snippets[guild_id][trigger] = content
        save_snippets()
        await interaction.response.send_message(f"✅ Snippet `!{trigger}` updated successfully!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Snippet `!{trigger}` not found!", ephemeral=True)

# TICKET SYSTEM
class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🛠️ Technical Support",
                description="Get help with technical issues",
                emoji="🛠️",
                value="tech_support"
            ),
            discord.SelectOption(
                label="❓ General Questions",
                description="Ask general questions",
                emoji="❓",
                value="general_question"
            ),
            discord.SelectOption(
                label="🚨 Report Issue",
                description="Report a problem or bug",
                emoji="🚨",
                value="report_issue"
            ),
            discord.SelectOption(
                label="💡 Feature Request",
                description="Suggest a new feature",
                emoji="💡",
                value="feature_request"
            ),
            discord.SelectOption(
                label="👥 Staff Application",
                description="Apply to join the staff team",
                emoji="👥",
                value="staff_application"
            ),
            discord.SelectOption(
                label="📋 Other",
                description="Something else not listed above",
                emoji="📋",
                value="other"
            )
        ]
        super().__init__(placeholder="Choose a ticket category...", options=options, min_values=1, max_values=1)
    
    async def callback(self, interaction: discord.Interaction):
        category_names = {
            "tech_support": "🛠️ Technical Support",
            "general_question": "❓ General Questions", 
            "report_issue": "🚨 Report Issue",
            "feature_request": "💡 Feature Request",
            "staff_application": "👥 Staff Application",
            "other": "📋 Other"
        }
        
        selected_category = self.values[0]
        category_display = category_names.get(selected_category, "Unknown")
        
        # Check if user already has an open ticket
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        
        if guild_id in ticket_data:
            for thread_id, data in ticket_data[guild_id].items():
                if data['user_id'] == user_id and data['status'] == 'open':
                    thread = interaction.guild.get_thread(int(thread_id))
                    if thread:
                        await interaction.response.send_message(
                            f"❌ You already have an open ticket: {thread.mention}", 
                            ephemeral=True
                        )
                        return
        
        # Create private thread
        try:
            thread_name = f"{category_display} - {interaction.user.display_name}"
            thread = await interaction.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                reason=f"Ticket created by {interaction.user}"
            )
            
            # Add user to thread
            await thread.add_user(interaction.user)
            
            # Store ticket data
            if guild_id not in ticket_data:
                ticket_data[guild_id] = {}
            
            ticket_data[guild_id][str(thread.id)] = {
                'user_id': user_id,
                'category': selected_category,
                'status': 'open',
                'created_at': discord.utils.utcnow().isoformat(),
                'channel_id': str(interaction.channel.id)
            }
            save_ticket_data()
            
            # Send welcome message in thread
            embed = discord.Embed(
                title=f"🎫 New Ticket - {category_display}",
                description=f"Hello {interaction.user.mention}! Thank you for creating a ticket.\n\n"
                           f"**Category:** {category_display}\n"
                           f"**Status:** Open\n\n"
                           f"Please describe your issue or question in detail. A staff member will assist you shortly!",
                color=discord.Color.green()
            )
            
            view = TicketControlView()
            await thread.send(embed=embed, view=view)
            
            await interaction.response.send_message(
                f"✅ Ticket created! {thread.mention}", 
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
        # Check if user has permission to close (ticket owner or staff)
        guild_id = str(interaction.guild.id)
        thread_id = str(interaction.channel.id)
        
        if guild_id not in ticket_data or thread_id not in ticket_data[guild_id]:
            await interaction.response.send_message("❌ Ticket data not found!", ephemeral=True)
            return
        
        ticket_info = ticket_data[guild_id][thread_id]
        is_ticket_owner = str(interaction.user.id) == ticket_info['user_id']
        is_staff = has_mod_permissions_or_override(interaction)
        
        if not (is_ticket_owner or is_staff):
            await interaction.response.send_message("❌ You don't have permission to close this ticket!", ephemeral=True)
            return
        
        # Close the ticket
        ticket_data[guild_id][thread_id]['status'] = 'closed'
        ticket_data[guild_id][thread_id]['closed_at'] = discord.utils.utcnow().isoformat()
        ticket_data[guild_id][thread_id]['closed_by'] = str(interaction.user.id)
        save_ticket_data()
        
        # Update embed
        embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=f"This ticket has been closed by {interaction.user.mention}.\n"
                       f"Closed at: <t:{int(discord.utils.utcnow().timestamp())}:f>",
            color=discord.Color.red()
        )
        
        await interaction.response.send_message(embed=embed)
        
        # Archive the thread after a short delay
        await asyncio.sleep(5)
        try:
            await interaction.channel.edit(archived=True, locked=True)
        except discord.Forbidden:
            pass
    
    @discord.ui.button(label="📋 Add Note", style=discord.ButtonStyle.secondary, custom_id="add_note")
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_mod_permissions_or_override(interaction):
            await interaction.response.send_message("❌ Only staff can add notes to tickets!", ephemeral=True)
            return
        
        modal = TicketNoteModal()
        await interaction.response.send_modal(modal)

class TicketNoteModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Add Ticket Note")
    
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
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Added by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ticket", description="Create a ticket menu")
async def create_ticket_menu(interaction: discord.Interaction):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Need help? Create a support ticket by selecting a category below!\n\n"
                   "**Available Categories:**\n"
                   "🛠️ **Technical Support** - Get help with technical issues\n"
                   "❓ **General Questions** - Ask general questions\n"
                   "🚨 **Report Issue** - Report a problem or bug\n"
                   "💡 **Feature Request** - Suggest a new feature\n"
                   "👥 **Staff Application** - Apply to join the staff team\n"
                   "📋 **Other** - Something else not listed above\n\n"
                   "*Your ticket will be created as a private thread that only you and staff can see.*",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Select a category from the dropdown menu below")
    
    view = TicketMenuView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="ticketstats", description="View ticket statistics")
async def ticket_stats(interaction: discord.Interaction):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    guild_id = str(interaction.guild.id)
    
    if guild_id not in ticket_data or not ticket_data[guild_id]:
        await interaction.response.send_message("❌ No ticket data found for this server!", ephemeral=True)
        return
    
    tickets = ticket_data[guild_id]
    total_tickets = len(tickets)
    open_tickets = sum(1 for t in tickets.values() if t['status'] == 'open')
    closed_tickets = sum(1 for t in tickets.values() if t['status'] == 'closed')
    
    # Count by category
    categories = {}
    for ticket in tickets.values():
        category = ticket['category']
        categories[category] = categories.get(category, 0) + 1
    
    category_text = ""
    category_names = {
        "tech_support": "🛠️ Technical Support",
        "general_question": "❓ General Questions",
        "report_issue": "🚨 Report Issue", 
        "feature_request": "💡 Feature Request",
        "staff_application": "👥 Staff Application",
        "other": "📋 Other"
    }
    
    for cat, count in categories.items():
        cat_name = category_names.get(cat, cat)
        category_text += f"{cat_name}: {count}\n"
    
    embed = discord.Embed(
        title="🎫 Ticket Statistics",
        color=discord.Color.blue()
    )
    embed.add_field(name="📊 Overview", value=f"**Total Tickets:** {total_tickets}\n**Open:** {open_tickets}\n**Closed:** {closed_tickets}", inline=False)
    
    if category_text:
        embed.add_field(name="📋 By Category", value=category_text, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="listtickets", description="List all open tickets")
async def list_tickets(interaction: discord.Interaction):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    guild_id = str(interaction.guild.id)
    
    if guild_id not in ticket_data:
        await interaction.response.send_message("❌ No tickets found for this server!", ephemeral=True)
        return
    
    open_tickets = []
    category_names = {
        "tech_support": "🛠️ Technical Support",
        "general_question": "❓ General Questions",
        "report_issue": "🚨 Report Issue",
        "feature_request": "💡 Feature Request", 
        "staff_application": "👥 Staff Application",
        "other": "📋 Other"
    }
    
    for thread_id, data in ticket_data[guild_id].items():
        if data['status'] == 'open':
            thread = interaction.guild.get_thread(int(thread_id))
            if thread:
                user = bot.get_user(int(data['user_id']))
                category = category_names.get(data['category'], data['category'])
                user_name = user.display_name if user else "Unknown User"
                
                created_timestamp = int(discord.utils.parse_time(data['created_at']).timestamp())
                open_tickets.append(f"{thread.mention} - {category}\n👤 {user_name} • <t:{created_timestamp}:R>")
    
    if not open_tickets:
        await interaction.response.send_message("✅ No open tickets found!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🎫 Open Tickets",
        description="\n\n".join(open_tickets[:10]),  # Limit to 10 to avoid embed limits
        color=discord.Color.green()
    )
    
    if len(open_tickets) > 10:
        embed.set_footer(text=f"Showing 10 of {len(open_tickets)} open tickets")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# SNIPPET MESSAGE HANDLER & PIN REPOSTING
@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Handle pinned message reposting first
    await handle_pin_repost(message)
    
    # Then handle snippets
    # Check if message starts with !
    if not message.content.startswith('!'):
        return
    
    # Extract the trigger word
    parts = message.content.split()
    if not parts:
        return
    
    trigger = parts[0][1:]  # Remove the !
    guild_id = str(message.guild.id)
    
    # Check if snippet exists
    if guild_id in snippets and trigger in snippets[guild_id]:
        try:
            # Delete the original message
            await message.delete()
            
            # Get snippet content
            content = snippets[guild_id][trigger]
            
            # Check if this is a reply to another message
            if message.reference and message.reference.message_id:
                try:
                    replied_message = await message.channel.fetch_message(message.reference.message_id)
                    # Mention the author of the replied message
                    content = f"{replied_message.author.mention} {content}"
                except discord.NotFound:
                    pass  # Original message was deleted, continue without mention
            
            # Send the snippet content
            await message.channel.send(content)
            
        except discord.Forbidden:
            # Bot doesn't have permission to delete messages
            await message.channel.send(f"⚠️ I don't have permission to delete messages! Snippet content: {content}")
        except discord.NotFound:
            # Message was already deleted
            pass

async def handle_pin_repost(message):
    """Handle reposting pinned messages when new messages are sent"""
    try:
        channel_id = str(message.channel.id)
        
        # Load pinned messages
        try:
            with open('pinned_messages.json', 'r') as f:
                pinned_messages = json.load(f)
        except FileNotFoundError:
            return
        
        # Check if this channel has a pinned message
        current_pin = None
        current_pin_id = None
        
        for pin_id, data in pinned_messages.items():
            if data['channel_id'] == channel_id:
                current_pin = data
                current_pin_id = pin_id
                break
        
        if current_pin:
            # Delete the old pinned message
            try:
                old_message = await message.channel.fetch_message(int(current_pin_id))
                await old_message.delete()
            except discord.NotFound:
                pass  # Message already deleted
            
            # Create new pinned message at bottom
            pin_content = f"📌 **PINNED MESSAGE**\n\n{current_pin['content']}"
            new_pinned_msg = await message.channel.send(pin_content)
            
            # Update the tracking with new message ID
            del pinned_messages[current_pin_id]
            pinned_messages[str(new_pinned_msg.id)] = current_pin
            
            # Save updated data
            with open('pinned_messages.json', 'w') as f:
                json.dump(pinned_messages, f, indent=2)
                
    except discord.Forbidden:
        # Bot doesn't have permission to manage messages
        pass
    except Exception as e:
        # Log error but don't break normal message flow
        print(f"Error handling pin repost: {e}")

# PIN COMMANDS
@bot.tree.command(name="setpin", description="Set a pinned message that stays at the bottom of the channel")
@app_commands.describe(content="The text content for the pinned message")
async def set_pin(interaction: discord.Interaction, content: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)
        
        # Load existing pinned messages
        try:
            with open('pinned_messages.json', 'r') as f:
                pinned_messages = json.load(f)
        except FileNotFoundError:
            pinned_messages = {}
        
        # Remove existing pinned message in this channel if it exists
        for pin_id, data in list(pinned_messages.items()):
            if data['channel_id'] == channel_id:
                try:
                    old_message = await interaction.channel.fetch_message(int(pin_id))
                    await old_message.delete()
                except discord.NotFound:
                    pass  # Message already deleted
                del pinned_messages[pin_id]
        
        # Create new pinned message
        pin_content = f"📌 **PINNED MESSAGE**\n\n{content}"
        pinned_msg = await interaction.channel.send(pin_content)
        
        # Store pinned message info
        pinned_data = {
            'content': content,
            'channel_id': channel_id,
            'guild_id': guild_id,
            'author_id': interaction.user.id
        }
        
        pinned_messages[str(pinned_msg.id)] = pinned_data
        
        # Save updated data
        with open('pinned_messages.json', 'w') as f:
            json.dump(pinned_messages, f, indent=2)
        
        await interaction.response.send_message("✅ Pinned message set! It will stay at the bottom of the channel.", ephemeral=True)
        
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to send messages!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to set pinned message: {e}", ephemeral=True)

@bot.tree.command(name="removepin", description="Remove a pinned message from the bottom")
async def list_pins(interaction: discord.Interaction):
    try:
        with open('pinned_messages.json', 'r') as f:
            pinned_messages = json.load(f)
    except FileNotFoundError:
        await interaction.response.send_message("❌ No pinned messages found!", ephemeral=True)
        return
    
    channel_pins = []
    for pin_id, data in pinned_messages.items():
        if int(data['channel_id']) == interaction.channel.id:
            channel_pins.append(f"• Message ID: `{pin_id}`")
    
    if not channel_pins:
        await interaction.response.send_message("❌ No pinned messages in this channel!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📌 Pinned Messages in this Channel",
        description="\n".join(channel_pins),
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
@app_commands.describe(message_id="The ID of the pinned message to remove")
async def remove_pin(interaction: discord.Interaction, message_id: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
        
        # Load pinned messages data
        try:
            with open('pinned_messages.json', 'r') as f:
                pinned_messages = json.load(f)
        except FileNotFoundError:
            await interaction.response.send_message("❌ No pinned messages found!", ephemeral=True)
            return
        
        # Check if this is a pinned message
        if str(msg_id) in pinned_messages:
            # Delete the pinned message
            message = await interaction.channel.fetch_message(msg_id)
            await message.delete()
            
            # Remove from tracking
            del pinned_messages[str(msg_id)]
            
            # Save updated data
            with open('pinned_messages.json', 'w') as f:
                json.dump(pinned_messages, f, indent=2)
            
            await interaction.response.send_message("✅ Pinned message removed!")
        else:
            await interaction.response.send_message("❌ This is not a tracked pinned message!", ephemeral=True)
            
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID!", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to delete messages!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to remove pinned message: {e}", ephemeral=True)

@bot.tree.command(name="listpins", description="Show the pinned message for this channel")
async def list_pins(interaction: discord.Interaction):
    try:
        with open('pinned_messages.json', 'r') as f:
            pinned_messages = json.load(f)
    except FileNotFoundError:
        await interaction.response.send_message("❌ No pinned messages found!", ephemeral=True)
        return
    
    channel_id = str(interaction.channel.id)
    
    # Find pinned message for this channel
    for pin_id, data in pinned_messages.items():
        if data['channel_id'] == channel_id:
            author = bot.get_user(data['author_id'])
            author_name = author.display_name if author else "Unknown User"
            
            embed = discord.Embed(
                title="📌 Current Pinned Message",
                description=data['content'],
                color=discord.Color.blue()
            )
            embed.add_field(name="Set by", value=author_name, inline=True)
            embed.add_field(name="Message ID", value=pin_id, inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    
    await interaction.response.send_message("❌ No pinned message in this channel!", ephemeral=True)

# REACTION ROLES COMMANDS
@bot.tree.command(name="reactionrole", description="Add a reaction role to a message")
@app_commands.describe(
    message_id="The ID of the message to add reaction role to",
    emoji="The emoji to react with",
    role="The role to give when reacting"
)
async def reaction_role(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
        message = await interaction.channel.fetch_message(msg_id)
        await message.add_reaction(emoji)
        
        # Store reaction role mapping
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
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
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

# MODERATION COMMANDS
@bot.tree.command(name="timeout", description="Timeout a member for specified minutes")
@app_commands.describe(
    member="The member to timeout",
    duration="Duration in minutes",
    reason="Reason for the timeout"
)
async def timeout_member(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        await member.timeout(discord.utils.utcnow() + discord.timedelta(minutes=duration), reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} has been timed out for {duration} minutes. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to timeout members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to timeout member: {e}", ephemeral=True)

@bot.tree.command(name="untimeout", description="Remove timeout from a member")
@app_commands.describe(
    member="The member to remove timeout from",
    reason="Reason for removing timeout"
)
async def untimeout_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        await member.timeout(None, reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} timeout has been removed. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage timeouts!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to remove timeout: {e}", ephemeral=True)

@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(
    member="The member to kick",
    reason="Reason for the kick"
)
async def kick_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} has been kicked. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to kick members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to kick member: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(
    member="The member to ban",
    reason="Reason for the ban"
)
async def ban_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"✅ {member.mention} has been banned. Reason: {reason}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to ban members!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to ban member: {e}", ephemeral=True)

@bot.tree.command(name="unban", description="Unban a user from the server")
@app_commands.describe(
    user_id="The ID of the user to unban",
    reason="Reason for the unban"
)
async def unban_member(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
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
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
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
@app_commands.describe(
    member="The member to add the role to",
    role="The role to add",
    reason="Reason for adding the role"
)
async def add_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        await member.add_roles(role, reason=reason)
        await interaction.response.send_message(f"✅ Added {role.name} role to {member.mention}!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage roles!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to add role: {e}", ephemeral=True)

@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.describe(
    member="The member to remove the role from",
    role="The role to remove",
    reason="Reason for removing the role"
)
async def remove_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason provided"):
    if not has_mod_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
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
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        if amount > 100:
            await interaction.response.send_message("❌ Cannot delete more than 100 messages at once!", ephemeral=True)
            return
        
        await interaction.response.defer()
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"✅ Deleted {len(deleted)} messages!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to delete messages!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Failed to delete messages: {e}", ephemeral=True)

# REACTION ROLE EVENT HANDLERS
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

# UTILITY COMMANDS
@bot.tree.command(name="help", description="Display all available commands")
async def help_mod(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Bot Commands",
        description="Here are all available slash commands:",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="🎫 Ticket System",
        value="`/ticket` - Create ticket menu\n`/listtickets` - List open tickets\n`/ticketstats` - View ticket statistics",
        inline=False
    )
    
    embed.add_field(
        name="📝 Snippet Commands",
        value="`/addsnippet <trigger> <content>` - Add a new snippet\n`/removesnippet <trigger>` - Remove a snippet\n`/editsnippet <trigger> <content>` - Edit a snippet\n`/listsnippets` - List all snippets",
        inline=False
    )
    
    embed.add_field(
        name="📌 Pin Commands",
        value="`/setpin <content>` - Set pinned message at bottom\n`/removepin` - Remove pinned message\n`/listpins` - Show current pinned message",
        inline=False
    )
    
    embed.add_field(
        name="⚡ Reaction Roles",
        value="`/reactionrole <message_id> <emoji> <role>` - Add reaction role\n`/removereactionrole <message_id> <emoji>` - Remove reaction role",
        inline=False
    )
    
    embed.add_field(
        name="🔨 Moderation",
        value="`/timeout <member> <minutes> [reason]` - Timeout member\n`/untimeout <member> [reason]` - Remove timeout\n`/kick <member> [reason]` - Kick member\n`/ban <member> [reason]` - Ban member\n`/unban <user_id> [reason]` - Unban user\n`/slowmode <seconds>` - Set slowmode\n`/clear <amount>` - Delete messages",
        inline=False
    )
    
    embed.add_field(
        name="👥 Role Management",
        value="`/addrole <member> <role> [reason]` - Add role to member\n`/removerole <member> <role> [reason]` - Remove role from member",
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ Snippet Usage",
        value="Use `!trigger` to activate snippets. Reply to a message with `!trigger` to mention the original author.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Run the bot
if __name__ == "__main__":
    token = load_token()
    if token:
        bot.run(token)
    else:
        print("Failed to load token. Please make sure token.txt exists and contains your bot token.")