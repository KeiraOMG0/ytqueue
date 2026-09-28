"""Manages the bgutil PO Token provider's companion Node server.

yt-dlp needs a proof-of-origin token to get playable stream URLs out of
YouTube for many videos (see yt-dlp issue #12482 / the SABR rollout). The
bgutil-ytdlp-pot-provider plugin (install the `pot` extra) talks to a small
local Node HTTP server to generate those tokens; this class starts/stops
that server for you.

Setup (once): clone the provider's server and build it --

    git clone --single-branch --branch 2.0.0 \\
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git pot_server
    cd pot_server/server && npm ci && npx tsc

Then point PotServer(script_path=".../pot_server/server/build/main.js").
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger("ytqueue.pot_server")


class PotServer:
    def __init__(
        self,
        script_path: str,
        node_path: Optional[str] = None,
        port: int = 4416,
    ) -> None:
        self.script_path = script_path
        self.node_path = node_path or shutil.which("node")
        self.port = port
        self._process: Optional[subprocess.Popen] = None

    def is_up(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/ping", timeout=1):
                return True
        except (urllib.error.URLError, OSError):
            return False

    async def start(self) -> None:
        """Start the PO token server if it isn't already running. Safe to
        call even if something else (or a previous run) already has it up."""
        if self.is_up():
            log.info("PO token server already running on port %d", self.port)
            return

        if not self.node_path:
            log.warning(
                "Could not start PO token server: no Node.js binary found. "
                "Age/sign-in-gated videos may fail to resolve."
            )
            return

        try:
            self._process = subprocess.Popen(
                [self.node_path, self.script_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.warning(
                "Could not start PO token server: script not found at %s. "
                "See ytqueue.pot_server module docstring for setup steps.",
                self.script_path,
            )
            return
        except Exception:
            log.exception("Failed to start PO token server")
            return

        for _ in range(20):
            await asyncio.sleep(0.5)
            if self.is_up():
                log.info("PO token server started on port %d (pid %d)", self.port, self._process.pid)
                return
            if self._process.poll() is not None:
                log.warning("PO token server exited immediately (code %s)", self._process.returncode)
                return

        log.warning("PO token server did not respond to /ping within 10s after starting")

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            log.info("Stopping PO token server (pid %d)", self._process.pid)
            self._process.terminate()
        self._process = None
