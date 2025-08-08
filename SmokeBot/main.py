import os
import json

# Folder structure
folders = [
    "bot",
    "bot/utils",
    "bot/modules",
    "bot/data"
]

# Files to create with content
files = {
    # ===== main.py =====
    "bot/main.py": """import discord
from discord.ext import commands
import os
from config import load_token
from utils.fileio import ensure_data_files

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} connected to Discord!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# Load all modules dynamically
for filename in os.listdir("./modules"):
    if filename.endswith(".py") and filename != "__init__.py":
        bot.load_extension(f"modules.{filename[:-3]}")

if __name__ == "__main__":
    ensure_data_files()
    token = load_token()
    if token:
        bot.run(token)
    else:
        print("Token not found. Please check token.txt")
""",

    # ===== config.py =====
    "bot/config.py": """import os

ADMIN_OVERRIDE_ID = 823654955025956895

def load_token():
    try:
        with open('token.txt', 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
""",

    # ===== utils/permissions.py =====
    "bot/utils/permissions.py": """from config import ADMIN_OVERRIDE_ID
import discord

def has_permissions_or_override(interaction: discord.Interaction) -> bool:
    return (
        interaction.user.id == ADMIN_OVERRIDE_ID or
        interaction.user.guild_permissions.administrator or
        interaction.user.guild_permissions.manage_messages
    )

def has_mod_permissions_or_override(interaction: discord.Interaction) -> bool:
    return (
        interaction.user.id == ADMIN_OVERRIDE_ID or
        interaction.user.guild_permissions.administrator or
        interaction.user.guild_permissions.moderate_members or
        interaction.user.guild_permissions.ban_members or
        interaction.user.guild_permissions.kick_members
    )
""",

    # ===== utils/fileio.py =====
    "bot/utils/fileio.py": """import json
import os

DATA_FILES = {
    "reaction_roles.json": {},
    "snippets.json": {},
    "ticket_data.json": {},
    "pinned_messages.json": {},
    "ticket_categories.json": {}
}

def ensure_data_files():
    os.makedirs("data", exist_ok=True)
    for filename, default_data in DATA_FILES.items():
        path = os.path.join("data", filename)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump(default_data, f, indent=2)

def load_json(filename):
    path = os.path.join("data", filename)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_json(filename, data):
    path = os.path.join("data", filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
""",

    # ===== modules/snippets.py =====
    "bot/modules/snippets.py": """import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import has_permissions_or_override
from utils.fileio import load_json, save_json

snippets = load_json("snippets.json")

class Snippets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="addsnippet", description="Add a new snippet")
    async def add_snippet(self, interaction, trigger: str, content: str):
        if not has_permissions_or_override(interaction):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        guild_id = str(interaction.guild.id)
        if guild_id not in snippets:
            snippets[guild_id] = {}
        snippets[guild_id][trigger] = content
        save_json("snippets.json", snippets)
        await interaction.response.send_message(f"✅ Snippet `!{trigger}` created.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.content.startswith("!"):
            return
        trigger = message.content[1:]
        guild_id = str(message.guild.id)
        if guild_id in snippets and trigger in snippets[guild_id]:
            await message.delete()
            await message.channel.send(snippets[guild_id][trigger])

async def setup(bot):
    await bot.add_cog(Snippets(bot))
"""
}

# Create folders
for folder in folders:
    os.makedirs(folder, exist_ok=True)

# Create files
for filepath, content in files.items():
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

# Create empty __init__.py files for packages
open("bot/__init__.py", "w").close()
open("bot/utils/__init__.py", "w").close()
open("bot/modules/__init__.py", "w").close()

# Create empty data files
data_dir = "bot/data"
os.makedirs(data_dir, exist_ok=True)
for filename in ["reaction_roles.json", "snippets.json", "ticket_data.json", "pinned_messages.json", "ticket_categories.json"]:
    with open(os.path.join(data_dir, filename), "w") as f:
        json.dump({}, f, indent=2)

print("✅ Bot project structure created in /bot")