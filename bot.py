import asyncio, logging, os, re, time
from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from scraper.generic import DirectPDFAdapter, HTMLImageAdapter, download_images
from scraper.apiadapter import APIReplicateAdapter, sniff_next_data, find_urls_in_json, find_image_urls_in_json
from scraper.pdf import images_to_pdf

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pdfbot")
TOKEN = os.environ["BOT_TOKEN"]
URL_RE = re.compile(r"https?://\S+")
PAGE_CAP = int(os.environ.get("PAGE_CAP", "120"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "12"))
PER_HOST = int(os.environ.get("PER_HOST", "6"))


def _bar(done: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return "░" * width + " 0%"
    frac = max(0.0, min(1.0, done / total))
    filled = int(round(frac * width))
    return "▓" * filled + "░" * (width - filled) + f" {int(frac * 100)}%"


class Progress:
    """Throttled status-message editor. Coalesces updates so we don't spam Telegram."""

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
            pass  # "message is not modified" etc.

    def stage(self, label: str, done: int = 0, total: int = 0):
        if total > 0:
            return f"{label}\n{_bar(done, total)}  ({done}/{total})"
        return f"{label}"


def _new_session() -> ClientSession:
    """One session per request, tuned for reader-site CDNs (Render 512 MB safe)."""
    connector = TCPConnector(
        limit=CONCURRENCY * 2,
        limit_per_host=PER_HOST,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    timeout = ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=30)
    return ClientSession(connector=connector, timeout=timeout)


async def process(url: str, progress: Progress) -> tuple[bytes, str]:
    async with _new_session() as s:
        # 1) replicate the JS button's XHR call (if configured)
        await progress.set(progress.stage("🔎 Checking for direct PDF endpoint…"), force=True)
        api = APIReplicateAdapter()
        if api.match(url):
            log.info("trying API-replication adapter for %s", url)
            pdf = await api.fetch_pdf(s, url)
            if pdf:
                return pdf, "document.pdf"

        # 2) Direct-PDF probe on the URL itself
        direct = DirectPDFAdapter()
        pdf = await direct.fetch_pdf(s, url)
        if pdf:
            return pdf, "document.pdf"

        # 3) Auto-detect image list (Next.js JSON blob first, then HTML)
        await progress.set(progress.stage("🧭 Parsing reader page…"), force=True)
        html_adapter = HTMLImageAdapter()
        image_urls = await html_adapter.fetch_image_urls(s, url)

        if not image_urls:
            raise RuntimeError("No downloadable images or PDF were found on this page.")

        if len(image_urls) > PAGE_CAP:
            image_urls = image_urls[:PAGE_CAP]

        total = len(image_urls)
        await progress.set(progress.stage(f"⬇️ Downloading {total} pages…", 0, total), force=True)

        done = 0

        async def on_progress(delta: int):
            nonlocal done
            done += delta
            await progress.set(progress.stage(f"⬇️ Downloading {total} pages…", done, total))

        pages = await download_images(s, image_urls, concurrency=CONCURRENCY, on_progress=on_progress)

        if not pages:
            raise RuntimeError("All page downloads failed. The site may be blocking or the URLs may be stale.")

        await progress.set(progress.stage(f"📚 Compiling PDF from {len(pages)} pages…"), force=True)
        pdf = await asyncio.to_thread(images_to_pdf, pages)
        return pdf, "document.pdf"


bot = Bot(TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Send me a reader / gallery link and I'll fetch the pages and return a PDF.\n"
        "• Direct PDFs are grabbed as-is.\n"
        "• For image readers I auto-detect hi-res URLs (lazy-load, srcset, __NEXT_DATA__).\n"
        f"• Cap: {PAGE_CAP} pages, {CONCURRENCY} parallel downloads."
    )


@dp.message(F.text)
async def handle(m: Message):
    urls = URL_RE.findall(m.text or "")
    if not urls:
        await m.reply("Send a URL.")
        return
    url = urls[0]
    status = await m.answer("⏳ Starting…")
    progress = Progress(bot, status.chat.id, status.message_id)
    started = time.monotonic()
    try:
        pdf, name = await process(url, progress)
        elapsed = time.monotonic() - started
        await progress.set(f"✅ Done in {elapsed:.1f}s — uploading PDF…", force=True)
        await m.answer_document(BufferedInputFile(pdf, filename=name))
    except Exception as e:
        log.exception("process failed")
        await progress.set(f"❌ {type(e).__name__}: {e}", force=True)
        return
    try:
        await status.delete()
    except TelegramBadRequest:
        pass


async def _health(_req):
    return web.Response(text="ok")


async def main():
    app = web.Application()
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "10000")))
    await site.start()
    log.info("HTTP health server started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
