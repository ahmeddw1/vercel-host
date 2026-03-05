import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
from dotenv import load_dotenv
import os

# Load .env
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

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}   # guild_id -> list of (url, title, thumbnail)
        self.loops = {}    # guild_id -> bool
        self.volumes = {}  # guild_id -> 0.0-1.0

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

    async def get_audio(self, query):
        data = await asyncio.to_thread(lambda: ytdl.extract_info(query, download=False))
        if "entries" in data:
            data = data["entries"][0]
        return data["url"], data["title"], data.get("thumbnail")

    @app_commands.command(name="play", description="Play music or search YouTube")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("Join a voice channel first!", ephemeral=True)
        await interaction.response.defer()
        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            vc = await channel.connect()
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
        current = self.loops.get(guild_id, False)
        self.loops[guild_id] = not current
        await interaction.response.send_message(f"🔁 Loop {'ON' if not current else 'OFF'}")

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

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(TOKEN)

asyncio.run(main())
