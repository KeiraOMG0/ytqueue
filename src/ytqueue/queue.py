"""Plain queue/state management -- no I/O, no yt-dlp, no audio."""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

from .models import Track


class PlayQueue:
    """Holds the pending queue, playback history, and loop/autoplay flags.
    Deliberately has no knowledge of yt-dlp or any audio backend -- pair it
    with a Resolver and an audio sink (see MusicPlayer) to actually play
    anything."""

    def __init__(self, history_size: int = 10) -> None:
        self.tracks: deque[Track] = deque()
        self.history: deque[Track] = deque(maxlen=history_size)
        self.current: Optional[Track] = None
        self.current_started_at: Optional[float] = None
        self.previous: Optional[Track] = None
        self.loop_enabled: bool = False
        self.autoplay_enabled: bool = False
        self.last_activity: float = time.time()

    def enqueue(self, track: Track) -> None:
        self.tracks.append(track)
        self.touch()

    def enqueue_front(self, track: Track) -> None:
        self.tracks.appendleft(track)
        self.touch()

    def pop_next(self) -> Optional[Track]:
        return self.tracks.popleft() if self.tracks else None

    def clear(self) -> None:
        self.tracks.clear()

    def set_current(self, track: Track) -> None:
        self.current = track
        self.current_started_at = time.time()

    def clear_current(self) -> None:
        self.current = None
        self.current_started_at = None

    def finish_current(self) -> None:
        """Call when the current track ends: pushes it to history/previous,
        re-queues it if loop is on, and clears the "now playing" slot."""
        if self.current:
            self.previous = self.current
            self.history.append(self.current)
            if self.loop_enabled:
                self.tracks.append(self.current)
        self.clear_current()

    def elapsed(self) -> Optional[float]:
        if self.current_started_at is None:
            return None
        return time.time() - self.current_started_at

    def touch(self) -> None:
        self.last_activity = time.time()

    def idle_for(self) -> float:
        return time.time() - self.last_activity

    def __len__(self) -> int:
        return len(self.tracks)
