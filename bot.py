import os
import sys
import discord
import traceback
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Select, View, Modal, TextInput
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.future import select
from typing import Optional
import asyncio
from atproto import Client, models
from datetime import datetime, timezone
import json

# Database setup
class Base(DeclarativeBase):
    pass

class GuildConfig(Base):
    __tablename__ = 'guild_configs'
    
    id = Column(Integer, primary_key=True)
    guild_id = Column(BigInteger, unique=True, nullable=False)
    private_channels_category = Column(BigInteger)
    admin_usernames = Column(String)  # Comma-separated list of usernames
    bluesky_enabled = Column(Integer, default=0)  # 0 = disabled, 1 = enabled
    bluesky_channel_id = Column(BigInteger)  # Channel to post Bluesky updates
    last_bluesky_post = Column(String)  # Timestamp of last processed post

# Create async engine
engine = create_async_engine('sqlite+aiosqlite:///bot.db', echo=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def migrate_db():
    """Run database migrations to add new columns"""
    async with engine.begin() as conn:
        # Check if new columns exist
        result = await conn.execute(text("PRAGMA table_info(guild_configs)"))
        columns = {row[1] for row in result}
        
        # Add bluesky columns if they don't exist
        if 'bluesky_enabled' not in columns:
            await conn.execute(text("ALTER TABLE guild_configs ADD COLUMN bluesky_enabled INTEGER DEFAULT 0"))
        if 'bluesky_channel_id' not in columns:
            await conn.execute(text("ALTER TABLE guild_configs ADD COLUMN bluesky_channel_id BIGINT"))
        if 'last_bluesky_post' not in columns:
            await conn.execute(text("ALTER TABLE guild_configs ADD COLUMN last_bluesky_post VARCHAR"))

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
        elif setting == 'BLUESKY_ENABLED':
            config.bluesky_enabled = int(value)
        elif setting == 'BLUESKY_CHANNEL':
            config.bluesky_channel_id = int(value)
        
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

async def check_bluesky_feed():
    """Check for new Bluesky posts from destiny2team.bungie.net"""
    client = Client()
    print("Checking Bluesky feed...")
    
    # Login to Bluesky if credentials are provided
    bluesky_username = os.environ.get('BLUESKY_USERNAME')
    bluesky_password = os.environ.get('BLUESKY_APP_PASSWORD')
    
    if bluesky_username and bluesky_password:
        try:
            print("Logging into Bluesky...")
            client.login(bluesky_username, bluesky_password)
            print("Logged in successfully")
        except Exception as e:
            print(f"Error logging into Bluesky: {e}")
            return
    else:
        print("No Bluesky credentials provided - some features may be limited")
    
    async with AsyncSession(engine) as session:
        # Get all guilds with Bluesky enabled
        stmt = select(GuildConfig).where(GuildConfig.bluesky_enabled == 1)
        result = await session.execute(stmt)
        configs = result.scalars().all()
        
        print(f"Found {len(configs)} guilds with Bluesky enabled")
        
        if not configs:
            return
            
        # Get latest posts from Destiny 2 team
        try:
            print("Fetching Destiny 2 team profile...")
            profile_response = await asyncio.to_thread(
                client.get_profile,
                "destiny2team.bungie.net"
            )
            did = profile_response.did
            
            # Get the avatar URL from the profile
            avatar_url = profile_response.avatar or "https://www.bungie.net/img/destiny_content/icons/icon_destiny.png"
            print(f"Found DID: {did}")
            
            print("Fetching feed...")
            feed_response = await asyncio.to_thread(
                client.app.bsky.feed.get_author_feed,
                {
                    "actor": "destiny2team.bungie.net",
                    "limit": 1 if not configs[0].last_bluesky_post else 10
                }
            )
            
            posts = feed_response.feed
            print(f"Found {len(posts)} posts")
            
            for config in configs:
                channel = bot.get_channel(config.bluesky_channel_id)
                if not channel:
                    print(f"Could not find channel {config.bluesky_channel_id}")
                    continue
                    
                last_post_time = config.last_bluesky_post or "1970-01-01T00:00:00Z"
                last_post_dt = datetime.fromisoformat(last_post_time.replace('Z', '+00:00'))
                print(f"Last post time: {last_post_time}")
                
                new_posts = 0
                for post in posts:
                    try:
                        # Access fields directly from the record
                        post_text = post.post.record.text
                        post_time = post.post.record.created_at
                        
                        if not post_time:
                            print(f"Warning: No timestamp found for post")
                            continue
                            
                        post_dt = datetime.fromisoformat(post_time.replace('Z', '+00:00'))
                        print(f"Checking post from {post_time}")
                        
                        if post_dt > last_post_dt:
                            new_posts += 1
                            # Create embed for the post
                            embed = discord.Embed(
                                title="Link to Post",
                                description=post_text,
                                color=0x00b0f4,
                                url=f"https://bsky.app/profile/destiny2team.bungie.net/post/{post.post.uri.split('/')[-1]}"
                            )
                            embed.set_author(
                                name="Destiny 2 Team on Bluesky",
                                icon_url=avatar_url
                            )
                            embed.timestamp = post_dt
                            
                            await channel.send(embed=embed)
                    except Exception as e:
                        print(f"Error processing post: {e}")
                        traceback.print_exc()
                        continue
                
                print(f"Sent {new_posts} new posts")
                
                # Update the last post time for this guild
                if posts:
                    try:
                        latest_post = posts[0]
                        latest_time = latest_post.post.record.created_at
                        if latest_time:
                            config.last_bluesky_post = latest_time
                            await session.commit()
                            print(f"Updated last post time to {latest_time}")
                    except Exception as e:
                        print(f"Error updating last post time: {e}")
                        traceback.print_exc()
                    
        except Exception as e:
            print(f"Error checking Bluesky feed: {e}")
            traceback.print_exc()

@tasks.loop(minutes=5)
async def bluesky_feed_task():
    """Background task to check Bluesky feed every 5 minutes"""
    await check_bluesky_feed()

@bluesky_feed_task.before_loop
async def before_bluesky_feed():
    """Wait for the bot to be ready before starting the feed task"""
    await bot.wait_until_ready()

# Load environment variables
env_path = Path('.').resolve() / '.env'
print(f"Current working directory: {os.getcwd()}")
print(f"Looking for .env file at: {env_path}")

if not env_path.exists():
    print(f"Error: .env file not found at {env_path}")
    sys.exit(1)

# Load variables from .env file
env_vars = {}
try:
    with open(env_path, 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
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

# Load optional Bluesky credentials
bluesky_username = env_vars.get('BLUESKY_USERNAME')
bluesky_password = env_vars.get('BLUESKY_APP_PASSWORD')
if bluesky_username and bluesky_password:
    print("Bluesky credentials found")

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
        await migrate_db()  # Run migrations
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
        
        # Start the Bluesky feed task
        if not bluesky_feed_task.is_running():
            bluesky_feed_task.start()
        
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
        
        if setting == "PRIVATE_CHANNELS_CATEGORY":
            placeholder = "Category ID"
            label_text = "Category ID"
            help_text = ""
        elif setting == "ADMIN_USERNAMES":
            placeholder = "Comma-separated usernames"
            label_text = "Admin Usernames"
            help_text = "Admin usernames can be in format 'username' or 'username#discriminator'"
        elif setting == "BLUESKY_CHANNEL":
            placeholder = "Channel ID"
            label_text = "Channel ID"
            help_text = "Right-click the channel and select 'Copy ID'"
        
        self.value_input = TextInput(
            label=label_text,
            placeholder=placeholder if not help_text else help_text,
            default=current_value or "",
            required=True,
            min_length=1,
            max_length=1000
        )
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

            elif self.setting == 'BLUESKY_CHANNEL':
                # Ensure value is a valid text channel ID
                channel = interaction.guild.get_channel(int(value))
                if not channel or not isinstance(channel, discord.TextChannel):
                    await interaction.response.send_message(
                        "Invalid channel ID. Please provide a valid text channel ID.",
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
                setting_display = {
                    "ADMIN_USERNAMES": "Admin Users",
                    "PRIVATE_CHANNELS_CATEGORY": "Private Channels Category",
                    "BLUESKY_CHANNEL": "Bluesky Feed Channel"
                }.get(setting_name, setting_name)
                
                success_message = f"✅ **{setting_display} Updated Successfully**\n"
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
                description="Category for private channels",
                value="PRIVATE_CHANNELS_CATEGORY"
            ),
            discord.SelectOption(
                label="Admin Usernames",
                description="Admins to notify",
                value="ADMIN_USERNAMES"
            ),
            discord.SelectOption(
                label="D2 Updates Channel",
                description="Channel for Bluesky updates",
                value="BLUESKY_CHANNEL"
            ),
            discord.SelectOption(
                label="D2 Updates Toggle",
                description="Enable/disable Bluesky feed",
                value="BLUESKY_ENABLED"
            )
        ]
        super().__init__(
            placeholder="Choose a setting...",
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
        elif setting == 'BLUESKY_CHANNEL' and config.bluesky_channel_id:
            current_value = str(config.bluesky_channel_id)
        elif setting == 'BLUESKY_ENABLED':
            current_value = str(config.bluesky_enabled)
            
        if setting == 'BLUESKY_ENABLED':
            view = BlueskyToggleView(self.guild_id, bool(config.bluesky_enabled))
            await interaction.response.send_message(
                "Toggle Bluesky Feed:",
                view=view,
                ephemeral=True
            )
        else:
            modal = SettingsModal(setting, current_value, self.guild_id)
            await interaction.response.send_modal(modal)

class BlueskyToggleView(View):
    def __init__(self, guild_id: int, current_state: bool):
        super().__init__()
        self.guild_id = guild_id
        self.add_item(BlueskyToggleButton(guild_id, current_state))

class BlueskyToggleButton(discord.ui.Button):
    def __init__(self, guild_id: int, current_state: bool):
        super().__init__(
            style=discord.ButtonStyle.green if not current_state else discord.ButtonStyle.red,
            label="Enable Bluesky Feed" if not current_state else "Disable Bluesky Feed"
        )
        self.guild_id = guild_id
        self.current_state = current_state

    async def callback(self, interaction: discord.Interaction):
        new_state = not self.current_state
        await update_guild_config(self.guild_id, "BLUESKY_ENABLED", str(int(new_state)))
        await interaction.response.send_message(
            f"Bluesky feed has been {'enabled' if new_state else 'disabled'}.",
            ephemeral=True
        )

@bot.tree.command(name="settings", description="Update bot settings (Admin only)")
@app_commands.default_permissions(administrator=True)
async def settings(interaction: discord.Interaction):
    """Updates specific bot settings using an interactive menu. Only available to administrators."""
    async with AsyncSession(engine) as session:
        # Get current config
        stmt = select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            config = GuildConfig(guild_id=interaction.guild_id)
            session.add(config)
            await session.commit()
        
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