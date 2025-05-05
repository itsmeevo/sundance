import os
import sys
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Select, View, Modal, TextInput
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.future import select
from typing import Optional
import asyncio

# Database setup
class Base(DeclarativeBase):
    pass

class GuildConfig(Base):
    __tablename__ = 'guild_configs'
    
    id = Column(Integer, primary_key=True)
    guild_id = Column(BigInteger, unique=True, nullable=False)
    private_channels_category = Column(BigInteger)
    admin_usernames = Column(String)  # Comma-separated list of usernames

# Create async engine
engine = create_async_engine('sqlite+aiosqlite:///bot.db', echo=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_guild_config(guild_id: int) -> GuildConfig:
    """Get or create guild configuration."""
    async with AsyncSession(engine) as session:
        stmt = select(GuildConfig).where(GuildConfig.guild_id == guild_id)
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            config = GuildConfig(guild_id=guild_id)
            session.add(config)
            await session.commit()
        
        return config

async def update_guild_config(guild_id: int, setting: str, value: str) -> bool:
    """Update guild configuration."""
    async with AsyncSession(engine) as session:
        stmt = select(GuildConfig).where(GuildConfig.guild_id == guild_id)
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            config = GuildConfig(guild_id=guild_id)
            session.add(config)
        
        if setting == 'PRIVATE_CHANNELS_CATEGORY':
            config.private_channels_category = int(value)
        elif setting == 'ADMIN_USERNAMES':
            config.admin_usernames = value
        
        await session.commit()
        return True

async def get_admin_mentions(guild: discord.Guild, admin_usernames: str) -> list[str]:
    """Convert admin usernames to mentions."""
    mentions = []
    if not admin_usernames:
        return mentions
        
    usernames = [u.strip() for u in admin_usernames.split(',')]
    for username in usernames:
        # Handle both username and username#discriminator formats
        if '#' in username:
            name, discriminator = username.rsplit('#', 1)
            members = [m for m in guild.members if m.name.lower() == name.lower() and str(m.discriminator) == discriminator]
        else:
            # For usernames without discriminators (Discord's new username system)
            members = [m for m in guild.members if m.name.lower() == username.lower()]
            
        if members:
            mentions.append(f"<@{members[0].id}>")
        else:
            # Log missing admin for server administrators to handle
            print(f"Warning: Admin {username} not found in guild {guild.name} ({guild.id})")
    
    return mentions

# Load environment variables for bot token only
env_path = Path('.').resolve() / '.env'
print(f"Current working directory: {os.getcwd()}")
print(f"Looking for .env file at: {env_path}")

if not env_path.exists():
    print(f"Error: .env file not found at {env_path}")
    sys.exit(1)

# Load token from environment variables
env_vars = {}
try:
    with open(env_path, 'r') as f:
        for line in f:
            if line.startswith('DISCORD_TOKEN='):
                key, value = line.strip().split('=', 1)
                env_vars[key] = value
                os.environ[key] = value
                break
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
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def create_private_channel(interaction: discord.Interaction, channel_suffix: str, welcome_message: str) -> discord.TextChannel:
    """Creates a private channel for a user in the designated category."""
    user_display_name = interaction.user.display_name
    channel_name = f"{user_display_name.lower()}-{channel_suffix}"
    
    # Get guild configuration
    config = await get_guild_config(interaction.guild_id)
    if not config.private_channels_category:
        raise ValueError("Private channels category not set! An admin needs to set this using /settings")
    
    category = interaction.guild.get_channel(config.private_channels_category)
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
        await init_db()
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
        
        # Set bot status
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for new Guardians"
            )
        )
    except Exception as e:
        print(e)

async def handle_cooldown_error(interaction: discord.Interaction, error: app_commands.CommandOnCooldown):
    """Handle cooldown errors gracefully"""
    minutes, seconds = divmod(int(error.retry_after), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        await interaction.response.send_message(
            f"Please wait {hours} hours, {minutes} minutes, and {seconds} seconds before using this command again.",
            ephemeral=True
        )
    elif minutes > 0:
        await interaction.response.send_message(
            f"Please wait {minutes} minutes and {seconds} seconds before using this command again.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Please wait {seconds} seconds before using this command again.",
            ephemeral=True
        )

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Called when the bot joins a new server. Sends setup instructions to the server owner."""
    config = await get_guild_config(guild.id)
    
    # Send setup instructions to server owner
    try:
        setup_message = (
            f"👋 Thanks for adding me to {guild.name}!\n\n"
            "To get started, you'll need to:\n"
            "1. Create a category for private channels\n"
            "2. Use `/settings` to configure:\n"
            "   • The private channels category\n"
            "   • Admin users who will be notified\n\n"
            "Only server administrators can use the `/settings` command.\n"
            "Need help? Contact <@159122797745012736> for support."
        )
        await guild.owner.send(setup_message)
    except discord.Forbidden:
        # Try to find a channel we can send the message to
        for channel in guild.text_channels:
            try:
                await channel.send(setup_message)
                break
            except discord.Forbidden:
                continue

@bot.tree.command(
    name="signup",
    description="Create a private introduction channel to meet the community's admins"
)
@app_commands.checks.cooldown(1, 300)  # Once every 5 minutes
async def signup(interaction: discord.Interaction):
    """Creates a private introduction channel for a new user and notifies admins."""
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Get guild configuration
        config = await get_guild_config(interaction.guild_id)
        admin_mentions = await get_admin_mentions(interaction.guild, config.admin_usernames)
        
        if not admin_mentions:
            await interaction.followup.send(
                "No admins have been configured yet! An admin needs to set this using /settings",
                ephemeral=True
            )
            return
        
        # Create welcome message
        welcome_message = (
            f"Eyes up, <@{interaction.user.id}>! A new ally is here!\n"
            f"{', '.join(admin_mentions)}, can you welcome the new Guardian?"
        )
        
        # Create the channel
        channel = await create_private_channel(
            interaction=interaction,
            channel_suffix="introduction",
            welcome_message=welcome_message
        )
        
        await interaction.followup.send(
            f"I've created your private introduction channel: {channel.mention}",
            ephemeral=True
        )
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to create channels!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"An error occurred: {str(e)}",
            ephemeral=True
        )

@signup.error
async def signup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await handle_cooldown_error(interaction, error)

@bot.tree.command(
    name="help",
    description="Create a private help channel to get assistance from server admins"
)
@app_commands.checks.cooldown(1, 60)  # Once per minute
async def help(interaction: discord.Interaction):
    """Creates a private help channel for a user to speak with admins."""
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Get guild configuration
        config = await get_guild_config(interaction.guild_id)
        admin_mentions = await get_admin_mentions(interaction.guild, config.admin_usernames)
        
        if not admin_mentions:
            await interaction.followup.send(
                "No admins have been configured yet! An admin needs to set this using /settings",
                ephemeral=True
            )
            return
        
        # Create help message
        help_message = f"{', '.join(admin_mentions)}, <@{interaction.user.id}> would like to speak with you."
        
        # Create the channel
        channel = await create_private_channel(
            interaction=interaction,
            channel_suffix="help",
            welcome_message=help_message
        )
        
        await interaction.followup.send(
            f"I've created your private help channel: {channel.mention}",
            ephemeral=True
        )
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to create channels!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"An error occurred: {str(e)}",
            ephemeral=True
        )

@help.error
async def help_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await handle_cooldown_error(interaction, error)

@bot.tree.command(
    name="cleanup",
    description="Delete the current introduction or help channel (Admin only)"
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.cooldown(1, 5)  # Once every 5 seconds
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        await channel.delete()
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to delete this channel!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"An error occurred: {str(e)}",
            ephemeral=True
        )

@cleanup.error
async def cleanup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await handle_cooldown_error(interaction, error)

class SettingsModal(Modal):
    def __init__(self, setting: str, current_value: str, guild_id: int):
        super().__init__(title=f"Update {setting}")
        self.setting = setting
        self.guild_id = guild_id
        
        placeholder = "Category ID" if setting == "PRIVATE_CHANNELS_CATEGORY" else "Comma-separated usernames"
        label_text = (
            "Enter category ID for private channels" 
            if setting == "PRIVATE_CHANNELS_CATEGORY" 
            else "Enter admin usernames (comma-separated)"
        )
        
        help_text = (
            "Admin usernames can be in format 'username' or 'username#discriminator'"
            if setting == "ADMIN_USERNAMES"
            else ""
        )
        
        self.value_input = TextInput(
            label=label_text,
            placeholder=placeholder,
            default=current_value or "",
            required=True,
            min_length=1,
            max_length=1000
        )
        if help_text:
            self.value_input.placeholder = help_text
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = self.value_input.value.strip()
            
            # Validate value based on setting type
            if self.setting == 'PRIVATE_CHANNELS_CATEGORY':
                # Ensure value is a valid category ID
                category = interaction.guild.get_channel(int(value))
                if not category or not isinstance(category, discord.CategoryChannel):
                    await interaction.response.send_message(
                        "Invalid category ID. Please provide a valid category ID.",
                        ephemeral=True
                    )
                    return

            elif self.setting == 'ADMIN_USERNAMES':
                # Validate usernames
                usernames = [u.strip() for u in value.split(',')]
                invalid_usernames = []
                valid_users = []
                
                for username in usernames:
                    found = False
                    if '#' in username:
                        name, discriminator = username.rsplit('#', 1)
                        members = [m for m in interaction.guild.members 
                                if m.name.lower() == name.lower() and 
                                str(m.discriminator) == discriminator]
                        if members:
                            found = True
                            valid_users.append(username)
                    else:
                        members = [m for m in interaction.guild.members 
                                if m.name.lower() == username.lower()]
                        if members:
                            found = True
                            valid_users.append(username)
                    
                    if not found:
                        invalid_usernames.append(username)
                
                if invalid_usernames:
                    await interaction.response.send_message(
                        f"Invalid usernames: {', '.join(invalid_usernames)}. "
                        "Please provide valid usernames.",
                        ephemeral=True
                    )
                    return
                
                # Store validated usernames
                value = ','.join(valid_users)

            # Update the setting in the database
            setting_name = 'ADMIN_USERNAMES' if self.setting == 'ADMIN_USER_IDS' else self.setting
            if await update_guild_config(self.guild_id, setting_name, value):
                # Create a more visually appealing success message
                setting_display = "Admin Users" if setting_name == "ADMIN_USERNAMES" else "Private Channels Category"
                success_message = (
                    f"✅ **{setting_display} Updated Successfully**\n"
                )
                await interaction.response.send_message(
                    success_message,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"Failed to update {self.setting}.",
                    ephemeral=True
                )

        except ValueError as e:
            await interaction.response.send_message(
                f"Invalid value format: {str(e)}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"An error occurred: {str(e)}",
                ephemeral=True
            )

class SettingsView(View):
    def __init__(self, guild_id: int):
        super().__init__()
        self.add_item(SettingsSelect(guild_id))

class SettingsSelect(Select):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label="Private Channels Category",
                description="Set the category for private channels",
                value="PRIVATE_CHANNELS_CATEGORY"
            ),
            discord.SelectOption(
                label="Admin Usernames",
                description="Set the admin users who get notified",
                value="ADMIN_USERNAMES"
            )
        ]
        super().__init__(
            placeholder="Choose a setting to update...",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        setting = self.values[0]
        config = await get_guild_config(self.guild_id)
        
        current_value = ""
        if setting == 'PRIVATE_CHANNELS_CATEGORY' and config.private_channels_category:
            current_value = str(config.private_channels_category)
        elif setting == 'ADMIN_USERNAMES' and config.admin_usernames:
            current_value = config.admin_usernames
            
        modal = SettingsModal(setting, current_value, self.guild_id)
        await interaction.response.send_modal(modal)

@bot.tree.command(name="settings", description="Update bot settings (Admin only)")
@app_commands.default_permissions(administrator=True)
async def settings(interaction: discord.Interaction):
    """Updates specific bot settings using an interactive menu. Only available to administrators."""
    # Get current config
    config = await get_guild_config(interaction.guild_id)
    
    # Check if this is initial setup
    is_initial_setup = not config.private_channels_category and not config.admin_usernames
    
    if is_initial_setup:
        setup_message = (
            "🔧 Initial Setup\n\n"
            "To complete setup:\n"
            "1. Create a category for private channels if you haven't already\n"
            "2. Select 'Private Channels Category' and enter the category ID\n"
            "3. Select 'Admin Usernames' and enter the usernames who should be notified\n\n"
            "Need the category ID? Right-click the category and select 'Copy ID'.\n"
            "For admin usernames, you can use either format:\n"
            "• username (e.g., \"meevo.\")\n"
            "• username#discriminator (e.g., \"meevo#1234\")"
        )
    else:
        setup_message = "Sure, what do you want to change?"

    view = SettingsView(interaction.guild_id)
    await interaction.response.send_message(
        setup_message,
        view=view,
        ephemeral=True
    )

# Run the bot
bot.run(token)