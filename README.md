# ytqueue

A Discord-agnostic YouTube music player core: resolve, queue, autoplay, and
stream audio via yt-dlp + ffmpeg.

Born out of fixing the same "plays for half a second then silently stops"
bug across two different Discord bots. Both bugs traced back to the same
root cause — yt-dlp resolving to a stream URL bound to a player client
whose fingerprint ffmpeg can't replicate, or to an HLS manifest that
`FFmpegPCMAudio` handles poorly — plus ffmpeg's own stderr being silently
discarded, which is what made the failure look like nothing happened at
all. This package is that fix, extracted so it doesn't have to be
re-diagnosed a third time.

## Install

```
pip install ytqueue
```

Optional extras:

```
pip install ytqueue[discord]   # discord.py adapter (regular discord.py bots)
pip install ytqueue[pot]       # PO token provider (age/sign-in-gated videos)
```

**If your bot runs on a discord.py *fork*** (e.g. a self-bot fork such as
`discord.py-self`), don't install the `discord` extra — it depends on
`discord.py` by name and pip's resolver will silently swap your fork out
for the real package. Just `pip install ytqueue` (core only); the core
modules don't import `discord` at all, and `ytqueue.discord`'s thin
wrapper works against any package that provides a compatible `discord`
module without needing it declared as a hard dependency.

## Quickstart (framework-agnostic)

```python
import asyncio
from ytqueue import Resolver, ResolverConfig, PlayQueue, MusicPlayer

async def main():
    resolver = Resolver(ResolverConfig(cookies_file="cookies.txt"))
    player = MusicPlayer(resolver=resolver)

    def play_source(source):
        while True:
            frame = source.read()
            if not frame:
                break
            # send `frame` (raw 16-bit 48kHz stereo PCM) to your audio sink
        source.cleanup()

    player.bind_sink(play_source)
    await player.enqueue_url_or_query("never gonna give you up")
    await player.play_next()

asyncio.run(main())
```

## Quickstart (discord.py)

```python
from discord.ext import commands
from ytqueue import Resolver, ResolverConfig
from ytqueue.discord import DiscordPlayer

resolver = Resolver(ResolverConfig(cookies_file="cookies.txt"))
player = DiscordPlayer(core=MusicPlayer(resolver=resolver))

@bot.command()
async def play(ctx, *, query: str):
    vc = ctx.voice_client or await ctx.author.voice.channel.connect()
    await player.enqueue_url_or_query(query)
    await player.play_next(ctx, vc)
```

## Why the defaults are what they are

- **Format selector** prefers a plain `https` progressive stream over HLS
  (`m3u8`). `FFmpegPCMAudio`/`FFmpegAudioSource` are far more reliable
  against a single https URL than an HLS manifest.
- **Player client chain** (`tv_simply, tv, web, web_safari` by default) —
  YouTube's SABR/PO-token rollout (see [yt-dlp #12482]) means several
  clients (`android_vr`, `ios`, `mweb`, `web_embedded`, plain
  `web`/`web_safari`) either return no downloadable URL or hand back one
  that 403s when ffmpeg — not yt-dlp's own extraction session — requests
  it. `tv_simply` has reliably survived an actual ffmpeg fetch in testing;
  the rest are fallbacks. This will likely need updating as YouTube's
  requirements keep shifting — `ResolverConfig.player_clients` is
  deliberately a plain list you can override.
- **ffmpeg stderr is captured and logged**, not discarded. A 403'd or
  expired stream URL makes ffmpeg exit almost immediately with no
  Python-visible exception — without its stderr, that looks exactly like
  the track just ending normally. `MusicPlayer.on_track_finished()` also
  flags anything that played for under 2 seconds with no error, pointing
  you at the logs.

[yt-dlp #12482]: https://github.com/yt-dlp/yt-dlp/issues/12482

## Optional: PO Token support

Some videos require a Proof-of-Origin token to get a playable URL at all,
regardless of player client. `ytqueue.pot_server.PotServer` manages the
[bgutil-ytdlp-pot-provider] companion Node server for you:

```
pip install ytqueue[pot]
git clone --single-branch --branch 2.0.0 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git pot_server
cd pot_server/server && npm ci && npx tsc
```

```python
from ytqueue.pot_server import PotServer

pot = PotServer(script_path="pot_server/server/build/main.js")
await pot.start()  # on your bot's startup
...
pot.stop()  # on shutdown
```

[bgutil-ytdlp-pot-provider]: https://github.com/Brainicism/bgutil-ytdlp-pot-provider

## Optional: cookie auto-refresh

`ytqueue.cookie_sync.CookieSyncServer` implements the server side of the
[musicbot-cookie-sync] browser extension protocol, so a `cookies.txt` that
rotates/expires gets refreshed automatically instead of needing a manual
re-export:

```python
from ytqueue.cookie_sync import CookieSyncServer

cookie_server = CookieSyncServer(cookies_file="cookies.txt")
cookie_server.start()

resolver = Resolver(ResolverConfig(
    cookies_file="cookies.txt",
    on_cookie_error=cookie_server.request_refresh_and_wait,
))
```

[musicbot-cookie-sync]: https://github.com/KeiraOMG0/musicbot-cookie-sync

## Autoplay

`PlayQueue.autoplay_enabled = True` makes `MusicPlayer.play_next()` refill
an empty queue from YouTube's auto-generated Mix/Radio playlist (`RD<id>`)
for the last played track — the same mechanism YouTube's own "up next"
autoplay uses.

## Package layout

- `ytqueue.models` — `Track`, `ResolvedStream` (plain dataclasses)
- `ytqueue.resolver` — yt-dlp extraction, format/client selection, cookie-retry hook
- `ytqueue.queue` — `PlayQueue`: pending tracks, history, loop/autoplay flags
- `ytqueue.autoplay` — Mix/Radio playlist refill
- `ytqueue.audio` — `FFmpegAudioSource`: raw ffmpeg subprocess → PCM frames
- `ytqueue.player` — `MusicPlayer`: orchestrates the above behind two callbacks
- `ytqueue.pot_server` — optional PO token companion server management
- `ytqueue.cookie_sync` — optional cookie auto-refresh server
- `ytqueue.discord` — optional discord.py adapter (`discord` extra)

## License

MIT — see [LICENSE](LICENSE).
