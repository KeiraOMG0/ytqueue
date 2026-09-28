"""A minimal, discord.py-independent ffmpeg-backed audio source.

Spawns ffmpeg to decode a (possibly network) stream URL to raw PCM, with
the resolved stream's HTTP headers passed through via `-headers` (needed
for many YouTube stream URLs to actually work) and ffmpeg's stderr captured
and logged line-by-line instead of silently discarded -- silently discarding
it is what makes a stream that 403s look like a track that "just ends"
instead of an error.

If you're using discord.py, you likely want ytqueue.discord.DiscordAudioSource
instead, which wraps this for VoiceClient.play(). This class is for anyone
else: a CLI player, a different chat platform, tests, etc.
"""

from __future__ import annotations

import io
import logging
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

from .models import ResolvedStream

log = logging.getLogger("ytqueue.audio")


@dataclass
class FFmpegOptions:
    executable: str = "ffmpeg"
    before_options: str = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    options: str = "-vn"
    # PCM output format ffmpeg is asked to produce.
    sample_rate: int = 48000
    channels: int = 2
    frame_size: int = 3840  # 20ms of 48kHz 16-bit stereo, matches discord.py's Opus frame size


class _StderrLogger(io.RawIOBase):
    """Forwards a subprocess's stderr to a logger, line by line."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._buffer = b""

    def write(self, data: bytes) -> int:
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            text = line.decode(errors="replace").strip()
            if text:
                log.warning("[%s] %s", self._label, text)
        return len(data)

    def writable(self) -> bool:
        return True


class FFmpegAudioSource:
    """Reads raw PCM audio from ffmpeg decoding a resolved stream.

    Usage:
        source = FFmpegAudioSource(resolved_stream, title="My Track")
        while True:
            frame = source.read()
            if not frame:
                break
            # send `frame` to your audio sink
        source.cleanup()
    """

    def __init__(
        self,
        stream: ResolvedStream,
        title: str = "",
        options: Optional[FFmpegOptions] = None,
    ) -> None:
        self.options = options or FFmpegOptions()
        self._title = title or stream.stream_url
        self._process = self._spawn(stream)

    def _spawn(self, stream: ResolvedStream) -> subprocess.Popen:
        opts = self.options
        before_options = opts.before_options
        if stream.http_headers:
            header_str = "".join(f"{k}: {v}\r\n" for k, v in stream.http_headers.items())
            before_options = f'{before_options} -headers "{header_str}"'.strip()

        args = [opts.executable]
        if before_options:
            args.extend(shlex.split(before_options))
        args += ["-i", stream.stream_url]
        args += [
            "-f", "s16le",
            "-ar", str(opts.sample_rate),
            "-ac", str(opts.channels),
            "-loglevel", "warning",
        ]
        if opts.options:
            args.extend(shlex.split(opts.options))
        args.append("pipe:1")

        log.debug("Spawning ffmpeg for %s: %s", self._title, " ".join(args))
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"{opts.executable} was not found on PATH") from e

        # Drain stderr on a background thread so it can't block the pipe
        # and so failures (403s, decode errors, etc.) are visible.
        import threading

        stderr_logger = _StderrLogger(self._title)

        def _drain() -> None:
            assert process.stderr is not None
            while True:
                chunk = process.stderr.read(io.DEFAULT_BUFFER_SIZE)
                if not chunk:
                    return
                stderr_logger.write(chunk)

        threading.Thread(target=_drain, daemon=True, name=f"ffmpeg-stderr:{self._title}").start()

        return process

    def read(self) -> bytes:
        """Read one frame of raw PCM audio. Returns b"" at end of stream."""
        assert self._process.stdout is not None
        data = self._process.stdout.read(self.options.frame_size)
        if len(data) != self.options.frame_size:
            return b""
        return data

    def cleanup(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
