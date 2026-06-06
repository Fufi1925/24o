"""
Discord Music Bot
─────────────────
Requires: discord.py, yt-dlp, python-dotenv, PyNaCl
.env:  DISCORD_TOKEN=...
       VOICE_CHANNEL_ID=...
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

YDL_OPTIONS: dict = {
    "format": "bestaudio/best",
    "noplaylist": True,       # Playlists explizit über !playlist
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

YDL_PLAYLIST_OPTIONS: dict = {
    **YDL_OPTIONS,
    "noplaylist": False,
    "extract_flat": "in_playlist",  # Schnelleres Laden
}

FFMPEG_OPTIONS: dict = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5 -nostdin"
    ),
    "options": "-vn -filter:a loudnorm",   # Lautstärke normalisieren
}

WATCHDOG_INTERVAL_MINUTES = 10
JOIN_TIMEOUT_SECONDS = 30
RECONNECT_DELAY_SECONDS = 8


# ══════════════════════════════════════════════════════════
#  DATENMODELL
# ══════════════════════════════════════════════════════════

@dataclass
class Track:
    url: str
    title: str
    duration: int           # Sekunden
    webpage_url: str = ""   # Original-URL (für Anzeige)
    requester: str = ""     # Wer hat hinzugefügt

    def duration_str(self) -> str:
        return format_duration(self.duration)


def format_duration(seconds: int) -> str:
    if not seconds:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


# ══════════════════════════════════════════════════════════
#  MUSIKZUSTAND (pro Guild)
# ══════════════════════════════════════════════════════════

class MusicState:
    def __init__(self):
        self.queue: list[Track] = []
        self.current: Optional[Track] = None
        self.lock = asyncio.Lock()
        self._skip_event = asyncio.Event()

    def clear(self):
        self.queue.clear()
        self.current = None

    @property
    def is_playing(self) -> bool:
        return self.current is not None

    def queue_info(self) -> str:
        """Kurze Queue-Übersicht."""
        total = sum(t.duration for t in self.queue)
        return f"{len(self.queue)} Song(s) · {format_duration(total)}"


# ══════════════════════════════════════════════════════════
#  BOT-SETUP
# ══════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True   # Nur was wirklich gebraucht wird
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Ein State-Objekt pro Guild
_states: dict[int, MusicState] = {}

def get_state(guild: discord.Guild) -> MusicState:
    if guild.id not in _states:
        _states[guild.id] = MusicState()
    return _states[guild.id]

# Mutex gegen parallele Reconnect-Versuche (pro Guild)
_connect_locks: dict[int, asyncio.Lock] = {}

def get_connect_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _connect_locks:
        _connect_locks[guild_id] = asyncio.Lock()
    return _connect_locks[guild_id]


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

    await asyncio.sleep(5)

    for guild in bot.guilds:
        bot.loop.create_task(_initial_join(guild))

    stay_in_channel.start()


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    """Reagiert NUR auf Bot-Disconnects."""
    if member.id != bot.user.id:
        return
    if before.channel is not None and after.channel is None:
        print(f"⚠️  Bot disconnected ({member.guild.name}) → Rejoin in {RECONNECT_DELAY_SECONDS}s …")
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        await safe_join(member.guild)


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Argument fehlt. `!bothelp` für Hilfe.")
    elif isinstance(error, commands.CommandNotFound):
        pass   # Stille Ignorierung unbekannter Befehle
    else:
        print(f"⚠️  Command-Fehler ({ctx.command}): {error}")
        await ctx.send(f"❌ Fehler: `{error}`")


# ══════════════════════════════════════════════════════════
#  VERBINDUNG
# ══════════════════════════════════════════════════════════

async def _initial_join(guild: discord.Guild):
    await asyncio.sleep(3)
    await safe_join(guild)


async def safe_join(guild: discord.Guild) -> bool:
    """
    Verbindet den Bot sicher mit TARGET_VOICE_CHANNEL_ID.
    Gibt True zurück wenn erfolgreich verbunden.
    """
    lock = get_connect_lock(guild.id)

    # Bereits am Verbinden → nicht doppelt
    if lock.locked():
        return False

    async with lock:
        channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            print("❌  VOICE_CHANNEL_ID ungültig oder kein Voice-Channel!")
            return False

        vc: Optional[discord.VoiceClient] = guild.voice_client

        # Schon korrekt verbunden
        if vc and vc.is_connected() and vc.channel.id == TARGET_VOICE_CHANNEL_ID:
            return True

        # Alte Verbindung sauber trennen
        if vc is not None:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(4)

        # Neu verbinden
        print(f"🔊  Verbinde mit «{channel.name}» …")
        try:
            vc = await asyncio.wait_for(
                channel.connect(reconnect=False, self_deaf=True, self_mute=True),
                timeout=JOIN_TIMEOUT_SECONDS,
            )
            print("✅  Verbunden!")

            # Unmute falls gerade etwas spielt
            state = get_state(guild)
            if state.is_playing:
                await _set_voice_state(guild, channel, mute=False)

            return True

        except asyncio.TimeoutError:
            print("❌  Verbindungs-Timeout.")
        except discord.errors.ConnectionClosed as exc:
            print(f"❌  Discord hat Verbindung abgelehnt (Code {exc.code}).")
            if exc.code == 4006:
                await asyncio.sleep(10)
        except Exception as exc:
            print(f"❌  Verbindungsfehler: {type(exc).__name__}: {exc}")

        return False


async def _set_voice_state(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    *,
    mute: bool,
):
    """Self-Mute/Unmute sauber setzen."""
    try:
        await guild.change_voice_state(
            channel=channel,
            self_mute=mute,
            self_deaf=mute,   # Taub nur wenn auch gemutet (idle)
        )
    except Exception as exc:
        print(f"⚠️  Voice-State-Fehler: {exc}")


# ══════════════════════════════════════════════════════════
#  WATCHDOG
# ══════════════════════════════════════════════════════════

@tasks.loop(minutes=WATCHDOG_INTERVAL_MINUTES)
async def stay_in_channel():
    for guild in bot.guilds:
        lock = get_connect_lock(guild.id)
        if lock.locked():
            continue

        vc: Optional[discord.VoiceClient] = guild.voice_client

        if vc is None or not vc.is_connected():
            print(f"🔄  Watchdog [{guild.name}]: Nicht verbunden → Rejoin …")
            await safe_join(guild)
        elif vc.channel.id != TARGET_VOICE_CHANNEL_ID:
            print(f"🔄  Watchdog [{guild.name}]: Falscher Channel → Wechsle …")
            await safe_join(guild)


@stay_in_channel.before_loop
async def _before_watchdog():
    await bot.wait_until_ready()
    await asyncio.sleep(20)


# ══════════════════════════════════════════════════════════
#  YT-DLP HELPER
# ══════════════════════════════════════════════════════════

async def fetch_track(query: str) -> Optional[Track]:
    """Lädt Infos zu einem einzelnen Track (kein Download)."""
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
    """Lädt alle Tracks einer Playlist (flach, schnell)."""
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
    """Spielt den nächsten Track aus der Queue."""
    state = get_state(guild)

    async with state.lock:
        if not state.queue:
            state.current = None
            channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
            vc: Optional[discord.VoiceClient] = guild.voice_client
            if channel and vc and vc.is_connected():
                await _set_voice_state(guild, channel, mute=True)
            print("📭  Queue leer.")
            return

        track = state.queue.pop(0)
        state.current = track

    vc: Optional[discord.VoiceClient] = guild.voice_client
    if not vc or not vc.is_connected():
        success = await safe_join(guild)
        if not success:
            state.current = None
            return
        await asyncio.sleep(2)
        vc = guild.voice_client

    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    await _set_voice_state(guild, channel, mute=False)
    await asyncio.sleep(0.5)

    # Falls URL abgelaufen ist, neu laden
    audio_url = track.url
    try:
        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=0.5)
    except Exception as exc:
        print(f"❌  Audio-Source Fehler: {exc}")
        state.current = None
        await play_next(guild)
        return

    def _after(error):
        if error:
            print(f"❌  Player-Fehler: {error}")
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
#  COMMANDS
# ══════════════════════════════════════════════════════════

@bot.command(name="play", aliases=["p"])
async def cmd_play(ctx: commands.Context, *, query: str):
    """Song oder YouTube-URL abspielen."""
    state = get_state(ctx.guild)

    if not ctx.guild.voice_client:
        await safe_join(ctx.guild)

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
    embed.add_field(name="🎵  Titel",   value=f"[{track.title}]({track.webpage_url})", inline=False)
    embed.add_field(name="⏱️  Länge",   value=track.duration_str(), inline=True)
    embed.add_field(name="📋  Position", value=f"#{pos}", inline=True)
    embed.add_field(name="👤  Von",      value=track.requester, inline=True)
    embed.set_footer(text=f"Queue: {state.queue_info()}")
    await msg.edit(content=None, embed=embed)

    if not state.is_playing:
        await play_next(ctx.guild)


@bot.command(name="playlist", aliases=["pl"])
async def cmd_playlist(ctx: commands.Context, *, url: str):
    """Komplette YouTube-Playlist in die Queue laden."""
    state = get_state(ctx.guild)

    if not url.startswith("http"):
        await ctx.send("❌  Bitte eine direkte Playlist-URL angeben.")
        return

    if not ctx.guild.voice_client:
        await safe_join(ctx.guild)

    msg = await ctx.send("⏳  Lade Playlist …")
    tracks = await fetch_playlist(url)

    if not tracks:
        await msg.edit(content="❌  Playlist nicht gefunden oder leer.")
        return

    for t in tracks:
        t.requester = ctx.author.display_name

    async with state.lock:
        state.queue.extend(tracks)

    embed = discord.Embed(
        title="📃  Playlist geladen",
        description=f"**{len(tracks)}** Songs hinzugefügt",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Queue: {state.queue_info()}")
    await msg.edit(content=None, embed=embed)

    if not state.is_playing:
        await play_next(ctx.guild)


@bot.command(name="skip", aliases=["s"])
async def cmd_skip(ctx: commands.Context):
    """Aktuellen Song überspringen."""
    vc = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()   # _after() ruft play_next() automatisch
        await ctx.send("⏭️  Übersprungen.")
    else:
        await ctx.send("❌  Es läuft gerade nichts.")


@bot.command(name="stop")
async def cmd_stop(ctx: commands.Context):
    """Musik stoppen und Queue leeren."""
    state = get_state(ctx.guild)
    vc = ctx.guild.voice_client

    async with state.lock:
        state.clear()

    if vc and vc.is_playing():
        vc.stop()

    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if channel:
        await _set_voice_state(ctx.guild, channel, mute=True)

    await ctx.send("⏹️  Gestoppt und Queue geleert.")


@bot.command(name="pause")
async def cmd_pause(ctx: commands.Context):
    """Pausiert die Wiedergabe."""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️  Pausiert.")
    else:
        await ctx.send("❌  Läuft nichts.")


@bot.command(name="resume", aliases=["r"])
async def cmd_resume(ctx: commands.Context):
    """Setzt pausierte Wiedergabe fort."""
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️  Fortgesetzt.")
    else:
        await ctx.send("❌  Nichts pausiert.")


@bot.command(name="volume", aliases=["vol", "v"])
async def cmd_volume(ctx: commands.Context, vol: int):
    """Lautstärke setzen (0–100)."""
    vc = ctx.guild.voice_client
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await ctx.send("❌  Läuft gerade nichts.")
        return
    if not 0 <= vol <= 100:
        await ctx.send("❌  Wert muss zwischen 0 und 100 liegen.")
        return

    # PCMVolumeTransformer erreichbar über vc.source
    if hasattr(vc.source, "volume"):
        vc.source.volume = vol / 100
        await ctx.send(f"🔊  Lautstärke: **{vol}%**")
    else:
        await ctx.send("⚠️  Lautstärke kann gerade nicht geändert werden.")


@bot.command(name="nowplaying", aliases=["np"])
async def cmd_nowplaying(ctx: commands.Context):
    """Zeigt den aktuell spielenden Song."""
    state = get_state(ctx.guild)
    if state.current:
        embed = discord.Embed(
            title="▶️  Spielt gerade",
            description=f"[{state.current.title}]({state.current.webpage_url})",
            color=discord.Color.green()
        )
        embed.add_field(name="⏱️  Länge", value=state.current.duration_str(), inline=True)
        embed.add_field(name="👤  Von",   value=state.current.requester or "—", inline=True)
        embed.set_footer(text=f"Queue: {state.queue_info()}")
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌  Läuft gerade nichts.")


@bot.command(name="queue", aliases=["q"])
async def cmd_queue(ctx: commands.Context):
    """Queue anzeigen."""
    state = get_state(ctx.guild)

    if not state.is_playing and not state.queue:
        await ctx.send("📭  Queue ist leer.")
        return

    embed = discord.Embed(title="📋  Queue", color=discord.Color.blue())

    if state.current:
        embed.add_field(
            name="▶️  Jetzt",
            value=f"[{state.current.title}]({state.current.webpage_url}) `[{state.current.duration_str()}]`",
            inline=False
        )

    if state.queue:
        lines = []
        for i, t in enumerate(state.queue[:15], 1):
            lines.append(f"`{i:>2}.` [{t.title}]({t.webpage_url}) `[{t.duration_str()}]`")
        if len(state.queue) > 15:
            lines.append(f"*… und {len(state.queue) - 15} weitere*")
        embed.add_field(name="📋  Nächste", value="\n".join(lines), inline=False)

    embed.set_footer(text=state.queue_info())
    await ctx.send(embed=embed)


@bot.command(name="clear")
async def cmd_clear(ctx: commands.Context):
    """Queue leeren (aktueller Song läuft weiter)."""
    state = get_state(ctx.guild)
    async with state.lock:
        state.queue.clear()
    await ctx.send("🗑️  Queue geleert.")


@bot.command(name="remove", aliases=["rm"])
async def cmd_remove(ctx: commands.Context, pos: int):
    """Song an Position X aus der Queue entfernen."""
    state = get_state(ctx.guild)
    async with state.lock:
        if not 1 <= pos <= len(state.queue):
            await ctx.send(f"❌  Ungültige Position. Queue hat {len(state.queue)} Einträge.")
            return
        removed = state.queue.pop(pos - 1)
    await ctx.send(f"🗑️  Entfernt: **{removed.title}**")


@bot.command(name="join")
async def cmd_join(ctx: commands.Context):
    """Bot in den Voice-Channel holen."""
    success = await safe_join(ctx.guild)
    channel = bot.get_channel(TARGET_VOICE_CHANNEL_ID)
    if success and channel:
        await ctx.send(f"✅  Verbunden mit **{channel.name}**!")
    else:
        await ctx.send("❌  Verbindung fehlgeschlagen.")


@bot.command(name="bothelp", aliases=["h", "hilfe"])
async def cmd_bothelp(ctx: commands.Context):
    """Hilfe anzeigen."""
    embed = discord.Embed(
        title="🤖  Bot Commands",
        description="Prefix: `!`",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="🎵  Wiedergabe",
        value=(
            "`!play <Song/URL>`   — Song suchen & spielen\n"
            "`!playlist <URL>`    — YouTube-Playlist laden\n"
            "`!skip` / `!s`       — Überspringen\n"
            "`!stop`              — Stoppen & Queue leeren\n"
            "`!pause`             — Pausieren\n"
            "`!resume` / `!r`     — Fortsetzen\n"
            "`!volume <0-100>`    — Lautstärke\n"
            "`!nowplaying` / `!np`— Aktueller Song"
        ),
        inline=False
    )
    embed.add_field(
        name="📋  Queue",
        value=(
            "`!queue` / `!q`      — Queue anzeigen\n"
            "`!clear`             — Queue leeren\n"
            "`!remove <#>`        — Song entfernen"
        ),
        inline=False
    )
    embed.add_field(
        name="🔊  Voice",
        value="`!join`              — Bot rufen",
        inline=False
    )
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
