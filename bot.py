import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View
import yt_dlp
import asyncio
from dotenv import load_dotenv
import os
import re

# Load token
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

ytdl_opts = {
    "format": "bestaudio/best",
    "quiet": True,
    "default_search": "ytsearch",
}

ffmpeg_opts = {"options": "-vn"}
ytdl = yt_dlp.YoutubeDL(ytdl_opts)

# Music Cog
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}   # guild_id -> list of (url, title, thumbnail)
        self.loops = {}    # guild_id -> bool
        self.volumes = {}  # guild_id -> 0.0-1.0

    async def get_audio(self, query):
        # Direct YouTube, SoundCloud or Spotify URLs
        if re.match(r'https?://(www\.)?(youtube\.com|youtu\.be|soundcloud\.com|open\.spotify\.com)', query):
            data = await asyncio.to_thread(lambda: ytdl.extract_info(query, download=False))
            if 'entries' in data:
                data = data['entries'][0]
        else:  # search on YouTube
            data = await asyncio.to_thread(lambda: ytdl.extract_info(f"ytsearch:{query}", download=False))
            data = data['entries'][0]
        return data['url'], data['title'], data.get('thumbnail')

    async def play_next(self, guild_id):
        vc = self.bot.get_guild(guild_id).voice_client
        if not vc:
            return
        queue = self.queues.get(guild_id, [])
        if queue:
            url, title, thumb = queue[0]
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **ffmpeg_opts),
                volume=self.volumes.get(guild_id, 0.5),
            )

            def after_playing(error):
                if error:
                    print(f"Playback error: {error}")
                if not self.loops.get(guild_id, False):
                    self.queues[guild_id].pop(0)
                fut = asyncio.run_coroutine_threadsafe(
                    self.play_next(guild_id), self.bot.loop
                )
                try:
                    fut.result()
                except:
                    pass

            vc.play(source, after=after_playing)

    async def send_queue_embed(self, interaction):
        guild_id = interaction.guild.id
        queue = self.queues.get(guild_id, [])
        if queue:
            desc = "\n".join([f"{i+1}. {item[1]}" for i, item in enumerate(queue[:10])])
            embed = discord.Embed(title="🎶 Queue", description=desc, color=discord.Color.purple())
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("Queue is empty.")

    @app_commands.command(name="join", description="Bot joins your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            return await interaction.response.send_message("Join a voice channel first!", ephemeral=True)
        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            await channel.connect()
            await interaction.response.send_message(f"👋 Joined {channel.name}")
        else:
            await interaction.response.send_message("Already in a voice channel")

    @app_commands.command(name="play", description="Play music from YouTube/Spotify/SoundCloud")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("Join a voice channel first!", ephemeral=True)
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        url, title, thumb = await self.get_audio(query)
        guild_id = interaction.guild.id
        self.queues.setdefault(guild_id, [])
        self.queues[guild_id].append((url, title, thumb))

        if not vc.is_playing():
            await self.play_next(guild_id)

        embed = discord.Embed(title="🎶 Added to Queue", description=f"**{title}**", color=discord.Color.green())
        if thumb:
            embed.set_thumbnail(url=thumb)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="skip", description="Skip current song")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭ Skipped")
        else:
            await interaction.response.send_message("Nothing playing")

    @app_commands.command(name="stop", description="Stop music and clear queue")
    async def stop(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        self.queues[guild_id] = []
        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
        await interaction.response.send_message("⏹ Stopped and cleared queue")

    @app_commands.command(name="leave", description="Leave voice channel")
    async def leave(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
        await interaction.response.send_message("👋 Left the channel")

    @app_commands.command(name="volume", description="Set volume (0-100)")
    async def volume(self, interaction: discord.Interaction, level: int):
        guild_id = interaction.guild.id
        level = max(0, min(level, 100))
        self.volumes[guild_id] = level / 100
        await interaction.response.send_message(f"🔊 Volume set to {level}%")

    @app_commands.command(name="loop", description="Toggle loop mode")
    async def loop(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        self.loops[guild_id] = not self.loops.get(guild_id, False)
        await interaction.response.send_message(f"🔁 Loop {'ON' if self.loops[guild_id] else 'OFF'}")

    @app_commands.command(name="nowplaying", description="Show current song")
    async def nowplaying(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        queue = self.queues.get(guild_id, [])
        if queue:
            title = queue[0][1]
            thumb = queue[0][2]
            embed = discord.Embed(title="🎵 Now Playing", description=f"**{title}**", color=discord.Color.blue())
            if thumb:
                embed.set_thumbnail(url=thumb)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("Nothing playing")

    @app_commands.command(name="queue", description="Show song queue")
    async def queue(self, interaction: discord.Interaction):
        await self.send_queue_embed(interaction)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(TOKEN)

asyncio.run(main())
