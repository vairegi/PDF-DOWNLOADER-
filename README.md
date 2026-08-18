# PDF Fetch Bot (Render free-tier safe)

Aiogram 3 + aiohttp + Pillow. No headless browser. Streams within 512MB.

## How it works
1. User sends a link.
2. `DirectPDFAdapter` tries: URL itself is a PDF → page contains an `<a ...pdf>` link.
3. If that fails, `HTMLImageAdapter` parses the reader page, defeats lazy-loading
   (`data-src` / `srcset` / placeholder-GIF filtering), downloads hi-res images
   with bounded concurrency, and compiles them into one PDF via Pillow.
4. Bot replies with the PDF (must stay <50MB for Bot API).

## Deploy on Render
- New Web Service from this repo. Build: `pip install -r requirements.txt`, Start: `python bot.py`.
- Set env var `BOT_TOKEN` (from @BotFather).
- `render.yaml` included. Health endpoint on `/` satisfies the port check.
- Free tier sleeps: use an external pinger (e.g. UptimeRobot) on the service URL.

## Add your own site
Subclass `BaseAdapter`, implement `match()` + `fetch_pdf()` (and optionally
`fetch_image_urls()`), and put it in `ADAPTERS` BEFORE the generic catch-alls.
Only target sites whose terms permit programmatic download.
