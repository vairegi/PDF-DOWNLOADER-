import asyncio, logging, os, re
from aiohttp import ClientSession, web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile
from scraper.generic import DirectPDFAdapter, HTMLImageAdapter, download_images
from scraper.apiadapter import APIReplicateAdapter, sniff_next_data, find_urls_in_json, find_image_urls_in_json
from scraper.pdf import images_to_pdf

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pdfbot")
TOKEN = os.environ["BOT_TOKEN"]
URL_RE = re.compile(r"https?://\S+")
PAGE_CAP = int(os.environ.get("PAGE_CAP", "120"))

async def process(url: str) -> tuple[bytes, str]:
    async with ClientSession() as s:
        # 1) replicate the JS button's XHR call (if configured)
        api = APIReplicateAdapter()
        if api.match(url):
            log.info("trying API-replication adapter for %s", url)
            pdf = await api.fetch_pdf(s, url)
            if pdf:
                log.info("API adapter hit, %d bytes", len(pdf))
                return pdf, f"{url.rsplit('/', 1)[-1].split('?')[0] or 'document'}.pdf"

        # 2) generic direct-PDF (url is a pdf, or page links to one)
        direct = DirectPDFAdapter()
        pdf = await direct.fetch_pdf(s, url)
        if pdf:
            log.info("direct PDF hit, %d bytes", len(pdf))
            return pdf, url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"

        # 3) sniff __NEXT_DATA__ for a hidden pdf url OR full image list
        from scraper.generic import _get
        _, _, body = await _get(s, url)
        nd = sniff_next_data(body)
        if nd:
            pdfs = find_urls_in_json(nd)
            for cand in pdfs:
                from urllib.parse import urljoin
                try:
                    _, _, data = await _get(s, urljoin(url, cand))
                    if data[:4] == b"%PDF":
                        log.info("NEXT_DATA pdf hit: %s", cand)
                        return data, "document.pdf"
                except Exception:
                    continue
            imgs = find_image_urls_in_json(nd)
        else:
            imgs = []

        # 4) classic lazy-load HTML fallback
        if not imgs:
            imgs = await HTMLImageAdapter().fetch_image_urls(s, url)
        if not imgs:
            raise RuntimeError("no PDF link and no images found")

        imgs = imgs[:PAGE_CAP]
        log.info("compiling %d page images", len(imgs))
        pages = await download_images(s, imgs)
        if not pages:
            raise RuntimeError("image downloads all failed")
        return images_to_pdf(list(pages)), "compiled.pdf"

async def main():
    bot, dp = Bot(TOKEN), Dispatcher()

    @dp.message(CommandStart())
    async def start(m: Message):
        await m.answer("Send me a link. I'll fetch the PDF, or compile page images into one.")

    @dp.message(F.text.regexp(URL_RE))
    async def handle(m: Message):
        url = URL_RE.search(m.text).group(0)
        status = await m.answer("⏳ Fetching…")
        try:
            pdf, name = await process(url)
            log.info("sending %s (%d bytes) to chat %s", name, len(pdf), m.chat.id)
            await m.answer_document(BufferedInputFile(pdf, filename=name))
        except Exception as e:
            log.exception("FAILED to process %s", url)   # full traceback in Render logs
            await m.answer(f"❌ Failed: {type(e).__name__}: {e}")
        finally:
            await status.delete()

    @dp.message()
    async def fallback(m: Message):
        if m.text:
            await m.answer("That doesn't look like a link — send a full https:// URL.")

    # tiny HTTP server so Render free tier sees an open port
    async def health(_): return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
