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
    "🔊 Im Sprachkanal",
    "🎙️ Voice aktiv",
    "🌙 24/7 im Voice",
    "🤖 Immer erreichbar",
    "⚡ Online",
    "🛡️ Server schützen",
    "📡 Verbunden",
    "🚀 Bereit",
    "👀 Beobachte den Server",
    "🔧 Systeme laufen",
    "💻 Bot aktiv",
    "🌍 Rund um die Uhr",
    "🎯 Einsatzbereit",
    "📊 Server überwachen",
    "🔄 Stabil verbunden",
    "☁️ Cloud Power",
    "🔋 Volle Leistung",
    "🏓 Niedriger Ping",
    "🌟 Premium Qualität",
    "🔒 Sicherheit aktiv",
    "🚨 Schutzmodus",
    "📈 Alles läuft",
    "💬 Hilfe verfügbar",
    "🛠️ Wartungsfrei",
    "🖥️ System online",
    "🌐 Immer da",
    "🤝 Für die Community",
    "⭐ Zuverlässig",
    "⚙️ Automatisiert",
    "🚁 Voice überwachen",
    "📲 Discord Bot",
    "🔍 Aktiv",
    "💎 Stabil",
    "🚀 Schnell",
    "🎮 Voice Service",
    "🕒 Keine Ausfälle",
    "📢 Support aktiv",
    "🔥 Leistungsstark",
    "🎯 Fokus auf Voice",
    "🌙 Nachtwache",
    "☀️ Tagschicht",
    "📡 Voice verbunden",
    "🔗 Verbindung stabil",
    "🧠 Intelligente Funktionen",
    "🏆 Top Performance",
    "⚡ Echtzeit aktiv",
    "🌍 Server Network",
    "🛡️ Voice Guard",
    "🤖 Discord Assistant",
    "❤️ Danke für eure Nutzung",
    "🎙️ Voice Connected",
    "🌐 Netzwerk aktiv",
    "⚙️ Dienste bereit",
    "🔋 Energie geladen",
    "🚀 Performance Mode",
    "📡 Signal stark",
    "🛡️ Sicherheit zuerst",
    "🔄 Dauerbetrieb",
    "💻 Server verbunden",
    "🔗 Voice Online",
    "👁️ Server Watch",
    "🛰️ Netzwerk überwacht",
    "⚡ Volle Geschwindigkeit",
    "🏆 Premium Service",
    "📊 Status OK",
    "🤖 Helfer aktiv",
    "🌙 Nachtbetrieb",
    "☀️ Tagesbetrieb",
    "🔍 Überwachung läuft",
    "📢 Bereit für Support",
    "🛠️ Systeme stabil",
    "🎯 Voice Ready",
    "💎 Hochverfügbar",
    "🚨 Schutz aktiv",
    "📈 Optimale Leistung",
    "🔒 Geschützt",
    "🤝 Community Support",
    "🌍 Global Online",
    "⚙️ Automatik aktiv",
    "🔄 Synchronisiert",
    "📶 Beste Verbindung",
    "🖥️ Service Online",
    "🎙️ Voice Service",
    "🔗 Netzwerk stabil",
    "📡 Signal gefunden",
    "🧠 Smart System",
    "🚀 Schnell verbunden",
    "⚡ Reaktionsbereit",
    "🔋 Power Mode",
    "🌐 Cloud Verbunden",
    "📊 Monitoring aktiv",
    "🛡️ Schutzschild aktiv",
    "🔄 Keine Unterbrechung",
    "🎯 Immer bereit",
    "🤖 Bot läuft",
    "💻 Runtime aktiv",
    "📶 Voice Link",
    "🌍 Worldwide Online",
    "🔗 Verbindung OK",
    "🚁 Voice Patrol",
    "🎙️ Voice Manager",
    "🛰️ Online Station",
    "🔧 Betriebsbereit",
    "📡 Netzwerkstatus OK",
    "⚡ Ultra Fast",
    "🔒 Secure Mode",
    "📈 Stabilität 100%",
    "🤖 Voice Assistant",
    "🛡️ Guardian Mode",
    "🌐 Online Hub",
    "🚀 Keine Downtime",
    "💎 Premium Voice",
    "🎯 Service aktiv",
    "📊 Live Monitoring",
    "⚙️ Vollautomatisch",
    "🧠 KI aktiv",
    "🔄 Dauerhaft verbunden",
    "📡 Voice Node",
    "🌍 Netzwerk aktiv",
    "🚨 Alarmbereit",
    "🏆 Höchstleistung",
    "🎙️ Voice Protection",
    "💻 Infrastruktur aktiv",
    "🔗 Voice Bridge",
    "⚡ System Ready",
    "🌐 Server Sync",
    "📊 Echtzeitstatus",
    "🛡️ Voice Security",
    "🔒 Maximale Sicherheit",
    "🚀 Performance aktiv",
    "🎯 Immer online",
    "💎 Premium Netzwerk",
    "📡 Stabile Leitung",
    "🤖 Utility Bot",
    "🌙 Wach im Voice",
    "☀️ Bereit am Tag",
    "🔧 Wartung abgeschlossen",
    "📈 Alles stabil",
    "🛰️ Verbindung aktiv",
    "⚡ Ping optimal",
    "🔗 Dauerverbindung",
    "🎙️ Voice Core",
    "🌐 Network Core",
    "🛡️ Server Guardian",
    "💻 Cloud System",
    "🚀 Voice Engine",
    "📡 Signal verfügbar",
    "⚙️ Betriebsmodus aktiv",
    "🤖 Automatischer Dienst",
    "💎 Qualität garantiert"
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