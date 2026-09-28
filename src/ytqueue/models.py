"""Plain data models shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Track:
    """A single queueable item: just enough to re-resolve it later."""

    url: str
    title: str
    duration: Optional[int] = None
    requester_id: Optional[int] = None


@dataclass
class ResolvedStream:
    """The result of resolving a Track to a playable stream."""

    stream_url: str
    http_headers: dict = field(default_factory=dict)
    duration: Optional[int] = None
    format_id: Optional[str] = None
    protocol: Optional[str] = None
    acodec: Optional[str] = None
