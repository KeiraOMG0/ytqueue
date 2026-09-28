"""Autoplay: refill an empty queue from YouTube's auto-generated Mix/Radio
playlist for the last played track, mimicking the site's own "recommended
up next" behavior."""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from .models import Track
from .resolver import Resolver

log = logging.getLogger("ytqueue.autoplay")

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")

DEFAULT_BATCH_SIZE = 5


def extract_video_id(url: str) -> Optional[str]:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


async def refill_from_mix(
    resolver: Resolver,
    seed_url: str,
    recent_urls: Iterable[str] = (),
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[Track]:
    """Resolve the YouTube Mix ("RD<id>") playlist seeded by the given video
    URL and return up to `batch_size` new Tracks, skipping any whose video
    ID appears in `recent_urls` (e.g. recent play history) to avoid
    immediate repeats. Returns an empty list if resolution fails or nothing
    new is found."""
    video_id = extract_video_id(seed_url)
    if not video_id:
        log.warning("Autoplay: could not extract a video ID from %s", seed_url)
        return []

    mix_url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    opts = {
        **resolver.config.base_ydl_opts(),
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "playlist_items": f"1-{batch_size + 1}",
    }

    info = await resolver.extract_info(opts, mix_url)
    if not info or "entries" not in info:
        log.warning("Autoplay: no mix playlist entries for seed %s", seed_url)
        return []

    recent_ids = {extract_video_id(u) for u in recent_urls}
    recent_ids.add(video_id)

    tracks: list[Track] = []
    for entry in info["entries"] or []:
        if not entry or not entry.get("id"):
            continue
        entry_id = entry["id"]
        if entry_id in recent_ids:
            continue
        watch_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry_id}"
        tracks.append(Track(url=watch_url, title=entry.get("title") or "Unknown"))
        if len(tracks) >= batch_size:
            break

    if tracks:
        log.info("Autoplay: found %d related track(s) from mix for %s", len(tracks), seed_url)
    else:
        log.warning("Autoplay: mix playlist for %s had no new tracks", seed_url)
    return tracks
