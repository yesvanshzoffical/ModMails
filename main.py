# Made By YesVanshz || YesDoDevs
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import sqlite3
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# Initialize bot
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.dm_messages = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Database setup
def init_db():
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS modmail_threads
                 (user_id INTEGER PRIMARY KEY, channel_id INTEGER, is_open INTEGER, last_activity TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS modmail_messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  thread_id INTEGER, 
                  author_id INTEGER, 
                  content TEXT, 
                  timestamp TEXT,
                  is_from_user INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS modmail_config
                 (guild_id INTEGER PRIMARY KEY, modmail_role_id INTEGER)''')
    
    conn.commit()
    conn.close()

init_db()

# Configuration
MODMAIL_CATEGORY_NAME = "Modmail"
INACTIVE_THRESHOLD = 48  # Hours after which inactive threads are closed
AUTO_CLOSE_CHECK_INTERVAL = 3600  # Seconds between auto-close checks
DELETE_DELAY = 5  # Seconds to wait before deleting channel after closing

# Helper functions
async def get_modmail_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Get the modmail role for the guild"""
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('SELECT modmail_role_id FROM modmail_config WHERE guild_id=?', (guild.id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0]:
        return guild.get_role(result[0])
    return None

async def set_modmail_role(guild: discord.Guild, role: discord.Role):
    """Set the modmail role for the guild"""
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO modmail_config VALUES (?, ?)', (guild.id, role.id))
    conn.commit()
    conn.close()

async def get_modmail_category(guild: discord.Guild) -> discord.CategoryChannel:
    """Get or create the modmail category"""
    category = discord.utils.get(guild.categories, name=MODMAIL_CATEGORY_NAME)
    if not category:
        category = await guild.create_category(MODMAIL_CATEGORY_NAME)
    return category

async def create_modmail_channel(user: discord.User, guild: discord.Guild) -> discord.TextChannel:
    """Create a modmail channel for a user"""
    category = await get_modmail_category(guild)
    modmail_role = await get_modmail_role(guild)
    
    # Clean username for channel name
    clean_name = user.name.replace(" ", "-").lower()[:20]
    channel_name = f"modmail-{clean_name}"
    
    # Create channel with restricted permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    
    if modmail_role:
        overwrites[modmail_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    channel = await category.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        reason=f"Modmail thread for {user}"
    )
    
    # Add to database
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO modmail_threads VALUES (?, ?, ?, ?)',
              (user.id, channel.id, 1, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    
    # Send welcome message to user
    welcome_embed = discord.Embed(
        title="Modmail Started",
        description="Your modmail ticket has been created. You can now chat with the staff team.",
        color=discord.Color.green()
    )
    welcome_embed.set_footer(text="Please be patient while waiting for a response.")
    
    try:
        await user.send(embed=welcome_embed)
    except discord.Forbidden:
        pass  # User has DMs disabled
    
    return channel

async def get_modmail_channel(user: discord.User, guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Get existing modmail channel for a user"""
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('SELECT channel_id FROM modmail_threads WHERE user_id=? AND is_open=1', (user.id,))
    result = c.fetchone()
    conn.close()
    
    if result:
        channel_id = result[0]
        return guild.get_channel(channel_id)
    return None

async def close_modmail_thread(user: discord.User, guild: discord.Guild, closer: Optional[discord.Member] = None):
    """Close a modmail thread with delete option and DM notification"""
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('UPDATE modmail_threads SET is_open=0 WHERE user_id=?', (user.id,))
    conn.commit()
    conn.close()
    
    channel = await get_modmail_channel(user, guild)
    if channel:
        # Send closure notification to user
        close_embed = discord.Embed(
            title="Modmail Closed",
            description="Your modmail ticket has been closed.",
            color=discord.Color.red()
        )
        if closer:
            close_embed.set_footer(text=f"Closed by {closer.display_name}")
        
        try:
            await user.send(embed=close_embed)
        except discord.Forbidden:
            pass  # User has DMs disabled
        
        # Send closure message in channel and delete after delay
        msg = await channel.send(f"Modmail thread for {user} has been closed. This channel will be deleted in {DELETE_DELAY} seconds.")
        await asyncio.sleep(DELETE_DELAY)
        await channel.delete(reason="Modmail thread closed")

async def log_message(thread_id: int, author_id: int, content: str, is_from_user: bool):
    """Log a message to the database"""
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('INSERT INTO modmail_messages (thread_id, author_id, content, timestamp, is_from_user) VALUES (?, ?, ?, ?, ?)',
              (thread_id, author_id, content, datetime.datetime.utcnow().isoformat(), is_from_user))
    conn.commit()
    conn.close()

async def update_thread_activity(user_id: int):
    """Update the last activity time for a thread"""
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('UPDATE modmail_threads SET last_activity=? WHERE user_id=?',
              (datetime.datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()

# Permission check
def has_modmail_role():
    async def predicate(ctx):
        if ctx.guild is None:
            return False
        
        modmail_role = await get_modmail_role(ctx.guild)
        if not modmail_role:
            await ctx.send("No modmail role has been set up. Please contact an administrator.")
            return False
        
        if modmail_role in ctx.author.roles or ctx.author.guild_permissions.administrator:
            return True
        
        await ctx.send("You don't have permission to use modmail commands.")
        return False
    return commands.check(predicate)

# Bot events
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    auto_close_check.start()

@bot.event
async def on_message(message: discord.Message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return
    
    # Handle DMs (user to mod)
    if isinstance(message.channel, discord.DMChannel):
        # Find the first guild the bot is in
        guild = bot.guilds[0] if bot.guilds else None
        if not guild:
            return await message.channel.send("Sorry, I'm not in any servers!")
        
        # Get or create modmail channel
        channel = await get_modmail_channel(message.author, guild)
        if not channel:
            channel = await create_modmail_channel(message.author, guild)
            await channel.send(f"📩 New modmail from {message.author.mention} (`{message.author.id}`)")
        
        # Forward message to modmail channel
        embed = discord.Embed(
            description=message.content,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"User ID: {message.author.id}")
        
        await channel.send(embed=embed)
        await update_thread_activity(message.author.id)
        await log_message(message.author.id, message.author.id, message.content, True)
        
        # Let user know their message was received
        await message.add_reaction('✅')
    
    # Handle messages in modmail channels (mod to user)
    elif (isinstance(message.channel, discord.TextChannel) and 
          message.channel.category and 
          message.channel.category.name == MODMAIL_CATEGORY_NAME and
          not message.content.startswith('!')):
        
        # Check if author has modmail role
        modmail_role = await get_modmail_role(message.guild)
        if not modmail_role or (modmail_role not in message.author.roles and not message.author.guild_permissions.administrator):
            return
        
        # Get user ID from database
        conn = sqlite3.connect('modmail.db')
        c = conn.cursor()
        c.execute('SELECT user_id FROM modmail_threads WHERE channel_id=?', (message.channel.id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            return
        
        user_id = result[0]
        user = await bot.fetch_user(user_id)
        
        # Don't process command messages
        if message.content.startswith('!'):
            return await bot.process_commands(message)
        
        # Forward message to user
        try:
            embed = discord.Embed(
                description=message.content,
                color=discord.Color.green(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.set_author(name=f"Moderator Reply", icon_url=message.author.display_avatar.url)
            embed.set_footer(text=f"{message.guild.name} Staff")
            
            await user.send(embed=embed)
            await message.add_reaction('✅')
            await update_thread_activity(user_id)
            await log_message(user_id, message.author.id, message.content, False)
        except discord.Forbidden:
            await message.channel.send("⚠ Could not deliver message to user. They may have DMs disabled.")

    await bot.process_commands(message)

# Bot commands
@bot.command(name='setmodmailrole', help='Set the role that can access modmail')
@commands.has_permissions(administrator=True)
async def set_modmail_role_cmd(ctx, role: discord.Role):
    await set_modmail_role(ctx.guild, role)
    await ctx.send(f"Modmail role set to {role.mention}")

@bot.command(name='close', help='Close the current modmail thread')
@has_modmail_role()
async def close_thread(ctx):
    if not (ctx.channel.category and ctx.channel.category.name == MODMAIL_CATEGORY_NAME):
        return await ctx.send("This command can only be used in modmail channels.")
    
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM modmail_threads WHERE channel_id=?', (ctx.channel.id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return await ctx.send("Could not find this modmail thread in the database.")
    
    user_id = result[0]
    user = await bot.fetch_user(user_id)
    
    await close_modmail_thread(user, ctx.guild, ctx.author)

@bot.command(name='delete', help='Delete a message by ID')
@has_modmail_role()
async def delete_message(ctx, message_id: int):
    try:
        msg = await ctx.channel.fetch_message(message_id)
        await msg.delete()
        await ctx.send(f"Message {message_id} deleted.", delete_after=5)
    except discord.NotFound:
        await ctx.send("Message not found.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("I don't have permission to delete that message.", delete_after=5)

# Background tasks
@tasks.loop(seconds=AUTO_CLOSE_CHECK_INTERVAL)
async def auto_close_check():
    """Automatically close inactive modmail threads"""
    print("Running auto-close check...")
    threshold = datetime.datetime.utcnow() - datetime.timedelta(hours=INACTIVE_THRESHOLD)
    
    conn = sqlite3.connect('modmail.db')
    c = conn.cursor()
    c.execute('SELECT user_id, channel_id FROM modmail_threads WHERE is_open=1 AND last_activity < ?', 
              (threshold.isoformat(),))
    inactive_threads = c.fetchall()
    conn.close()
    
    for user_id, channel_id in inactive_threads:
        guild = bot.guilds[0]  # Assuming single guild for simplicity
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                user = await bot.fetch_user(user_id)
                await channel.send(f"This thread has been inactive for {INACTIVE_THRESHOLD} hours and will now be closed.")
                await close_modmail_thread(user, guild)
            except Exception as e:
                print(f"Error closing thread {channel_id}: {e}")

# Run the bot
if __name__ == '__main__':
    bot.run(os.getenv('DISCORD_TOKEN'))
