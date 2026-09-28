"""Optional discord.py adapter for ytqueue. Install with the `discord` extra:

    pip install ytqueue[discord]

Usage:

    player = DiscordPlayer(resolver=Resolver(ResolverConfig(...)))

    @bot.command()
    async def play(ctx, *, query: str):
        vc = ctx.voice_client or await ctx.author.voice.channel.connect()
        await player.enqueue_url_or_query(query)
        if not vc.is_playing():
            await player.play_next(ctx, vc)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import discord
    from discord.ext import commands

from ..audio import FFmpegAudioSource
from ..player import MusicPlayer
from ..models import Track

log = logging.getLogger("ytqueue.discord")


def _require_discord():
    try:
        import discord  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "ytqueue.discord requires discord.py. Install with: pip install ytqueue[discord]"
        ) from e


class DiscordAudioSource:
    """Adapts an ytqueue FFmpegAudioSource to discord.py's AudioSource ABC."""

    def __init__(self, source: FFmpegAudioSource) -> None:
        _require_discord()
        self._source = source

    def read(self) -> bytes:
        return self._source.read()

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._source.cleanup()


class DiscordPlayer:
    """Binds an ytqueue MusicPlayer to a discord.py VoiceClient.

    One DiscordPlayer per guild/voice session -- create a new one (or reset
    its `.core.queue`) per guild if you're running a multi-guild bot.
    """

    def __init__(self, core: Optional[MusicPlayer] = None, volume: float = 1.0) -> None:
        _require_discord()
        self.core = core or MusicPlayer()
        self.volume = volume
        self._ctx = None
        self._vc = None

        self.core.bind_sink(self._play_source)

    def _play_source(self, source: FFmpegAudioSource) -> None:
        import discord

        assert self._vc is not None
        discord_source = discord.PCMVolumeTransformer(
            DiscordAudioSource(source), volume=self.volume
        )

        def _after(error: Optional[Exception]) -> None:
            self.core.on_track_finished(error)
            if self._vc and self._vc.is_connected() and self._ctx is not None:
                import asyncio

                asyncio.run_coroutine_threadsafe(
                    self.play_next(self._ctx, self._vc), self._ctx.bot.loop
                )

        self._vc.play(discord_source, after=_after)

    async def enqueue_url_or_query(self, text: str) -> list[Track]:
        return await self.core.enqueue_url_or_query(text)

    async def play_next(self, ctx: "commands.Context", vc: "discord.VoiceClient") -> Optional[Track]:
        """Advance to the next queued track and start playing it on `vc`.
        Safe to call when something is already playing (it's a no-op)."""
        if vc.is_playing() or vc.is_paused():
            return None
        self._ctx = ctx
        self._vc = vc
        return await self.core.play_next()
