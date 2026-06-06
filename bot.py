import discord
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# ── Bot Setup ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Globals ────────────────────────────────────────────────
TARGET_VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID"))  # Dein Channel
queue = []
is_playing = False

# ── YT-DLP Options ────────────────────────────────────────
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}

# ══════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Bot online als {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="🎵 !play | 24/7 Online"
        )
    )
    await join_target_channel()
    stay_in_channel.start()


@bot.event
async def on_voice_state_update(member, before, after):
    """Verhindert, dass der Bot rausgeworfen wird."""
    if member.id == bot.user.id:
        if after.channel is None:
            # Bot wurde rausgeworfen → sofort wieder joinen
            await asyncio.sleep(2)
            await join_target_channel()


# ══════════════════════════════════════════════════════════
#  HELPER FUNKTIONEN
# ══════════════════════════════════════════════════════════

async def join_target_channel():
    """Joined den Ziel-Voice-Channel und setzt sich auf Full Mute."""
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if channel is None:
        print("❌ Voice Channel nicht gefunden!")
        return

    guild = channel.guild

    # Schon drin?
    if guild.voice_client and guild.voice_client.channel.id == TARGET_VOICE_CHANNEL_ID:
        print("✅ Bereits im Channel.")
        return

    # Alten VC verlassen falls nötig
    if guild.voice_client:
        await guild.voice_client.disconnect()

    try:
        vc = await channel.connect()
        # Self-Mute aktivieren (Bot hört nichts + sendet nichts → spart Ressourcen)
        await guild.change_voice_state(
            channel=channel,
            self_mute=True,
            self_deaf=True
        )
        print(f"🔊 Beigetreten: {channel.name} | Self-Muted & Deafened")
    except Exception as e:
        print(f"❌ Fehler beim Joinen: {e}")


@tasks.loop(minutes=5)
async def stay_in_channel():
    """Prüft alle 5 Minuten ob der Bot noch im Channel ist."""
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if channel is None:
        return

    guild = channel.guild
    vc = guild.voice_client

    if vc is None or not vc.is_connected():
        print("⚠️ Nicht im Channel – versuche zu joinen...")
        await join_target_channel()
    elif vc.channel.id != TARGET_VOICE_CHANNEL_ID:
        print("⚠️ Falscher Channel – wechsle zurück...")
        await join_target_channel()


# ══════════════════════════════════════════════════════════
#  MUSIK FUNKTIONEN
# ══════════════════════════════════════════════════════════

async def search_and_get_url(query: str):
    """Sucht auf YouTube und gibt Stream-URL + Titel zurück."""
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            # Wenn kein Link → YouTube suchen
            if not query.startswith("http"):
                query = f"ytsearch:{query}"
            info = ydl.extract_info(query, download=False)

            # Playlist oder einzelnes Video?
            if "entries" in info:
                info = info["entries"][0]

            return info["url"], info["title"]
        except Exception as e:
            print(f"❌ Fehler beim Suchen: {e}")
            return None, None


async def play_next(guild):
    """Spielt das nächste Lied aus der Queue."""
    global is_playing

    if not queue:
        is_playing = False
        # Nach Musik: Wieder auf Self-Mute setzen
        channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
        if channel and guild.voice_client:
            await guild.change_voice_state(
                channel=channel,
                self_mute=True,
                self_deaf=True
            )
        return

    vc = guild.voice_client
    if vc is None:
        await join_target_channel()
        vc = guild.voice_client

    url, title = queue.pop(0)
    is_playing = True

    # Beim Spielen: Self-Mute aufheben
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    await guild.change_voice_state(
        channel=channel,
        self_mute=False,
        self_deaf=False
    )

    source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
    source = discord.PCMVolumeTransformer(source, volume=0.5)

    def after_playing(error):
        if error:
            print(f"❌ Player Fehler: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    vc.play(source, after=after_playing)
    print(f"▶️ Spiele: {title}")


# ══════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════

@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query: str):
    """Spielt Musik oder fügt zur Queue hinzu."""
    
    # Prüfen ob Bot im Ziel-Channel ist
    guild = ctx.guild
    vc = guild.voice_client

    if vc is None or not vc.is_connected():
        await join_target_channel()
        vc = guild.voice_client

    await ctx.send(f"🔍 Suche nach: **{query}**")

    url, title = await search_and_get_url(query)
    if url is None:
        await ctx.send("❌ Konnte das Lied nicht finden!")
        return

    queue.append((url, title))
    await ctx.send(f"✅ Zur Queue hinzugefügt: **{title}**")

    if not is_playing:
        await play_next(guild)
        await ctx.send(f"▶️ Spiele jetzt: **{title}**")


@bot.command(name="skip", aliases=["s"])
async def skip(ctx):
    """Überspringt das aktuelle Lied."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.send("⏭️ Übersprungen!")
    else:
        await ctx.send("❌ Gerade läuft nichts.")


@bot.command(name="queue", aliases=["q"])
async def show_queue(ctx):
    """Zeigt die aktuelle Queue."""
    if not queue:
        await ctx.send("📭 Die Queue ist leer.")
        return

    msg = "**📋 Aktuelle Queue:**\n"
    for i, (_, title) in enumerate(queue, 1):
        msg += f"`{i}.` {title}\n"
    await ctx.send(msg)


@bot.command(name="stop")
async def stop(ctx):
    """Stoppt die Musik und leert die Queue."""
    global queue, is_playing
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
    queue.clear()
    is_playing = False

    # Zurück auf Mute
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    await ctx.guild.change_voice_state(
        channel=channel,
        self_mute=True,
        self_deaf=True
    )
    await ctx.send("⏹️ Musik gestoppt & Queue geleert.")


@bot.command(name="volume", aliases=["vol"])
async def volume(ctx, vol: int):
    """Lautstärke einstellen (0-100)."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        if 0 <= vol <= 100:
            vc.source.volume = vol / 100
            await ctx.send(f"🔊 Lautstärke: **{vol}%**")
        else:
            await ctx.send("❌ Wert zwischen 0 und 100!")
    else:
        await ctx.send("❌ Gerade läuft nichts.")


@bot.command(name="pause")
async def pause(ctx):
    """Pausiert die Musik."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Pausiert.")


@bot.command(name="resume")
async def resume(ctx):
    """Setzt die Musik fort."""
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Fortgesetzt.")


@bot.command(name="join")
async def join(ctx):
    """Bot manuell in den Ziel-Channel rufen."""
    await join_target_channel()
    await ctx.send(f"✅ Bin im Channel!")


@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying(ctx):
    """Zeigt das aktuelle Lied."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        await ctx.send("▶️ Musik läuft gerade!")
    else:
        await ctx.send("❌ Gerade läuft nichts.")


@bot.command(name="bothelp")
async def bothelp(ctx):
    """Hilfe anzeigen."""
    embed = discord.Embed(
        title="🤖 Bot Commands",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🎵 Musik",
        value=(
            "`!play <Song/URL>` - Musik spielen\n"
            "`!skip` - Überspringen\n"
            "`!stop` - Stoppen\n"
            "`!pause` - Pausieren\n"
            "`!resume` - Fortsetzen\n"
            "`!volume <0-100>` - Lautstärke\n"
            "`!queue` - Queue anzeigen\n"
            "`!nowplaying` - Aktuelles Lied"
        ),
        inline=False
    )
    embed.add_field(
        name="🔊 Voice",
        value=(
            "`!join` - Bot in Channel rufen\n"
        ),
        inline=False
    )
    await ctx.send(embed=embed)


# ══════════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════════

bot.run(os.getenv("DISCORD_TOKEN"))