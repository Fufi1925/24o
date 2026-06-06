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
TARGET_VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID"))
queue = []
is_playing = False
current_title = ""

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
        "-reconnect 1 "
        "-reconnect_streamed 1 "
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
    print(f"🎯 Ziel Channel ID: {TARGET_VOICE_CHANNEL_ID}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="🎵 !play | 24/7"
        )
    )
    await asyncio.sleep(3)
    await join_target_channel()
    stay_in_channel.start()


@bot.event
async def on_voice_state_update(member, before, after):
    """Bot wurde rausgeworfen → sofort wieder joinen."""
    if member.id != bot.user.id:
        return

    if after.channel is None:
        print("⚠️ Bot wurde rausgeworfen! Versuche wieder zu joinen...")
        await asyncio.sleep(3)
        await join_target_channel()


# ══════════════════════════════════════════════════════════
#  HELPER
# ══════════════════════════════════════════════════════════

async def join_target_channel():
    """Joined den Ziel-Voice-Channel mit Self-Mute."""
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)

    if channel is None:
        print("❌ Voice Channel nicht gefunden! Prüfe VOICE_CHANNEL_ID")
        return

    guild = channel.guild
    vc = guild.voice_client

    # Schon im richtigen Channel?
    if vc and vc.is_connected() and vc.channel.id == TARGET_VOICE_CHANNEL_ID:
        print("✅ Bereits im richtigen Channel.")
        return

    # Alten VC trennen
    if vc and vc.is_connected():
        await vc.disconnect(force=True)
        await asyncio.sleep(1)

    try:
        vc = await channel.connect(timeout=30, reconnect=True)
        await asyncio.sleep(1)

        # Self-Mute + Self-Deaf
        await guild.change_voice_state(
            channel=channel,
            self_mute=True,
            self_deaf=True
        )
        print(f"🔊 Verbunden mit: {channel.name} | Muted & Deafened ✅")

    except discord.ClientException as e:
        print(f"❌ ClientException: {e}")
    except asyncio.TimeoutError:
        print("❌ Timeout beim Verbinden!")
    except Exception as e:
        print(f"❌ Unbekannter Fehler: {e}")


@tasks.loop(minutes=3)
async def stay_in_channel():
    """Watchdog: Prüft alle 3 Min ob Bot noch im Channel ist."""
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if channel is None:
        return

    guild = channel.guild
    vc = guild.voice_client

    if vc is None or not vc.is_connected():
        print("🔄 Watchdog: Nicht verbunden → Joinen...")
        await join_target_channel()
    elif vc.channel.id != TARGET_VOICE_CHANNEL_ID:
        print("🔄 Watchdog: Falscher Channel → Wechseln...")
        await join_target_channel()
    else:
        print("✅ Watchdog: Alles OK")


@stay_in_channel.before_loop
async def before_watchdog():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════════════════
#  MUSIK
# ══════════════════════════════════════════════════════════

async def get_audio_url(query: str):
    """YouTube URL oder Suche → Stream URL."""
    loop = asyncio.get_event_loop()

    def extract():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            if not query.startswith("http"):
                search = f"ytsearch:{query}"
            else:
                search = query
            info = ydl.extract_info(search, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info["url"], info["title"], info.get("duration", 0)

    try:
        url, title, duration = await loop.run_in_executor(None, extract)
        return url, title, duration
    except Exception as e:
        print(f"❌ Fehler beim Extrahieren: {e}")
        return None, None, 0


def format_duration(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


async def play_next(guild):
    """Nächstes Lied aus Queue spielen."""
    global is_playing, current_title

    if not queue:
        is_playing = False
        current_title = ""
        # Zurück auf Mute wenn Queue leer
        channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
        if channel and guild.voice_client:
            try:
                await guild.change_voice_state(
                    channel=channel,
                    self_mute=True,
                    self_deaf=True
                )
            except Exception:
                pass
        print("📭 Queue leer → Zurück auf Mute")
        return

    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        await join_target_channel()
        vc = guild.voice_client
        if vc is None:
            is_playing = False
            return

    url, title, duration = queue.pop(0)
    is_playing = True
    current_title = title

    # Beim Spielen Mute aufheben
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    try:
        await guild.change_voice_state(
            channel=channel,
            self_mute=False,
            self_deaf=False
        )
    except Exception as e:
        print(f"⚠️ Voice State Fehler: {e}")

    try:
        source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=0.5)

        def after_playing(error):
            global is_playing
            if error:
                print(f"❌ Player Fehler: {error}")
            is_playing = False
            asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

        vc.play(source, after=after_playing)
        print(f"▶️ Spiele: {title} [{format_duration(duration)}]")

    except Exception as e:
        print(f"❌ Fehler beim Abspielen: {e}")
        is_playing = False
        await play_next(guild)


# ══════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════

@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query: str):
    """!play <Song Name oder YouTube URL>"""
    guild = ctx.guild

    # Bot im Channel?
    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        await join_target_channel()

    msg = await ctx.send(f"🔍 Suche: **{query}**...")

    url, title, duration = await get_audio_url(query)

    if url is None:
        await msg.edit(content="❌ Song nicht gefunden!")
        return

    queue.append((url, title, duration))

    embed = discord.Embed(color=discord.Color.green())
    embed.add_field(name="✅ Zur Queue hinzugefügt", value=f"**{title}**", inline=False)
    embed.add_field(name="⏱️ Länge", value=format_duration(duration), inline=True)
    embed.add_field(name="📋 Position", value=f"#{len(queue)}", inline=True)
    await msg.edit(content=None, embed=embed)

    if not is_playing:
        await play_next(guild)


@bot.command(name="skip", aliases=["s"])
async def skip(ctx):
    """Aktuellen Song überspringen."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.send("⏭️ Übersprungen!")
    else:
        await ctx.send("❌ Gerade läuft nichts.")


@bot.command(name="queue", aliases=["q"])
async def show_queue(ctx):
    """Queue anzeigen."""
    if not queue and not is_playing:
        await ctx.send("📭 Queue ist leer.")
        return

    embed = discord.Embed(
        title="📋 Musik Queue",
        color=discord.Color.blue()
    )

    if current_title:
        embed.add_field(
            name="▶️ Spielt gerade",
            value=f"**{current_title}**",
            inline=False
        )

    if queue:
        queue_text = ""
        for i, (_, title, duration) in enumerate(queue[:10], 1):
            queue_text += f"`{i}.` {title} `[{format_duration(duration)}]`\n"

        if len(queue) > 10:
            queue_text += f"\n*...und {len(queue) - 10} weitere Songs*"

        embed.add_field(
            name="📋 Warteschlange",
            value=queue_text,
            inline=False
        )
    else:
        embed.add_field(
            name="📋 Warteschlange",
            value="*Leer*",
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command(name="stop")
async def stop(ctx):
    """Musik stoppen und Queue leeren."""
    global queue, is_playing, current_title
    vc = ctx.guild.voice_client

    if vc and vc.is_playing():
        vc.stop()

    queue.clear()
    is_playing = False
    current_title = ""

    # Zurück auf Mute
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if channel:
        await ctx.guild.change_voice_state(
            channel=channel,
            self_mute=True,
            self_deaf=True
        )

    await ctx.send("⏹️ Gestoppt & Queue geleert.")


@bot.command(name="volume", aliases=["vol", "v"])
async def volume(ctx, vol: int):
    """Lautstärke: !volume 0-100"""
    vc = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        if 0 <= vol <= 100:
            vc.source.volume = vol / 100
            await ctx.send(f"🔊 Lautstärke: **{vol}%**")
        else:
            await ctx.send("❌ Wert muss zwischen 0 und 100 sein!")
    else:
        await ctx.send("❌ Gerade läuft nichts.")


@bot.command(name="pause")
async def pause(ctx):
    """Musik pausieren."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Pausiert.")
    else:
        await ctx.send("❌ Läuft gerade nichts.")


@bot.command(name="resume", aliases=["r"])
async def resume(ctx):
    """Musik fortsetzen."""
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Fortgesetzt.")
    else:
        await ctx.send("❌ Nichts pausiert.")


@bot.command(name="nowplaying", aliases=["np", "current"])
async def nowplaying(ctx):
    """Aktuellen Song anzeigen."""
    if is_playing and current_title:
        embed = discord.Embed(
            title="▶️ Spielt gerade",
            description=f"**{current_title}**",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Queue: {len(queue)} Songs")
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Gerade läuft nichts.")


@bot.command(name="join")
async def join(ctx):
    """Bot manuell in Ziel-Channel rufen."""
    await join_target_channel()
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    await ctx.send(f"✅ Verbunden mit **{channel.name}**!")


@bot.command(name="clear")
async def clear_queue(ctx):
    """Queue leeren (Song läuft weiter)."""
    global queue
    queue.clear()
    await ctx.send("🗑️ Queue geleert!")


@bot.command(name="bothelp", aliases=["h", "commands"])
async def bothelp(ctx):
    """Alle Commands anzeigen."""
    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Prefix: `!`",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="🎵 Musik",
        value=(
            "`!play <Song/URL>` - Song abspielen\n"
            "`!skip` - Überspringen\n"
            "`!stop` - Stoppen\n"
            "`!pause` - Pausieren\n"
            "`!resume` - Fortsetzen\n"
            "`!volume <0-100>` - Lautstärke\n"
            "`!queue` - Queue anzeigen\n"
            "`!clear` - Queue leeren\n"
            "`!nowplaying` - Aktueller Song"
        ),
        inline=False
    )
    embed.add_field(
        name="🔊 Voice",
        value="`!join` - Bot in Channel rufen",
        inline=False
    )
    embed.set_footer(text="Bot ist 24/7 im Voice Channel aktiv")
    await ctx.send(embed=embed)


# ══════════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════════

token = os.getenv("DISCORD_TOKEN")
if not token:
    print("❌ DISCORD_TOKEN fehlt in den Environment Variables!")
    exit(1)

if not os.getenv("VOICE_CHANNEL_ID"):
    print("❌ VOICE_CHANNEL_ID fehlt in den Environment Variables!")
    exit(1)

bot.run(token)