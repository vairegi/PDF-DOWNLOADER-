"""Telethon userbot bridge: sends the gallery URL to @nHentaiBot and waits
for its reply containing a telegra.ph link.

Detection rule (as specified): poll the dialog for the newest message from
@nHentaiBot whose text contains a telegra.ph URL, for up to `timeout`
seconds. Baseline = latest message id BEFORE we send, so we never match an
old article from a previous request.
"""
from __future__ import annotations
import asyncio, logging, re

from telethon import TelegramClient
from telethon.sessions import StringSession

log = logging.getLogger("pdfbot.userbot")

TELEGRAPH_RE = re.compile(r"https?://(?:www\.)?(?:telegra\.ph|telegraph\.ph)/\S+", re.I)


def _extract_telegraph_url(msg) -> str | None:
    text = getattr(msg, "message", None) or ""
    m = TELEGRAPH_RE.search(text)
    if m:
        # strip trailing punctuation Telegram messages often carry
        return m.group(0).rstrip(").,;]")
    # also check hidden URLs behind text links
    entities = getattr(msg, "entities", None) or []
    try:
        from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl
        for ent in entities:
            if isinstance(ent, MessageEntityTextUrl) and "telegra.ph" in (ent.url or ""):
                return ent.url
            if isinstance(ent, MessageEntityUrl):
                chunk = text[ent.offset: ent.offset + ent.length]
                if "telegra.ph" in chunk:
                    return chunk
    except Exception:
        pass
    return None


class UserbotBridge:
    def __init__(self, api_id: int, api_hash: str, session_string: str, bot_username: str):
        self.client = TelegramClient(StringSession(session_string), api_id, api_hash)
        self.bot_username = bot_username
        self._entity = None
        self._lock = asyncio.Lock()  # serialize roundtrips — one dialog, one baseline

    async def start(self):
        await self.client.start()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "TELEGRAM_SESSION is not authorized. Re-run session_gen.py locally "
                "and update the env var."
            )
        self._entity = await self.client.get_entity(self.bot_username)

    async def stop(self):
        await self.client.disconnect()

    async def whoami(self) -> str:
        me = await self.client.get_me()
        return f"{me.first_name} (id={me.id})"

    async def request_telegraph(self, url: str, timeout: int = 30) -> str | None:
        """Send `url` to the bot, return the telegra.ph link from its reply, or None."""
        async with self._lock:
            # baseline: id of the newest message in the dialog right now
            try:
                latest = await self.client.get_messages(self._entity, limit=1)
                baseline = latest[0].id if latest else 0
            except Exception as e:
                log.warning("baseline fetch failed: %s", e)
                baseline = 0

            await self.client.send_message(self._entity, url)
            log.info("sent %s to @%s (baseline msg id=%s)", url, self.bot_username, baseline)

            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                await asyncio.sleep(1.2)
                try:
                    new_msgs = await self.client.get_messages(self._entity, min_id=baseline, limit=10)
                except Exception as e:
                    log.warning("poll error: %s", e)
                    continue
                for msg in new_msgs:
                    if getattr(msg, "out", False):
                        continue  # our own message
                    tg = _extract_telegraph_url(msg)
                    if tg:
                        log.info("telegra.ph reply received: %s", tg)
                        return tg
            log.warning("no telegra.ph reply within %ss", timeout)
            return None
