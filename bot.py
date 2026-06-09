import os
import asyncio
import discord

TOKEN_1 = os.getenv("DISCORD_TOKEN_1")
TOKEN_2 = os.getenv("DISCORD_TOKEN_2")
CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID"))

class VoiceBot(discord.Client):
    def __init__(self, leave_after_hours):
        super().__init__(intents=discord.Intents.default())
        self.leave_after = leave_after_hours * 3600

    async def on_ready(self):
        print(f"✅ Online: {self.user}")
        self.loop.create_task(self.voice_loop())

    async def voice_loop(self):
        await self.wait_until_ready()

        while not self.is_closed():
            channel = self.get_channel(CHANNEL_ID)

            if channel:
                vc = discord.utils.get(
                    self.voice_clients,
                    guild=channel.guild
                )

                if vc is None or not vc.is_connected():
                    try:
                        await channel.connect(self_deaf=True)
                        print(f"{self.user} → Voice verbunden")
                    except Exception as e:
                        print(f"{self.user} → Fehler beim Joinen: {e}")

                await asyncio.sleep(self.leave_after)

                vc = discord.utils.get(
                    self.voice_clients,
                    guild=channel.guild
                )

                if vc and vc.is_connected():
                    try:
                        await vc.disconnect()
                        print(f"{self.user} → Voice verlassen")
                    except Exception as e:
                        print(f"{self.user} → Fehler beim Verlassen: {e}")

                await asyncio.sleep(60)

async def main():
    bot1 = VoiceBot(23)  # 23 Stunden
    bot2 = VoiceBot(26)  # 26 Stunden

    await asyncio.gather(
        bot1.start(TOKEN_1),
        bot2.start(TOKEN_2)
    )

asyncio.run(main())