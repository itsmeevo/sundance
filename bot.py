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
                os.environ[key] = value
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

async def create_private_channel(interaction: discord.Interaction, channel_suffix: str, welcome_message: str) -> discord.TextChannel:
    """
    Creates a private channel for a user in the designated category.
    
    Args:
        interaction: The Discord interaction that triggered the command
        channel_suffix: The suffix to append to the channel name (e.g., 'introduction' or 'help')
        welcome_message: The message to send in the new channel
        
    Returns:
        discord.TextChannel: The created channel
        
    Raises:
        discord.Forbidden: If the bot lacks permissions to create the channel
    """
    user_display_name = interaction.user.display_name
    channel_name = f"{user_display_name.lower()}-{channel_suffix}"
    category_id = int(os.getenv('PRIVATE_CHANNELS_CATEGORY'))
    
    category = interaction.guild.get_channel(category_id)
    if not category:
        raise ValueError("Could not find the specified category for channels!")
    
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    channel = await interaction.guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        category=category
    )
    
    await channel.send(welcome_message)
    return channel

@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord."""
    print(f"{bot.user} is ready and online!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

@bot.tree.command(name="signup", description="Creates a private introduction channel for you")
async def signup(interaction: discord.Interaction):
    """Creates a private introduction channel for a new user and notifies admins."""
    try:
        # Get admin mentions
        admin_ids = os.getenv('ADMIN_USER_IDS', '').split(',')
        admin_mentions = ', '.join([f'<@{user_id}>' for user_id in admin_ids if user_id])
        
        # Create welcome message
        welcome_message = (
            f"Eyes up, <@{interaction.user.id}>! A new ally is here!\n"
            f"{admin_mentions}, can you welcome the new Guardian?"
        )
        
        # Create the channel
        channel = await create_private_channel(
            interaction=interaction,
            channel_suffix="introduction",
            welcome_message=welcome_message
        )
        
        await interaction.response.send_message(
            f"Created your private introduction channel: {channel.mention}",
            ephemeral=True
        )
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
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

@bot.tree.command(name="help", description="Creates a private help channel to speak with admins")
async def help(interaction: discord.Interaction):
    """Creates a private help channel for a user to speak with admins."""
    try:
        # Get admin mentions
        admin_ids = os.getenv('ADMIN_USER_IDS', '').split(',')
        admin_mentions = ', '.join([f'<@{user_id}>' for user_id in admin_ids if user_id])
        
        # Create help message
        help_message = f"{admin_mentions}, <@{interaction.user.id}> would like to speak with you."
        
        # Create the channel
        channel = await create_private_channel(
            interaction=interaction,
            channel_suffix="help",
            welcome_message=help_message
        )
        
        await interaction.response.send_message(
            f"Created your private help channel: {channel.mention}",
            ephemeral=True
        )
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
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
    """Deletes the current channel if it's an introduction or help channel."""
    channel = interaction.channel
    
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