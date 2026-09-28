"""ytqueue: a Discord-agnostic YouTube music player core.

Resolve URLs/searches to playable streams via yt-dlp, manage a queue with
loop/autoplay, and build ffmpeg-backed audio sources -- independent of any
particular chat platform. See ytqueue.discord for an optional discord.py
adapter (installed via the `discord` extra).
"""

from .models import Track
from .queue import PlayQueue
from .resolver import Resolver, ResolverConfig
from .audio import FFmpegAudioSource, FFmpegOptions
from .player import MusicPlayer

__all__ = [
    "Track",
    "PlayQueue",
    "Resolver",
    "ResolverConfig",
    "FFmpegAudioSource",
    "FFmpegOptions",
    "MusicPlayer",
]

__version__ = "0.1.0"
