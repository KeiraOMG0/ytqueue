"""Orchestrates PlayQueue + Resolver + FFmpegAudioSource into a cohesive
player, without assuming any particular chat platform or audio sink.

The caller supplies two callbacks:
  - `play_source(source: FFmpegAudioSource) -> None`
      Hand the audio source to whatever actually plays it (a discord.py
      VoiceClient, a local speaker output, etc). Must arrange to call
      `player.on_track_finished()` when playback of that source ends.
  - `on_track_started(track: Track) -> Awaitable[None] | None` (optional)
      Notified right before playback starts, e.g. to send a "Now playing"
      message.

See ytqueue.discord.DiscordPlayer for a ready-made discord.py binding that
wires these up for you.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional, Union

from .audio import FFmpegAudioSource, FFmpegOptions
from .autoplay import refill_from_mix
from .models import Track
from .queue import PlayQueue
from .resolver import Resolver

log = logging.getLogger("ytqueue.player")

PlaySourceFn = Callable[[FFmpegAudioSource], None]
TrackHook = Callable[[Track], Union[Awaitable[None], None]]


async def _maybe_await(value: Union[Awaitable[None], None]) -> None:
    if value is not None:
        await value


class MusicPlayer:
    def __init__(
        self,
        resolver: Optional[Resolver] = None,
        queue: Optional[PlayQueue] = None,
        ffmpeg_options: Optional[FFmpegOptions] = None,
        autoplay_batch_size: int = 5,
    ) -> None:
        self.resolver = resolver or Resolver()
        self.queue = queue or PlayQueue()
        self.ffmpeg_options = ffmpeg_options or FFmpegOptions()
        self.autoplay_batch_size = autoplay_batch_size

        self.on_track_started: Optional[TrackHook] = None
        self.on_track_skipped_load_failure: Optional[TrackHook] = None

        self._lock = asyncio.Lock()
        self._play_source_fn: Optional[PlaySourceFn] = None
        self._current_start_time: Optional[float] = None

    def bind_sink(self, play_source_fn: PlaySourceFn) -> None:
        """Register the callback that actually plays an FFmpegAudioSource
        (e.g. a discord.py VoiceClient.play wrapper)."""
        self._play_source_fn = play_source_fn

    async def enqueue_url_or_query(self, text: str) -> list[Track]:
        """Add a URL (single video or playlist) or a search query's first
        result set to the queue. For ambiguous text queries where you want
        to let the user pick, use resolver.search() directly instead."""
        if self.resolver.is_url(text):
            tracks = await self.resolver.expand_url(text)
        else:
            results = await self.resolver.search(text, limit=1)
            tracks = results[:1]

        for track in tracks:
            self.queue.enqueue(track)
        return tracks

    async def play_next(self) -> Optional[Track]:
        """Advance to the next track, resolving and starting playback.
        Returns the Track that started playing, or None if nothing did
        (empty queue, everything failed to load, or something is already
        playing)."""
        if self._play_source_fn is None:
            raise RuntimeError("MusicPlayer.bind_sink() must be called before play_next()")

        async with self._lock:
            if not self.queue.tracks and self.queue.autoplay_enabled and self.queue.previous:
                await self._autoplay_refill()

            while self.queue.tracks:
                track = self.queue.pop_next()
                assert track is not None

                stream = await self.resolver.resolve_stream(track)
                if not stream:
                    log.warning("Could not resolve stream for: %s", track.title)
                    await _maybe_await(
                        self.on_track_skipped_load_failure(track) if self.on_track_skipped_load_failure else None
                    )
                    continue

                try:
                    source = FFmpegAudioSource(stream, title=track.title, options=self.ffmpeg_options)
                except Exception:
                    log.exception("Failed to start ffmpeg for %s", track.title)
                    await _maybe_await(
                        self.on_track_skipped_load_failure(track) if self.on_track_skipped_load_failure else None
                    )
                    continue

                track.duration = stream.duration
                self.queue.set_current(track)
                self.queue.touch()
                self._current_start_time = time.monotonic()

                log.info("Now playing: %s", track.title)
                await _maybe_await(self.on_track_started(track) if self.on_track_started else None)

                self._play_source_fn(source)
                return track

            return None

    def on_track_finished(self, error: Optional[BaseException] = None) -> float:
        """Call this from your sink's completion callback (e.g. discord.py's
        VoiceClient.play(after=...)) when the current track's audio ends.
        Returns how many seconds it played for, which is useful for
        detecting a stream that failed near-instantly with no exception
        (a common symptom of a 403'd/expired stream URL)."""
        played_for = time.monotonic() - self._current_start_time if self._current_start_time else 0.0
        if error:
            log.error("Playback error after %s (played %.1fs): %s", self._track_label(), played_for, error)
        elif played_for < 2:
            log.warning(
                "Playback of %s ended almost immediately (%.2fs) with no error reported "
                "-- check ytqueue.audio logs for the real cause "
                "(bad stream URL, expired signature, or ffmpeg failing to decode).",
                self._track_label(),
                played_for,
            )
        self.queue.finish_current()
        self._current_start_time = None
        return played_for

    def _track_label(self) -> str:
        return self.queue.current.title if self.queue.current else "<unknown>"

    async def _autoplay_refill(self) -> int:
        assert self.queue.previous is not None
        recent_urls = [t.url for t in self.queue.history]
        tracks = await refill_from_mix(
            self.resolver,
            self.queue.previous.url,
            recent_urls=recent_urls,
            batch_size=self.autoplay_batch_size,
        )
        for track in tracks:
            self.queue.enqueue(track)
        return len(tracks)
