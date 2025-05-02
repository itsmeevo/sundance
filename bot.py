import os
import sys
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Select, View, Modal, TextInput
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
intents.members = True  # Enable members intent
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

def update_env_file(key: str, value: str):
    """Updates a specific key in the .env file."""
    env_path = Path('.').resolve() / '.env'
    lines = []
    
    with open(env_path, 'r') as f:
        lines = f.readlines()
    
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            updated = True
            break
    
    if updated:
        with open(env_path, 'w') as f:
            f.writelines(lines)
        os.environ[key] = value
        return True
    return False

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

class SettingsModal(Modal):
    def __init__(self, setting: str, current_value: str):
        super().__init__(title=f"Update {setting}")
        self.setting = setting
        
        placeholder = "Category ID" if setting == "PRIVATE_CHANNELS_CATEGORY" else "Comma-separated user IDs"
        label_text = (
            "Enter category ID for private channels" 
            if setting == "PRIVATE_CHANNELS_CATEGORY" 
            else "Enter admin user IDs (comma-separated)"
        )
        
        self.value_input = TextInput(
            label=label_text,
            placeholder=placeholder,
            default=current_value,
            required=True
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

            elif self.setting == 'ADMIN_USER_IDS':
                # Validate user IDs
                user_ids = value.split(',')
                invalid_ids = []
                for user_id in user_ids:
                    try:
                        user_id = user_id.strip()
                        member = await interaction.guild.fetch_member(int(user_id))
                        if not member:
                            invalid_ids.append(user_id)
                    except:
                        invalid_ids.append(user_id)
                
                if invalid_ids:
                    await interaction.response.send_message(
                        f"Invalid user IDs: {', '.join(invalid_ids)}. Please provide valid user IDs.",
                        ephemeral=True
                    )
                    return

            # Update the setting
            if update_env_file(self.setting, value):
                await interaction.response.send_message(
                    f"Successfully updated {self.setting}!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"Failed to update {self.setting}. Setting not found in .env file.",
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
    def __init__(self):
        super().__init__()
        self.add_item(SettingsSelect())

class SettingsSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Private Channels Category",
                description="Set the category for private channels",
                value="PRIVATE_CHANNELS_CATEGORY"
            ),
            discord.SelectOption(
                label="Admin User IDs",
                description="Set the admin users who get notified",
                value="ADMIN_USER_IDS"
            )
        ]
        super().__init__(
            placeholder="Choose a setting to update...",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        setting = self.values[0]
        current_value = os.getenv(setting, "Not set")
        modal = SettingsModal(setting, current_value)
        await interaction.response.send_modal(modal)

@bot.tree.command(name="settings", description="Update bot settings (Admin only)")
async def settings(interaction: discord.Interaction):
    """Updates specific bot settings using an interactive menu. Only available to administrators."""
    # Check if user has administrator permissions
    member = interaction.guild.get_member(interaction.user.id)
    if not member.guild_permissions.administrator:
        await interaction.response.send_message(
            "This command requires administrator permissions!",
            ephemeral=True
        )
        return

    view = SettingsView()
    await interaction.response.send_message(
        "Please select a setting to update:",
        view=view,
        ephemeral=True
    )

# Run the bot
bot.run(token)