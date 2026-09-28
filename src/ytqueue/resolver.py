"""yt-dlp-backed resolution of a URL/search query into a playable stream.

Encodes the lessons learned fighting YouTube's client-gating rollout
(see yt-dlp issue #12482): plain `bestaudio/best` regularly resolves to
either an HLS (m3u8) stream or a stream URL bound to a player client whose
fingerprint ffmpeg can't replicate, both of which manifest as a track that
"plays" for a fraction of a second then silently ends. The defaults here
avoid both failure modes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import yt_dlp

from .models import ResolvedStream, Track

log = logging.getLogger("ytqueue.resolver")

_COOKIE_ERROR_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "cookies",
    "age",
)


def _looks_like_cookie_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _COOKIE_ERROR_MARKERS)


@dataclass
class ResolverConfig:
    """Tunables for stream resolution.

    Attributes:
        cookies_file: Path to a Netscape-format cookies.txt, or None.
        node_path: Path to a Node.js binary, needed by yt-dlp's EJS
            challenge solver for some extraction paths. None disables it.
        player_clients: yt-dlp player clients to try, in order. tv_simply
            has -- as of this writing -- reliably survived an actual ffmpeg
            fetch where android_vr/ios/mweb/web/web_safari have not; kept
            configurable since YouTube's requirements shift over time.
        format_selector: yt-dlp format selector string. Defaults to
            preferring a plain https progressive stream over HLS, since
            FFmpegAudioSource is far more reliable against a single https
            URL than against an m3u8 manifest.
        default_search: yt-dlp default_search prefix for bare queries.
        allow_playlists: whether a bare playlist URL should expand.
        on_cookie_error: optional async callback invoked when extraction
            fails with what looks like a cookie/auth error (age-gate,
            "sign in to confirm", etc). Should attempt to refresh the
            cookies file and return True if it did so (triggering one
            retry), False otherwise. See ytqueue.cookie_sync for a ready
            implementation using the musicbot-cookie-sync browser
            extension's protocol.
    """

    cookies_file: Optional[str] = None
    node_path: Optional[str] = None
    player_clients: list[str] = field(
        default_factory=lambda: ["tv_simply", "tv", "web", "web_safari"]
    )
    format_selector: str = "bestaudio[protocol^=https]/best[protocol^=https]/bestaudio/best"
    default_search: str = "ytsearch10"
    allow_playlists: bool = True
    on_cookie_error: Optional[Callable[[], Awaitable[bool]]] = None

    def base_ydl_opts(self) -> dict:
        opts: dict = {
            "format": self.format_selector,
            "quiet": True,
            "ignoreerrors": True,
            "default_search": self.default_search,
            "noplaylist": not self.allow_playlists,
            "extractor_args": {"youtube": {"player_client": self.player_clients}},
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if self.node_path:
            opts["js_runtimes"] = {"node": {"path": self.node_path}}
            opts["remote_components"] = ["ejs:github"]
        return opts


class Resolver:
    """Resolves queries/URLs to metadata and playable stream URLs via yt-dlp."""

    def __init__(self, config: Optional[ResolverConfig] = None) -> None:
        self.config = config or ResolverConfig()

    @staticmethod
    def is_url(text: str) -> bool:
        return text.startswith("http://") or text.startswith("https://")

    async def extract_info(self, opts: dict, url: str, _allow_cookie_retry: bool = True) -> Optional[dict]:
        """Run yt-dlp extraction in a thread. Retries once via
        config.on_cookie_error if the failure looks cookie/auth-related."""
        loop = asyncio.get_running_loop()

        def _run() -> Optional[dict]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            return await loop.run_in_executor(None, _run)
        except yt_dlp.utils.DownloadError as e:
            message = str(e)
            log.error("yt-dlp DownloadError for %s: %s", url, e)
            if _allow_cookie_retry and self.config.on_cookie_error and _looks_like_cookie_error(message):
                log.info("Error looks cookie/auth-related, attempting a refresh...")
                refreshed = await self.config.on_cookie_error()
                if refreshed:
                    log.info("Cookies refreshed, retrying extraction for %s", url)
                    return await self.extract_info(opts, url, _allow_cookie_retry=False)
            return None
        except Exception:
            log.exception("yt-dlp unexpected error for %s", url)
            return None

    async def search(self, query: str, limit: int = 10) -> list[Track]:
        """Text search -> a list of candidate Tracks (not yet resolved to a
        stream URL). Use resolve() on the chosen one before playback."""
        opts = {
            **self.config.base_ydl_opts(),
            "default_search": f"ytsearch{limit}",
            "noplaylist": True,
            "extract_flat": "in_playlist",
        }
        info = await self.extract_info(opts, query)
        if not info:
            return []

        results: list[Track] = []
        entries = info.get("entries") if "entries" in info else [info]
        for e in entries or []:
            if not e or not e.get("id"):
                continue
            title = e.get("title") or e.get("fulltitle")
            if not title:
                continue
            watch_url = e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}"
            results.append(Track(url=watch_url, title=title, duration=e.get("duration")))
            if len(results) >= limit:
                break
        return results

    async def expand_url(self, url: str) -> list[Track]:
        """Resolve a URL that may be a single video or a playlist into one
        or more queueable Tracks (metadata only, not yet a stream)."""
        opts = {**self.config.base_ydl_opts(), "extract_flat": "in_playlist"}
        info = await self.extract_info(opts, url)
        if not info:
            return []

        if "entries" in info:
            tracks = []
            for e in info["entries"] or []:
                if not e or not e.get("id"):
                    continue
                watch_url = e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
                tracks.append(Track(url=watch_url, title=e.get("title") or "Unknown"))
            return tracks

        watch_url = info.get("webpage_url") or url
        return [Track(url=watch_url, title=info.get("title") or "Unknown", duration=info.get("duration"))]

    async def resolve_stream(self, track: Track) -> Optional[ResolvedStream]:
        """Resolve a Track to an actual playable stream URL + headers."""
        opts = {**self.config.base_ydl_opts(), "noplaylist": True}
        info = await self.extract_info(opts, track.url)
        if not info:
            log.warning("No info returned for: %s", track.title)
            return None

        stream_url = info.get("url")
        if not stream_url:
            log.warning("No stream URL for: %s", track.title)
            return None

        log.debug(
            "Extracted stream for %s: format=%s acodec=%s ext=%s protocol=%s",
            track.title,
            info.get("format"),
            info.get("acodec"),
            info.get("ext"),
            info.get("protocol"),
        )

        return ResolvedStream(
            stream_url=stream_url,
            http_headers=info.get("http_headers") or {},
            duration=info.get("duration"),
            format_id=info.get("format_id"),
            protocol=info.get("protocol"),
            acodec=info.get("acodec"),
        )
