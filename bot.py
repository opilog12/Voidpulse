import os
import discord
import random
import shutil
import asyncio
from collections import deque
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands
import subprocess
import sys
import json
from datetime import datetime

# ================================
# AUTOMATYCZNE POBRANIE / AKTUALIZACJA yt-dlp
# ================================
def install_or_update_yt_dlp():
    try:
        import yt_dlp
        print("✅ yt-dlp is installed, checking for updates...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], check=True)
    except ImportError:
        print("⚠️ yt-dlp not found, installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)

install_or_update_yt_dlp()
import yt_dlp  # import po instalacji/aktualizacji

# ================================
# Ładowanie tokena i FFMPEG
# ================================
load_dotenv()
TOKEN = os.getenv("token")
ffmpeg_exe = shutil.which("ffmpeg") or "./bin/ffmpeg.exe"

# Kolejki piosenek i ustawienia serwera
SONG_QUEUES = {}
CURRENT_SONG = {}
LOOP_MODE = {}
AUTOSHUFFLE = {}

# ================================
# Funkcja pobierania informacji o utworze
# ================================
async def get_audio_source(query):
    loop = asyncio.get_running_loop()
    ydl_opts_full = {
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "default_search": "ytsearch",
    }
    full_info = await loop.run_in_executor(
        None, lambda: yt_dlp.YoutubeDL(ydl_opts_full).extract_info(query, download=False)
    )

    if "entries" in full_info:
        full_info = full_info["entries"][0]

    return {
        "url": full_info["url"],
        "title": full_info.get("title", "Unknown Title"),
        "webpage_url": full_info.get("webpage_url", query),
        "thumbnail": full_info.get("thumbnail"),
        "duration": full_info.get("duration"),
    }

# ================================
# Tworzenie bota
# ================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
discord.utils.setup_logging(level="WARNING")

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} is online and commands are synced!")
    activity = discord.Activity(type=discord.ActivityType.listening, name="your next request...")
    await bot.change_presence(status=discord.Status.online, activity=activity)

# ================================
# Funkcja grająca następny utwór
# ================================
async def play_next_song(vc, guild_id, channel, message=None):
    guild_loop = LOOP_MODE.get(guild_id, "off")
    guild_autoshuffle = AUTOSHUFFLE.get(guild_id, False)

    if guild_loop == "song" and CURRENT_SONG.get(guild_id):
        info = CURRENT_SONG[guild_id]
    elif SONG_QUEUES.get(guild_id) and SONG_QUEUES[guild_id]:
        if guild_autoshuffle and len(SONG_QUEUES[guild_id]) > 1:
            queue_list = list(SONG_QUEUES[guild_id])
            random.shuffle(queue_list)
            SONG_QUEUES[guild_id] = deque(queue_list)

        query = SONG_QUEUES[guild_id].popleft()
        info = await get_audio_source(query)
        CURRENT_SONG[guild_id] = info

        if guild_loop == "queue":
            SONG_QUEUES[guild_id].append(query)
    else:
        try:
            await vc.disconnect()
        except:
            pass
        SONG_QUEUES[guild_id] = deque()
        CURRENT_SONG[guild_id] = None
        return

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            info["url"],
            executable=ffmpeg_exe,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        ),
        volume=0.5,
    )

    def after_play(error):
        if error:
            print(f"Error playing {info['title']}: {error}")
        asyncio.run_coroutine_threadsafe(
            play_next_song(vc, guild_id, channel), bot.loop
        )

    vc.play(source, after=after_play)

    loop_text = []
    if guild_loop == "song":
        loop_text.append("🔁 Loop: song")
    elif guild_loop == "queue":
        loop_text.append("🔁 Loop: queue")
    if guild_autoshuffle:
        loop_text.append("🔀 Auto-shuffle: ON")

    embed = discord.Embed(
        title="🎶 Now playing:",
        description=f"[{info['title']}]({info['webpage_url']})",
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=info["thumbnail"])
    if loop_text:
        embed.add_field(name="Mode", value=" | ".join(loop_text), inline=False)

    if message:
        await message.edit(content=None, embed=embed)
    else:
        await channel.send(embed=embed)

# ================================
# Komendy muzyczne
# ================================
@bot.tree.command(name="play", description="Play a song from YouTube")
@app_commands.describe(song_query="Search query or YouTube link")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("❌ You must be in a voice channel to use this.")
        return

    voice_channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    guild_id = str(interaction.guild_id)
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()

    loading_msg = await interaction.followup.send("⏳ Loading...")
    result = await get_audio_source(song_query)
    SONG_QUEUES[guild_id].append(song_query)

    if vc.is_playing() or vc.is_paused():
        await loading_msg.edit(content=f"✅ Added to queue: **{result['title']}**")
    else:
        await play_next_song(vc, guild_id, interaction.channel, message=loading_msg)

@bot.tree.command(name="loop", description="Toggle loop mode (off/song/queue)")
@app_commands.describe(mode="Loop mode: off, song, or queue")
async def loop_command(interaction: discord.Interaction, mode: str):
    guild_id = str(interaction.guild_id)
    mode = mode.lower()
    if mode not in ["off", "song", "queue"]:
        await interaction.response.send_message("❌ Invalid mode. Use `off`, `song`, or `queue`.")
        return
    LOOP_MODE[guild_id] = mode
    await interaction.response.send_message(f"🔁 Loop mode set to **{mode}**.")

@bot.tree.command(name="autoshuffle", description="Toggle automatic shuffle (on/off)")
@app_commands.describe(state="on or off")
async def autoshuffle(interaction: discord.Interaction, state: str):
    guild_id = str(interaction.guild_id)
    state = state.lower()
    if state not in ["on", "off"]:
        await interaction.response.send_message("❌ Invalid state. Use `on` or `off`.")
        return
    AUTOSHUFFLE[guild_id] = (state == "on")
    await interaction.response.send_message(f"🔀 Auto-shuffle **{'enabled' if state == 'on' else 'disabled'}**.")

@bot.tree.command(name="shuffle", description="Shuffle the current music queue")
async def shuffle(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())

    if not queue or len(queue) < 2:
        await interaction.response.send_message("❌ Not enough songs in the queue to shuffle.")
        return

    queue_list = list(queue)
    random.shuffle(queue_list)
    SONG_QUEUES[guild_id] = deque(queue_list)
    await interaction.response.send_message("🔀 The queue has been shuffled!")

@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Skipping song...")
    else:
        await interaction.response.send_message("❌ Nothing is currently playing.")

@bot.tree.command(name="queue", description="Show the current queue")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    loop_mode = LOOP_MODE.get(guild_id, "off")
    auto_mode = AUTOSHUFFLE.get(guild_id, False)
    if not queue:
        await interaction.response.send_message("📭 The queue is empty.")
        return
    formatted = "\n".join([f"{i+1}. {song}" for i, song in enumerate(queue)])
    embed = discord.Embed(
        title="🎵 Current Queue",
        description=formatted,
        color=discord.Color.green(),
    )
    footer = []
    if loop_mode != "off":
        footer.append(f"🔁 Loop: {loop_mode}")
    if auto_mode:
        footer.append("🔀 Auto-shuffle: ON")
    embed.set_footer(text=" | ".join(footer) + " | Bot by Opilog12")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying", description="Show the currently playing song")
async def nowplaying(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    current = CURRENT_SONG.get(guild_id)
    loop_mode = LOOP_MODE.get(guild_id, "off")
    auto_mode = AUTOSHUFFLE.get(guild_id, False)
    if not current:
        await interaction.response.send_message("❌ Nothing is currently playing.")
        return

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"[{current['title']}]({current['webpage_url']})",
        color=discord.Color.purple()
    )
    if current.get("thumbnail"):
        embed.set_thumbnail(url=current["thumbnail"])
    modes = []
    if loop_mode != "off":
        modes.append(f"🔁 Loop: {loop_mode}")
    modes.append(f"🔀 Auto-shuffle: {'ON' if auto_mode else 'OFF'}")
    embed.add_field(name="Modes", value=" | ".join(modes), inline=False)
    await interaction.response.send_message(embed=embed)

# ================================
# Pomniejsze komendy (pause, resume, stop, volume, clear, help, version, echo)
# ================================
@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Paused playback.")
    else:
        await interaction.response.send_message("❌ Nothing is currently playing.")

@bot.tree.command(name="resume", description="Resume paused playback")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Resumed playback.")
    else:
        await interaction.response.send_message("❌ Nothing is paused.")

@bot.tree.command(name="stop", description="Stop playback and clear the queue")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    guild_id = str(interaction.guild_id)
    if vc:
        await vc.disconnect()
    SONG_QUEUES[guild_id] = deque()
    CURRENT_SONG[guild_id] = None
    LOOP_MODE[guild_id] = "off"
    AUTOSHUFFLE[guild_id] = False
    await interaction.response.send_message("🛑 Stopped playback and cleared the queue.")

@bot.tree.command(name="volume", description="Change the volume (0-200)")
@app_commands.describe(volume="Volume percentage (0-200)")
async def volume(interaction: discord.Interaction, volume: int):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ Nothing is currently playing.")
        return
    if volume < 0 or volume > 200:
        await interaction.response.send_message("❌ Volume must be between 0 and 200.")
        return
    if hasattr(vc.source, 'volume'):
        vc.source.volume = volume / 100
        await interaction.response.send_message(f"🔊 Volume set to {volume}%")
    else:
        await interaction.response.send_message("❌ Cannot change volume for this source.")

@bot.tree.command(name="clear", description="Clear a number of messages from the channel")
@app_commands.describe(amount="Number of messages to delete (1-100)")
async def clear(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions to use this command.", ephemeral=True)
        return
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ You can only delete between 1 and 100 messages.", ephemeral=True)
        return
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)

@bot.tree.command(name="help", description="Show all commands and their descriptions")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Bot Commands",
        color=discord.Color.blue()
    )
    embed.add_field(name="/play <song>", value="Play a song from YouTube", inline=False)
    embed.add_field(name="/skip", value="Skip the current song", inline=False)
    embed.add_field(name="/queue", value="Show the current queue", inline=False)
    embed.add_field(name="/pause", value="Pause playback", inline=False)
    embed.add_field(name="/resume", value="Resume playback", inline=False)
    embed.add_field(name="/stop", value="Stop playback and clear the queue", inline=False)
    embed.add_field(name="/volume <0-200>", value="Adjust the volume", inline=False)
    embed.add_field(name="/nowplaying", value="Show the current song and AutoShuffle state", inline=False)
    embed.add_field(name="/loop <off|song|queue>", value="Set loop mode", inline=False)
    embed.add_field(name="/shuffle", value="Shuffle the queue manually", inline=False)
    embed.add_field(name="/autoshuffle <on|off>", value="Enable automatic shuffling", inline=False)
    embed.add_field(name="/clear <amount>", value="Delete messages (Admin only)", inline=False)
    embed.add_field(name="/version", value="Shows bot version", inline=False)
    embed.add_field(name="/warn <username> <reason>", value="Warn user (Admin only)", inline=False)
    embed.add_field(name="/warns <username>", value="Shows user warns (Admin only)", inline=False)
    embed.add_field(name="/removewarn <user> <warn_id>", value="Removes user warn (Admin only)", inline=False)
    embed.add_field(name="!echo (text)", value="Write message as bot (Admin only)", inline=False)
    embed.set_footer(text="Bot created by: opilog12")
    await interaction.response.send_message(embed=embed)

@bot.command(name="echo")
async def echo(ctx, *, message: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You need administrator permissions to use this command.")
        return
    try:
        await ctx.message.delete()
    except:
        pass
    await ctx.send(message)

@bot.tree.command(name="version", description="Show bot version")
async def version(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📌 Bot Version",
        description="Current version: **1.2**",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Bot created by: opilog12")
    await interaction.response.send_message(embed=embed)

# ================================
# SYSTEM WARNÓW
# ================================
WARNS_FILE = "warns.json"

def load_warns():
    if not os.path.exists(WARNS_FILE):
        return {}
    with open(WARNS_FILE, "r") as f:
        return json.load(f)

def save_warns(data):
    with open(WARNS_FILE, "w") as f:
        json.dump(data, f, indent=4)

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.describe(user="User to warn", reason="Reason for the warn")
@app_commands.checks.has_permissions(administrator=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    warns = load_warns()
    guild_id = str(interaction.guild_id)
    user_id = str(user.id)
    if guild_id not in warns:
        warns[guild_id] = {}
    if user_id not in warns[guild_id]:
        warns[guild_id][user_id] = []

    warn_entry = {
        "reason": reason,
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "moderator": str(interaction.user)
    }

    warns[guild_id][user_id].append(warn_entry)
    save_warns(warns)

    try:
        dm_embed = discord.Embed(
            title="⚠️ You have received a warning",
            description=f"**Reason:** {reason}",
            color=discord.Color.orange(),
        )
        dm_embed.add_field(name="Server", value=interaction.guild.name, inline=False)
        dm_embed.add_field(name="Moderator", value=str(interaction.user), inline=False)
        dm_embed.add_field(name="Date", value=warn_entry["date"], inline=False)
        await user.send(embed=dm_embed)
    except:
        pass

    embed = discord.Embed(
        title="⚠️ Warn issued",
        description=f"{user.mention} has been warned for: **{reason}**",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="warns", description="Show warns of a user")
@app_commands.describe(user="User to check warns for")
@app_commands.checks.has_permissions(administrator=True)
async def warns_command(interaction: discord.Interaction, user: discord.Member):
    warns = load_warns()
    guild_id = str(interaction.guild_id)
    user_id = str(user.id)
    user_warns = warns.get(guild_id, {}).get(user_id, [])

    if not user_warns:
        await interaction.response.send_message(f"✅ {user.mention} has no warns.")
        return

    embed = discord.Embed(
        title=f"⚠️ Warns for {user}",
        color=discord.Color.orange()
    )
    for idx, w in enumerate(user_warns, start=1):
        embed.add_field(
            name=f"Warn {idx}",
            value=f"**Reason:** {w['reason']}\n**Moderator:** {w['moderator']}\n**Date:** {w['date']}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removewarn", description="Remove a warn from a user")
@app_commands.describe(user="User", warn_id="Warn number to remove (1,2,3...)")
@app_commands.checks.has_permissions(administrator=True)
async def removewarn(interaction: discord.Interaction, user: discord.Member, warn_id: int):
    warns = load_warns()
    guild_id = str(interaction.guild_id)
    user_id = str(user.id)
    user_warns = warns.get(guild_id, {}).get(user_id, [])

    if not user_warns or warn_id < 1 or warn_id > len(user_warns):
        await interaction.response.send_message("❌ Invalid warn ID.")
        return

    removed = user_warns.pop(warn_id - 1)
    warns[guild_id][user_id] = user_warns
    save_warns(warns)

    await interaction.response.send_message(f"✅ Removed warn {warn_id} from {user.mention}.")

# ================================
# Uruchomienie bota
# ================================
bot.run(TOKEN)
