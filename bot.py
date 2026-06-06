import asyncio
import os
import discord

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID"))

class KeepAliveBot(discord.Client):
    async def on_ready(self):
        print(f"Bot online als {self.user}")
        self.loop.create_task(self.maintain_voice())

    async def maintain_voice(self):
        while True:
            # Finde Channel
            channel = self.get_channel(CHANNEL_ID)
            if not isinstance(channel, discord.VoiceChannel):
                print("Voicechannel nicht gefunden – warte 30s")
                await asyncio.sleep(30)
                continue

            # Prüfen, ob bereits verbunden
            for guild in self.guilds:
                if guild.voice_client and guild.voice_client.is_connected():
                    if guild.voice_client.channel.id == CHANNEL_ID:
                        print("Bereits im richtigen Channel – warte 60s")
                        await asyncio.sleep(60)
                        continue

            # Versuch, zu verbinden
            try:
                print(f"Verbinde mit {channel.name} ...")
                vc = await asyncio.wait_for(
                    channel.connect(self_deaf=True, self_mute=True),
                    timeout=30
                )
                print("Erfolgreich verbunden!")
                await asyncio.sleep(60)  # Halte Check-Intervall
            except Exception as e:
                print(f"Fehler: {e} – warte 60s")
                await asyncio.sleep(60)

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.voice_states = True
    bot = KeepAliveBot(intents=intents)
    bot.run(TOKEN)