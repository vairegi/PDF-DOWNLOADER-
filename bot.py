"""Hybrid bot: BotFather bot (aiogram) talks to users; a Telethon userbot
does the @nHentaiBot roundtrip in the background and hands back a
telegra.ph article URL, which we scrape (no anti-bot there) into a PDF.

Required env vars (Render):
  BOT_TOKEN          BotFather token
  API_ID             from my.telegram.org
  API_HASH           from my.telegram.org
  TELEGRAM_SESSION   Telethon StringSession (run session_gen.py locally once)

Optional:
  NH_BOT_USERNAME    default "nHentaiBot"
  NH_TIMEOUT         seconds to wait for @nHentaiBot reply, default 30
  PAGE_CAP           default 120
  CONCURRENCY        parallel image downloads, default 12
  PORT               Render injects this; default 10000
"""
import asyncio, logging, os, re, time
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest

from scraper.userbot_bridge import UserbotBridge
from scraper.telegraph import fetch_telegraph_image_urls
from scraper.generic import download_images
from scraper.pdf import images_to_pdf

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pdfbot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TELEGRAM_SESSION = os.environ["TELEGRAM_SESSION"]
NH_BOT = os.environ.get("NH_BOT_USERNAME", "nHentaiBot").lstrip("@")
NH_TIMEOUT = int(os.environ.get("NH_TIMEOUT", "30"))
PAGE_CAP = int(os.environ.get("PAGE_CAP", "120"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "12"))

URL_RE = re.compile(r"https?://\S+")
NHENTAI_RE = re.compile(r"https?://(?:www\.)?nhentai\.net/g/\d+", re.I)


def _bar(done: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return "░" * width + " 0%"
    frac = max(0.0, min(1.0, done / total))
    filled = int(round(frac * width))
    return "▓" * filled + "░" * (width - filled) + f" {int(frac * 100)}%"


class Progress:
    """Throttled status-message editor."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int, min_interval: float = 1.2):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.min_interval = min_interval
        self._last_text = ""
        self._last_edit = 0.0
        self._lock = asyncio.Lock()

    async def set(self, text: str, force: bool = False):
        now = time.monotonic()
        async with self._lock:
            if not force and text == self._last_text:
                return
            if not force and (now - self._last_edit) < self.min_interval:
                return
            self._last_text = text
            self._last_edit = now
        try:
            await self.bot.edit_message_text(text, chat_id=self.chat_id, message_id=self.message_id)
        except TelegramBadRequest:
            pass

    def stage(self, label: str, done: int = 0, total: int = 0) -> str:
        if total > 0:
            return f"{label}\n{_bar(done, total)}  ({done}/{total})"
        return label


def _new_session() -> ClientSession:
    connector = TCPConnector(limit=CONCURRENCY * 2, limit_per_host=CONCURRENCY,
                             ttl_dns_cache=300, enable_cleanup_closed=True)
    timeout = ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=30)
    return ClientSession(connector=connector, timeout=timeout)


bot = Bot(BOT_TOKEN)
dp = Dispatcher()
bridge = UserbotBridge(API_ID, API_HASH, TELEGRAM_SESSION, NH_BOT)


@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Send me an nhentai.net gallery link (e.g. https://nhentai.net/g/123456).\n"
        "I'll fetch the Telegra.ph article for it and return a PDF.\n"
        f"Cap: {PAGE_CAP} pages."
    )


@dp.message(F.text)
async def handle(m: Message):
    text = m.text or ""
    urls = URL_RE.findall(text)
    if not urls:
        await m.reply("Send an nhentai.net gallery link.")
        return
    url = urls[0]
    if not NHENTAI_RE.search(url):
        await m.reply("❌ Only nhentai.net gallery links are supported.")
        return

    status = await m.answer("⏳ Starting…")
    progress = Progress(bot, status.chat.id, status.message_id)
    started = time.monotonic()

    try:
        # 1) userbot roundtrip: send URL to @nHentaiBot, wait for telegra.ph reply
        await progress.set(f"🤝 Asking @{NH_BOT} for the Telegra.ph article…", force=True)
        tg_url = await bridge.request_telegraph(url, timeout=NH_TIMEOUT)
        if not tg_url:
            raise RuntimeError(f"@{NH_BOT} did not respond — try again later")

        # 2) extract image URLs from the telegra.ph article
        await progress.set("📄 Got Telegra.ph article — extracting images…", force=True)
        async with _new_session() as s:
            image_urls = await fetch_telegraph_image_urls(s, tg_url)
            if not image_urls:
                raise RuntimeError("Telegra.ph article contained no images")
            if len(image_urls) > PAGE_CAP:
                image_urls = image_urls[:PAGE_CAP]

            total = len(image_urls)
            done = 0
            await progress.set(progress.stage(f"⬇️ Downloading {total} pages…", 0, total), force=True)

            async def on_progress(delta: int):
                nonlocal done
                done += delta
                await progress.set(progress.stage(f"⬇️ Downloading {total} pages…", done, total))

            pages = await download_images(s, image_urls, concurrency=CONCURRENCY,
                                          on_progress=on_progress, referer=tg_url)

        if not pages:
            raise RuntimeError("All image downloads failed")

        # 3) compile + send
        await progress.set(f"📚 Compiling PDF from {len(pages)} pages…", force=True)
        pdf = await asyncio.to_thread(images_to_pdf, pages)

        m_id = re.search(r"/g/(\d+)", url)
        name = f"nhentai_{m_id.group(1)}.pdf" if m_id else "document.pdf"
        elapsed = time.monotonic() - started
        await progress.set(f"✅ Done in {elapsed:.1f}s — uploading PDF…", force=True)
        await m.answer_document(BufferedInputFile(pdf, filename=name))

    except Exception as e:
        log.exception("process failed")
        await progress.set(f"❌ {e}", force=True)
        return
    try:
        await status.delete()
    except TelegramBadRequest:
        pass


async def _health(_req):
    return web.Response(text="ok")


async def main():
    # userbot first — if the session is invalid we fail fast at startup
    await bridge.start()
    log.info("userbot connected as %s", await bridge.whoami())

    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_head("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "10000")))
    await site.start()
    log.info("HTTP health server started")

    try:
        await dp.start_polling(bot)
    finally:
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
