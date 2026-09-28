"""Optional: a local HTTP server implementing the musicbot-cookie-sync
browser extension's protocol (https://github.com/KeiraOMG0/musicbot-cookie-sync),
for auto-refreshing cookies.txt when yt-dlp hits a cookie/auth error.

Wire CookieSyncServer.request_refresh_and_wait as a Resolver's
on_cookie_error callback:

    server = CookieSyncServer(cookies_file="cookies.txt")
    server.start()
    resolver = Resolver(ResolverConfig(
        cookies_file="cookies.txt",
        on_cookie_error=server.request_refresh_and_wait,
    ))
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("ytqueue.cookie_sync")

DEFAULT_PORT = 8999
DEFAULT_REFRESH_TIMEOUT = 60.0


class CookieSyncServer:
    def __init__(
        self,
        cookies_file: str,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
    ) -> None:
        self.cookies_file = cookies_file
        self.host = host
        self.port = port

        self._needs_refresh = False
        self._lock = threading.Lock()
        self._refreshed_event = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="cookie-sync-server")
        thread.start()
        log.info("Cookie sync server listening on http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    async def request_refresh_and_wait(self, timeout: float = DEFAULT_REFRESH_TIMEOUT) -> bool:
        """Ask the browser extension for fresh cookies and wait for them to
        land. Returns True if fresh cookies were received before the
        timeout. Matches the Resolver `on_cookie_error` callback signature."""
        with self._lock:
            self._needs_refresh = True
        self._refreshed_event.clear()
        log.info("Requested cookie refresh from browser extension, waiting up to %ds", timeout)

        loop = asyncio.get_running_loop()
        got_it = await loop.run_in_executor(None, self._refreshed_event.wait, timeout)

        if not got_it:
            with self._lock:
                self._needs_refresh = False
            log.warning("No cookie refresh received within %ds", timeout)
        return got_it

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                log.debug("%s - %s", self.address_string(), fmt % args)

            def _send(self, code: int, body: bytes = b"", content_type: str = "text/plain") -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self) -> None:
                if self.path == "/needs-refresh":
                    with outer._lock:
                        needs = outer._needs_refresh
                    self._send(200, json.dumps({"needsRefresh": needs}).encode(), "application/json")
                    return
                self._send(404)

            def do_POST(self) -> None:
                if self.path != "/cookies":
                    self._send(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                if not body.strip():
                    self._send(400, b"empty cookies payload")
                    return
                try:
                    with open(outer.cookies_file, "w", encoding="utf-8") as f:
                        f.write(body)
                except Exception:
                    log.exception("Failed to write cookies file %s", outer.cookies_file)
                    self._send(500, b"failed to write cookies file")
                    return

                with outer._lock:
                    outer._needs_refresh = False
                outer._refreshed_event.set()
                log.info("Received fresh cookies from browser extension (%d bytes)", len(body))
                self._send(200, b"ok")

        return Handler
