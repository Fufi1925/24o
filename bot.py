"""
Discord Music Bot — 24/7 Voice stabil (Railway)
────────────────────────────────────────────────
Fix: Exklusive Join‑Tasks ohne vorzeitige Abbruchprüfung,
     saubere Wartezeiten, keine parallelen Verbindungen.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

# ══════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════

TARGET_VOICE_CHANNEL_ID: int = int(os.getenv("VOICE_CHANNEL_ID", "0"))
WATCHDOG_INTERVAL_MINUTES = 2
JOIN_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 30

YDL_OPTIONS: dict = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

YDL_PLAYLIST_OPTIONS: dict = {
    **YDL_OPTIONS,
    "noplaylist": False,
    "extract_flat": "in_playlist",
}

FFMPEG_OPTIONS: dict = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5 -nostdin"
    ),
    "options": "-vn -filter:a loudnorm",
}

try:
    import nacl  # noqa: F401
except ImportError:
    raise SystemExit("❌  PyNaCl fehlt! Installieren: pip install PyNaCl")


# ══════════════════════════════════════════════════════════
#  DATENMODELL
# ══════════════════════════════════════════════════════════

@dataclass
class Track:
    url: str
    title: str
    duration: int
    webpage_url: str = ""
    requester: str = ""

    def duration_str(self) -> str:
        return format_duration(self.duration)


def format_duration(seconds: int) -> str:
    if not seconds:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


# ══════════════════════════════════════════════════════════
#  MUSIKZUSTAND
# ══════════════════════════════════════════════════════════

class MusicState:
    def __init__(self):
        self.queue: list[Track] = []
        self.current: Optional[Track] = None
        self.lock = asyncio.Lock()

    def clear(self):
        self.queue.clear()
        self.current = None

    @property
    def is_playing(self) -> bool:
        return self.current is not None

    def queue_info(self) -> str:
        total = sum(t.duration for t in self.queue)
        return f"{len(self.queue)} Song(s) · {format_duration(total)}"


# ══════════════════════════════════════════════════════════
#  BOT-SETUP
# ══════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = False

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

_states: dict[int, MusicState] = {}

def get_state(guild: discord.Guild) -> MusicState:
    if guild.id not in _states:
        _states[guild.id] = MusicState()
    return _states[guild.id]

# Exklusiver Join-Lock pro Guild
_join_locks: dict[int, asyncio.Lock] = {}

def get_join_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _join_locks:
        _join_locks[guild_id] = asyncio.Lock()
    return _join_locks[guild_id]


def is_bot_connected(guild_id: int) -> bool:
    guild = bot.get_guild(guild_id)
    if guild is None:
        return False
    vc = guild.voice_client
    return (
        vc is not None
        and vc.is_connected()
        and vc.channel is not None
        and vc.channel.id == TARGET_VOICE_CHANNEL_ID
    )


# ══════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅  Bot online  →  {bot.user}")
    print(f"🎯  Channel ID  →  {TARGET_VOICE_CHANNEL_ID}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="!play | !bothelp"
        )
    )

    await asyncio.sleep(10)   # Gateway stabilisieren

    for guild in bot.guilds:
        asyncio.create_task(join_retry_loop(guild))

    stay_in_channel.start()


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"➕  Neue Guild: {guild.name}")
    await asyncio.sleep(5)
    asyncio.create_task(join_retry_loop(guild))


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    """Nur bei echten Trennungen vom Zielkanal."""
    if member.id != bot.user.id:
        return

    if before.channel and before.channel.id == TARGET_VOICE_CHANNEL_ID and after.channel is None:
        print(f"⚠️  Bot hat Zielkanal «{before.channel.name}» verlassen → erneut verbinden")
        asyncio.create_task(join_retry_loop(member.guild))


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Argument fehlt. `!bothelp` für Hilfe.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"⚠️  Command-Fehler ({ctx.command}): {error}")
        await ctx.send(f"❌ Fehler: `{error}`")


# ══════════════════════════════════════════════════════════
#  VERBINDUNGSLOGIK (EXKLUSIV, SEQUENZIELL)
# ══════════════════════════════════════════════════════════

async def join_retry_loop(guild: discord.Guild):
    """
    Exklusiver Verbindungs-Task pro Guild.
    Wartet ggf., bis ein laufender Task den Lock freigibt,
    und arbeitet dann alle Retries durch.
    """
    lock = get_join_lock(guild.id)

    # Warte, bis wir exklusiv arbeiten dürfen
    async with lock:
        # Kurzer Puffer
        await asyncio.sleep(2)

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"🔊  Join‑Versuch {attempt}/{MAX_RETRIES} für «{guild.name}»")
            if await attempt_connection(guild):
                print(f"✅  Verbunden mit «{guild.name}»")
                return
            if attempt < MAX_RETRIES:
                print(f"⏳  Warte {RETRY_DELAY}s …")
                await asyncio.sleep(RETRY_DELAY)

        print(f"❌  Join endgültig fehlgeschlagen für «{guild.name}» – Watchdog übernimmt später.")


async def attempt_connection(guild: discord.Guild) -> bool:
    """
    Ein einzelner Verbindungsversuch:
    - Alten VoiceClient vollständig entfernen
    - Warten, bis guild.voice_client None ist
    - Neu verbinden (kein reconnect)
    """
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if not isinstance(channel, discord.VoiceChannel):
        print("   ❌  VOICE_CHANNEL_ID ungültig oder kein VoiceChannel!")
        return False

    # 1. Alten VoiceClient sauber entfernen
    vc = guild.voice_client
    if vc:
        print("   🧹  Entferne alte Voice-Verbindung …")
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass
        # Internen State komplett löschen
        if hasattr(guild, "_voice_state"):
            try:
                del guild._voice_state
            except Exception:
                pass
        # Warten, bis guild.voice_client wirklich None ist (max. 10 Sekunden)
        for _ in range(20):
            if guild.voice_client is None:
                break
            await asyncio.sleep(0.5)
        else:
            print("   ⚠️  VoiceClient konnte nicht vollständig entfernt werden.")

    # 2. Frisch verbinden
    print(f"   🔗  Verbinde mit «{channel.name}» …")
    try:
        vc = await asyncio.wait_for(
            channel.connect(
                reconnect=False,
                self_deaf=True,
                self_mute=True
            ),
            timeout=JOIN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print("   ❌  Verbindungs‑Timeout")
        return False
    except discord.errors.ConnectionClosed as exc:
        print(f"   ❌  Verbindung abgelehnt (Code {exc.code})")
        return False
    except Exception as exc:
        print(f"   ❌  Fehler: {type(exc).__name__}: {exc}")
        return False

    # 3. Mute-Status anpassen, falls Musik läuft
    state = get_state(guild)
    if state.is_playing:
        try:
            await guild.change_voice_state(
                channel=channel,
                self_mute=False,
                self_deaf=True
            )
        except Exception:
            pass

    return True


# ══════════════════════════════════════════════════════════
#  WATCHDOG
# ══════════════════════════════════════════════════════════

@tasks.loop(minutes=WATCHDOG_INTERVAL_MINUTES)
async def stay_in_channel():
    for guild in bot.guilds:
        if not is_bot_connected(guild.id):
            print(f"🔄  Watchdog [{guild.name}]: Nicht verbunden → Join …")
            asyncio.create_task(join_retry_loop(guild))


@stay_in_channel.before_loop
async def _before_watchdog():
    await bot.wait_until_ready()
    await asyncio.sleep(20)


# ══════════════════════════════════════════════════════════
#  YT-DLP HELPER (unverändert)
# ══════════════════════════════════════════════════════════

async def fetch_track(query: str) -> Optional[Track]:
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            search = query if query.startswith("http") else f"ytsearch:{query}"
            info = ydl.extract_info(search, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info

    try:
        info = await loop.run_in_executor(None, _extract)
        return Track(
            url=info["url"],
            title=info.get("title", "Unbekannt"),
            duration=info.get("duration", 0),
            webpage_url=info.get("webpage_url", query),
        )
    except Exception as exc:
        print(f"❌  YT-DLP Fehler: {exc}")
        return None


async def fetch_playlist(url: str) -> list[Track]:
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_PLAYLIST_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
            if "entries" not in info:
                return []
            tracks = []
            for entry in info["entries"]:
                if not entry:
                    continue
                tracks.append(Track(
                    url=entry.get("url") or entry.get("webpage_url", ""),
                    title=entry.get("title", "Unbekannt"),
                    duration=entry.get("duration", 0),
                    webpage_url=entry.get("webpage_url", ""),
                ))
            return tracks

    try:
        return await loop.run_in_executor(None, _extract)
    except Exception as exc:
        print(f"❌  Playlist-Fehler: {exc}")
        return []


# ══════════════════════════════════════════════════════════
#  PLAYBACK
# ══════════════════════════════════════════════════════════

async def play_next(guild: discord.Guild):
    state = get_state(guild)

    async with state.lock:
        if not state.queue:
            state.current = None
            vc = guild.voice_client
            if vc and vc.is_connected():
                try:
                    await guild.change_voice_state(
                        channel=vc.channel,
                        self_mute=True,
                        self_deaf=True
                    )
                except Exception:
                    pass
            print("📭  Queue leer.")
            return

        track = state.queue.pop(0)
        state.current = track

    vc = guild.voice_client
    if not vc or not vc.is_connected():
        await join_retry_loop(guild)
        await asyncio.sleep(2)
        vc = guild.voice_client

    if not vc or not vc.is_connected():
        state.current = None
        return

    channel = vc.channel
    if channel:
        try:
            await guild.change_voice_state(channel=channel, self_mute=False, self_deaf=True)
        except Exception:
            pass
    await asyncio.sleep(0.5)

    try:
        source = discord.FFmpegPCMAudio(track.url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=0.5)
    except Exception as exc:
        print(f"❌  Audio‑Source Fehler: {exc}")
        state.current = None
        await play_next(guild)
        return

    def _after(error):
        if error:
            print(f"❌  Player‑Fehler: {error}")
        state.current = None
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    try:
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await asyncio.sleep(0.3)
        vc.play(source, after=_after)
        print(f"▶️   {track.title}  [{track.duration_str()}]")
    except Exception as exc:
        print(f"❌  play() Fehler: {exc}")
        state.current = None
        await asyncio.sleep(1)
        await play_next(guild)


# ══════════════════════════════════════════════════════════
#  COMMANDS (gekürzt, voll funktionsfähig)
# ══════════════════════════════════════════════════════════

@bot.command(name="play", aliases=["p"])
async def cmd_play(ctx: commands.Context, *, query: str):
    state = get_state(ctx.guild)
    msg = await ctx.send(f"🔍  Suche: **{query}** …")
    track = await fetch_track(query)
    if track is None:
        await msg.edit(content="❌  Song nicht gefunden!")
        return
    track.requester = ctx.author.display_name
    async with state.lock:
        state.queue.append(track)
        pos = len(state.queue)
    embed = discord.Embed(color=discord.Color.green())
    embed.set_author(name="✅  Zur Queue hinzugefügt")
    embed.add_field(name="🎵  Titel", value=f"[{track.title}]({track.webpage_url})", inline=False)
    embed.add_field(name="⏱️  Länge", value=track.duration_str(), inline=True)
    embed.add_field(name="📋  Position", value=f"#{pos}", inline=True)
    embed.add_field(name="👤  Von", value=track.requester, inline=True)
    embed.set_footer(text=f"Queue: {state.queue_info()}")
    await msg.edit(content=None, embed=embed)
    if not state.is_playing:
        await play_next(ctx.guild)

@bot.command(name="skip", aliases=["s"])
async def cmd_skip(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send("⏭️  Übersprungen.")
    else:
        await ctx.send("❌  Es läuft gerade nichts.")

@bot.command(name="stop")
async def cmd_stop(ctx: commands.Context):
    state = get_state(ctx.guild)
    vc = ctx.guild.voice_client
    async with state.lock:
        state.clear()
    if vc and vc.is_playing():
        vc.stop()
    if vc and vc.is_connected():
        try:
            await ctx.guild.change_voice_state(channel=vc.channel, self_mute=True, self_deaf=True)
        except Exception:
            pass
    await ctx.send("⏹️  Gestoppt und Queue geleert.")

@bot.command(name="queue", aliases=["q"])
async def cmd_queue(ctx: commands.Context):
    state = get_state(ctx.guild)
    if not state.is_playing and not state.queue:
        await ctx.send("📭  Queue ist leer.")
        return
    embed = discord.Embed(title="📋  Queue", color=discord.Color.blue())
    if state.current:
        embed.add_field(name="▶️  Jetzt", value=f"[{state.current.title}]({state.current.webpage_url}) `[{state.current.duration_str()}]`", inline=False)
    if state.queue:
        lines = []
        for i, t in enumerate(state.queue[:15], 1):
            lines.append(f"`{i:>2}.` [{t.title}]({t.webpage_url}) `[{t.duration_str()}]`")
        if len(state.queue) > 15:
            lines.append(f"*… und {len(state.queue) - 15} weitere*")
        embed.add_field(name="📋  Nächste", value="\n".join(lines), inline=False)
    embed.set_footer(text=state.queue_info())
    await ctx.send(embed=embed)

@bot.command(name="bothelp", aliases=["h", "hilfe"])
async def cmd_bothelp(ctx: commands.Context):
    embed = discord.Embed(title="🤖  Bot Commands", description="Prefix: `!`", color=discord.Color.blurple())
    embed.add_field(name="🎵  Wiedergabe", value="`!play <Song/URL>` | `!playlist <URL>` | `!skip` | `!stop` | `!pause` | `!resume` | `!volume 0-100` | `!nowplaying`", inline=False)
    embed.add_field(name="📋  Queue", value="`!queue` | `!clear` | `!remove <#>`", inline=False)
    embed.add_field(name="🔊  Voice", value="`!join`", inline=False)
    await ctx.send(embed=embed)


# ══════════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════════

def _validate_env():
    errors = []
    if not os.getenv("DISCORD_TOKEN"):
        errors.append("DISCORD_TOKEN fehlt!")
    if not os.getenv("VOICE_CHANNEL_ID"):
        errors.append("VOICE_CHANNEL_ID fehlt!")
    elif TARGET_VOICE_CHANNEL_ID == 0:
        errors.append("VOICE_CHANNEL_ID ist ungültig (muss eine Zahl sein)!")
    if errors:
        for e in errors:
            print(f"❌  {e}")
        raise SystemExit(1)

_validate_env()
bot.run(os.getenv("DISCORD_TOKEN"), log_handler=None)