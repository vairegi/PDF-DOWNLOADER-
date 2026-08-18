import asyncio, logging, os, re
from aiohttp import ClientSession, web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile
from scraper.base import HEADERS
from scraper.generic import DirectPDFAdapter, HTMLImageAdapter, download_images
from scraper.pdf import images_to_pdf

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]
ADAPTERS = [DirectPDFAdapter(), HTMLImageAdapter()]  # DirectPDF matches everything, HTMLImage is fallback
URL_RE = re.compile(r"https?://\S+")

async def process(url: str) -> tuple[bytes, str]:
    async with ClientSession() as s:
        direct = ADAPTERS[0]
        pdf = await direct.fetch_pdf(s, url)
        if pdf:
            return pdf, url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
        # fallback: images -> pdf
        img_adapter = ADAPTERS[1]
        imgs = await img_adapter.fetch_image_urls(s, url)
        if not imgs:
            raise RuntimeError("no PDF link and no images found")
        imgs = imgs[:120]  # hard cap for 512MB RAM
        pages = await download_images(s, imgs)
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
            await m.answer_document(BufferedInputFile(pdf, filename=name))
        except Exception as e:
            await m.answer(f"❌ Failed: {e}")
        finally:
            await status.delete()

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
