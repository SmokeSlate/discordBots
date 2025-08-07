import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import os
from typing import Optional

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
    
    await interaction.response.send_message(f"✅ Snippet `!{trigger}` created successfully!")

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
        await interaction.response.send_message(f"✅ Snippet `!{trigger}` removed successfully!")
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
    
    await interaction.response.send_message(embed=embed)

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
        await interaction.response.send_message(f"✅ Snippet `!{trigger}` updated successfully!")
    else:
        await interaction.response.send_message(f"❌ Snippet `!{trigger}` not found!", ephemeral=True)

# SNIPPET MESSAGE HANDLER
@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
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

# PIN COMMANDS
@bot.tree.command(name="setpin", description="Pin a message to the channel")
@app_commands.describe(message_id="The ID of the message to pin")
async def set_pin(interaction: discord.Interaction, message_id: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
        message = await interaction.channel.fetch_message(msg_id)
        await message.pin()
        await interaction.response.send_message("✅ Message pinned successfully!")
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID!", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to pin messages!", ephemeral=True)
    except discord.HTTPException:
        await interaction.response.send_message("❌ Failed to pin message. Channel might have reached pin limit (50).", ephemeral=True)

@bot.tree.command(name="removepin", description="Remove a pin from a message")
@app_commands.describe(message_id="The ID of the message to unpin")
async def remove_pin(interaction: discord.Interaction, message_id: str):
    if not has_permissions_or_override(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
        message = await interaction.channel.fetch_message(msg_id)
        await message.unpin()
        await interaction.response.send_message("✅ Message unpinned successfully!")
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID!", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to unpin messages!", ephemeral=True)
    except discord.HTTPException:
        await interaction.response.send_message("❌ Failed to unpin message!", ephemeral=True)

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
        
        await interaction.response.send_message(f"✅ Reaction role set! React with {emoji} to get the {role.name} role.")
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
            await interaction.response.send_message("✅ Reaction role removed!")
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
        await interaction.response.send_message(f"✅ {member.mention} has been timed out for {duration} minutes. Reason: {reason}")
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
        await interaction.response.send_message(f"✅ {member.mention} timeout has been removed. Reason: {reason}")
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
        await interaction.response.send_message(f"✅ {member.mention} has been kicked. Reason: {reason}")
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
        await interaction.response.send_message(f"✅ {member.mention} has been banned. Reason: {reason}")
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
        await interaction.response.send_message(f"✅ {user.mention} has been unbanned. Reason: {reason}")
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
            await interaction.response.send_message("✅ Slowmode disabled!")
        else:
            await interaction.response.send_message(f"✅ Slowmode set to {seconds} seconds!")
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
        await interaction.response.send_message(f"✅ Added {role.name} role to {member.mention}!")
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
        await interaction.response.send_message(f"✅ Removed {role.name} role from {member.mention}!")
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
        name="📝 Snippet Commands",
        value="`/addsnippet <trigger> <content>` - Add a new snippet\n`/removesnippet <trigger>` - Remove a snippet\n`/editsnippet <trigger> <content>` - Edit a snippet\n`/listsnippets` - List all snippets",
        inline=False
    )
    
    embed.add_field(
        name="📌 Pin Commands",
        value="`/setpin <message_id>` - Pin a message\n`/removepin <message_id>` - Unpin a message",
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
    
    await interaction.response.send_message(embed=embed)

# Run the bot
if __name__ == "__main__":
    token = load_token()
    if token:
        bot.run(token)
    else:
        print("Failed to load token. Please make sure token.txt exists and contains your bot token.")