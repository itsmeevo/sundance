import os
import sys
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path

# Load environment variables
env_path = Path('.').resolve() / '.env'
print(f"Current working directory: {os.getcwd()}")
print(f"Looking for .env file at: {env_path}")

if not env_path.exists():
    print(f"Error: .env file not found at {env_path}")
    sys.exit(1)

# Load all environment variables
env_vars = {}
try:
    with open(env_path, 'r') as f:
        for line in f:
            if '=' in line:
                key, value = line.strip().split('=', 1)
                env_vars[key] = value
                os.environ[key] = value  # Also set in os.environ for os.getenv() to work
except Exception as e:
    print(f"Error reading .env file: {e}")
    sys.exit(1)

token = env_vars.get('DISCORD_TOKEN')
if not token:
    print("Error: DISCORD_TOKEN not found in .env file")
    sys.exit(1)

print(f"Token loaded, length: {len(token)}")

if len(token) < 50:
    print("Error: Token seems too short - please verify it was copied correctly")
    sys.exit(1)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} is ready and online!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

@bot.tree.command(name="signup", description="Creates a private introduction channel for you")
async def signup(interaction: discord.Interaction):
    # Get the user's display name and category ID
    user_display_name = interaction.user.display_name
    channel_name = f"{user_display_name.lower()}-introduction"
    category_id = int(os.getenv('SIGNUP_CHANNEL'))
    
    # Get the category
    category = interaction.guild.get_channel(category_id)
    if not category:
        await interaction.response.send_message(
            "Could not find the specified category for introduction channels!",
            ephemeral=True
        )
        return
    
    # Create channel permissions for the user
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    try:
        # Create the private channel in the specified category
        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category
        )
        
        # Get admin user IDs and create mentions with commas
        admin_ids = os.getenv('ADMIN_USER_IDS', '').split(',')
        admin_mentions = ', '.join([f'<@{user_id}>' for user_id in admin_ids if user_id])
        
        # Send welcome message in the new channel with user mention and admin mentions on new line
        await channel.send(f"Eyes up, <@{interaction.user.id}>! A new ally is here!\n{admin_mentions}, can you welcome the new Guardian?")
        
        await interaction.response.send_message(
            f"Created your private introduction channel: {channel.mention}",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to create channels!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"An error occurred: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="helpme", description="Creates a private help channel to speak with admins")
async def help(interaction: discord.Interaction):
    # Get the user's display name and category ID
    user_display_name = interaction.user.display_name
    channel_name = f"{user_display_name.lower()}-help"
    category_id = int(os.getenv('SIGNUP_CHANNEL'))
    
    # Get the category
    category = interaction.guild.get_channel(category_id)
    if not category:
        await interaction.response.send_message(
            "Could not find the specified category for help channels!",
            ephemeral=True
        )
        return
    
    # Create channel permissions for the user
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    try:
        # Create the private channel in the specified category
        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category
        )
        
        # Get admin user IDs and create mentions with commas
        admin_ids = os.getenv('ADMIN_USER_IDS', '').split(',')
        admin_mentions = ', '.join([f'<@{user_id}>' for user_id in admin_ids if user_id])
        
        # Send help request message in the new channel
        await channel.send(f"{admin_mentions}, <@{interaction.user.id}> would like to speak with you.")
        
        await interaction.response.send_message(
            f"Created your private help channel: {channel.mention}",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to create channels!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"An error occurred: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="cleanup", description="Deletes the current channel if it's an introduction or help channel")
async def cleanup(interaction: discord.Interaction):
    channel = interaction.channel
    
    # Check if the channel name ends with 'introduction' or 'help'
    if not (channel.name.endswith('introduction') or channel.name.endswith('help')):
        await interaction.response.send_message(
            "This command can only be used in introduction or help channels!",
            ephemeral=True
        )
        return
    
    try:
        await channel.delete()
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to delete this channel!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"An error occurred: {str(e)}",
            ephemeral=True
        )

# Run the bot
bot.run(token)