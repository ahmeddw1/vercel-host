# bot.py
# Discord bot with: Moderation • Music (yt-dlp) • Weather • Custom status • Ticket system
# discord.py 2.3+ style (app_commands / slash commands)

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import random
import aiohttp
import yt_dlp
from datetime import datetime
import os
from dotenv import load_dotenv
import chat_exporter
import io

load_dotenv()

# ────────────────────────────────────────────────
#  CONFIG – CHANGE THESE VALUES
# ────────────────────────────────────────────────

TOKEN = os.getenv("DISCORD_TOKEN")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")      # https://openweathermap.org/api

# Ticket system config – replace with REAL IDs from your server
TICKET_CATEGORY_ID    = 123456789012345678      # Category → tickets created here
SUPPORT_ROLE_ID       = 987654321098765432      # Support/staff role ID
TICKET_LOG_CHANNEL_ID = 111222333444555666      # Optional: channel for transcripts

TICKET_PREFIX = "ticket-"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    case_insensitive=True,
    help_command=None
)

# Music storage (per guild)
queues = {}             # guild_id → [(url, title, requester), ...]
now_playing = {}        # guild_id → (title, url, requester)
current_voice_client = {}  # guild_id → voice_client

# ────────────────────────────────────────────────
#  EVENTS + STATUS ROTATION
# ────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} • {len(bot.guilds)} servers")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

    change_status.start()
    bot.add_view(TicketView())                  # persistent create button
    bot.add_view(TicketControlView(0, 0))       # dummy for persistent close button


@tasks.loop(minutes=10)
async def change_status():
    acts = [
        discord.Activity(type=discord.ActivityType.listening, name="/play your song"),
        discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers"),
        discord.Game(name="music + tickets + mod"),
        discord.Activity(type=discord.ActivityType.watching, name=datetime.now().strftime("%H:%M")),
    ]
    await bot.change_presence(activity=random.choice(acts))


# ────────────────────────────────────────────────
#  MUSIC COMMANDS
# ────────────────────────────────────────────────

async def play_next(interaction_or_ctx):
    """Helper to play next song in queue"""
    if isinstance(interaction_or_ctx, discord.Interaction):
        guild = interaction_or_ctx.guild
        send = lambda *a, **k: interaction_or_ctx.followup.send(*a, **k)
    else:
        guild = interaction_or_ctx.guild
        send = interaction_or_ctx.send

    if guild.id not in queues or not queues[guild.id]:
        await asyncio.sleep(60)
        if guild.id in current_voice_client and current_voice_client[guild.id].is_connected():
            if not current_voice_client[guild.id].is_playing():
                await current_voice_client[guild.id].disconnect()
        return

    url, title, requester = queues[guild.id].pop(0)
    now_playing[guild.id] = (title, url, requester)

    vc = current_voice_client.get(guild.id)
    if not vc:
        return

    try:
        source = discord.FFmpegPCMAudio(
            url,
            executable="ffmpeg",
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn"
        )
        vc.play(
            source,
            after=lambda e: bot.loop.create_task(play_next(interaction_or_ctx))
        )

        embed = discord.Embed(
            title="Now Playing",
            description=f"**{title}**\nRequested by {requester.mention}",
            color=0x2ecc71
        )
        await send(embed=embed)

    except Exception as e:
        await send(f"Playback error: {e}")
        await play_next(interaction_or_ctx)


@bot.tree.command(name="play", description="Play song / add to queue (YouTube, etc)")
@app_commands.describe(query="Song name or URL")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        return await interaction.followup.send("Join a voice channel first!", ephemeral=True)

    channel = interaction.user.voice.channel

    if interaction.guild.voice_client is None:
        vc = await channel.connect()
        current_voice_client[interaction.guild.id] = vc
    else:
        vc = interaction.guild.voice_client
        if vc.channel != channel:
            return await interaction.followup.send("I'm in another voice channel!", ephemeral=True)

    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "extract_flat": "in_playlist",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)

            if "entries" in info:  # playlist
                added = 0
                for entry in info["entries"]:
                    if entry:
                        queues.setdefault(interaction.guild.id, []).append((entry["url"], entry.get("title", "??"), interaction.user))
                        added += 1
                embed = discord.Embed(title="Playlist added", description=f"{added} tracks", color=0x3498db)
                return await interaction.followup.send(embed=embed)

            else:
                url = info["url"]
                title = info["title"]
                queues.setdefault(interaction.guild.id, []).append((url, title, interaction.user))

                embed = discord.Embed(title="Added to queue", description=title, color=0x3498db)
                embed.set_thumbnail(url=info.get("thumbnail"))
                await interaction.followup.send(embed=embed)

                if not vc.is_playing() and not vc.is_paused():
                    await play_next(interaction)

    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


@bot.tree.command(name="skip", description="Skip current track")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        return await interaction.response.send_message("Nothing playing.", ephemeral=True)
    vc.stop()
    await interaction.response.send_message("⏭️ Skipped")


@bot.tree.command(name="pause", description="Pause / Resume")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("Not in VC.", ephemeral=True)
    if vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Paused")
    elif vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Resumed")
    else:
        await interaction.response.send_message("Nothing to pause.", ephemeral=True)


@bot.tree.command(name="queue", description="Show queue")
async def queue_cmd(interaction: discord.Interaction):
    q = queues.get(interaction.guild.id, [])
    if not q:
        return await interaction.response.send_message("Queue empty.", ephemeral=True)

    embed = discord.Embed(title="Queue", color=0xe67e22)
    for i, (_, title, user) in enumerate(q[:12], 1):
        embed.add_field(name=f"{i}. {title}", value=f"by {user}", inline=False)
    if len(q) > 12:
        embed.set_footer(text=f"+ {len(q)-12} more")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="leave", description="Leave voice channel")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        current_voice_client.pop(interaction.guild.id, None)
        await interaction.response.send_message("👋 Left VC")
    else:
        await interaction.response.send_message("Not in VC.", ephemeral=True)


# ────────────────────────────────────────────────
#  MODERATION
# ────────────────────────────────────────────────

@bot.tree.command(name="ban", description="Ban member")
@app_commands.default_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"{member} banned | {reason}")


@bot.tree.command(name="kick", description="Kick member")
@app_commands.default_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"{member} kicked | {reason}")


@bot.tree.command(name="mute", description="Timeout member")
@app_commands.default_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 60):
    if minutes < 1 or minutes > 40320:
        return await interaction.response.send_message("1–40320 minutes only.", ephemeral=True)
    until = discord.utils.utcnow() + discord.utils.time_delta(minutes=minutes)
    await member.timeout(until)
    await interaction.response.send_message(f"{member} muted for {minutes} min")


@bot.tree.command(name="unmute", description="Remove timeout")
@app_commands.default_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"{member} unmuted")


@bot.tree.command(name="purge", description="Delete messages")
@app_commands.default_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 2, 100]):
    await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"🧹 Deleted {amount} messages", delete_after=6, ephemeral=True)


# ────────────────────────────────────────────────
#  WEATHER
# ────────────────────────────────────────────────

@bot.tree.command(name="weather", description="Weather forecast")
@app_commands.describe(city="City name")
async def weather(interaction: discord.Interaction, city: str):
    await interaction.response.defer()
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                return await interaction.followup.send("City not found.", ephemeral=True)
            data = await r.json()

    embed = discord.Embed(
        title=f"{data['name']}, {data['sys']['country']}",
        description=data["weather"][0]["description"].title(),
        color=0x3498db
    )
    embed.set_thumbnail(url=f"http://openweathermap.org/img/wn/{data['weather'][0]['icon']}@2x.png")
    embed.add_field(name="Temp", value=f"{data['main']['temp']} °C")
    embed.add_field(name="Feels like", value=f"{data['main']['feels_like']} °C")
    embed.add_field(name="Humidity", value=f"{data['main']['humidity']}%")
    await interaction.followup.send(embed=embed)


# ────────────────────────────────────────────────
#  TICKET SYSTEM
# ────────────────────────────────────────────────

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.blurple, emoji="📩", custom_id="create_ticket_btn")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            return await interaction.followup.send("Ticket category missing.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.get_role(SUPPORT_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        channel = await guild.create_text_channel(
            name=f"{TICKET_PREFIX}{member.name.lower()}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket by {member}"
        )

        embed = discord.Embed(
            title="Support Ticket",
            description=f"{member.mention} please describe your issue.\nStaff will help soon.",
            color=0x2ecc71
        )

        view = TicketControlView(channel.id, member.id)
        await channel.send(f"{member.mention} <@&{SUPPORT_ROLE_ID}>", embed=embed, view=view)

        await interaction.followup.send(f"Your ticket: {channel.mention}", ephemeral=True)


class TicketControlView(discord.ui.View):
    def __init__(self, channel_id: int, creator_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.creator_id = creator_id

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒", custom_id="close_ticket_btn")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if SUPPORT_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("Only staff can close.", ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            return

        transcript = await chat_exporter.export(channel)
        if transcript and TICKET_LOG_CHANNEL_ID:
            log_ch = interaction.guild.get_channel(TICKET_LOG_CHANNEL_ID)
            if log_ch:
                file = discord.File(io.BytesIO(transcript.encode()), f"transcript-{channel.name}.html")
                await log_ch.send(f"Ticket closed: {channel.name} • by {interaction.user.mention}", file=file)

        await channel.delete(reason="Closed by staff")


@bot.tree.command(name="ticketpanel", description="Post ticket creation panel (admin only)")
@app_commands.default_permissions(manage_guild=True)
async def ticket_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Need help?",
        description="Click below to open a private ticket.",
        color=0x3498db
    )
    view = TicketView()
    await interaction.response.send_message(embed=embed, view=view)


# ────────────────────────────────────────────────
#  HELP
# ────────────────────────────────────────────────

@bot.tree.command(name="help", description="Command list")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Commands", color=0x7289da)
    embed.add_field(name="Music", value="/play  /skip  /pause  /queue  /leave", inline=False)
    embed.add_field(name="Moderation", value="/ban  /kick  /mute  /unmute  /purge", inline=False)
    embed.add_field(name="Utility", value="/weather  /ticketpanel  /help", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    bot.run(TOKEN)
