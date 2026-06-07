import asyncio
import logging
import os
import discord

# Discord-Logs ausblenden
logging.getLogger("discord").setLevel(logging.CRITICAL)
logging.getLogger("discord.http").setLevel(logging.CRITICAL)
logging.getLogger("discord.gateway").setLevel(logging.CRITICAL)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID"))

STATUSES = [
    "Status 1", "Status 2", "Status 3", "Status 4", "Status 5",
    "Status 6", "Status 7", "Status 8", "Status 9", "Status 10",
    "Status 11", "Status 12", "Status 13", "Status 14", "Status 15",
    "Status 16", "Status 17", "Status 18", "Status 19", "Status 20",
    "Status 21", "Status 22", "Status 23", "Status 24", "Status 25",
    "Status 26", "Status 27", "Status 28", "Status 29", "Status 30",
    "Status 31", "Status 32", "Status 33", "Status 34", "Status 35",
    "Status 36", "Status 37", "Status 38", "Status 39", "Status 40"
]

class KeepAliveBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Bot online als {self.user}")
        self.loop.create_task(self.maintain_voice())
        self.loop.create_task(self.rotate_status())

    async def rotate_status(self):
        while True:
            for status in STATUSES:
                try:
                    await self.change_presence(
                        activity=discord.CustomActivity(name=status)
                    )
                except Exception:
                    print("⚠ Status konnte nicht gesetzt werden")
                await asyncio.sleep(3)

    async def maintain_voice(self):
        while True:
            try:
                channel = self.get_channel(CHANNEL_ID)

                if not isinstance(channel, discord.VoiceChannel):
                    print("⚠ Voicechannel nicht gefunden")
                    await asyncio.sleep(30)
                    continue

                vc = channel.guild.voice_client

                if vc and vc.is_connected():
                    await asyncio.sleep(60)
                    continue

                print(f"🎤 Verbinde mit {channel.name}")
                await channel.connect(self_deaf=True, self_mute=True)
                print("✅ Verbunden")

            except Exception as e:
                print(f"⚠ Verbindungsproblem: {e}")

            await asyncio.sleep(60)

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.voice_states = True

    bot = KeepAliveBot(intents=intents)
    bot.run(TOKEN, log_handler=None)