import os
import discord
from discord.ext import commands, tasks

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Eingeloggt als {bot.user}")
    keep_connected.start()

@tasks.loop(seconds=30)
async def keep_connected():
    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        return

    vc = discord.utils.get(bot.voice_clients, guild=channel.guild)

    if vc is None or not vc.is_connected():
        await channel.connect(self_deaf=True)
        print("Voice verbunden")

bot.run(TOKEN)