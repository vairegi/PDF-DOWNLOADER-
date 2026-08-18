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

## Env-var config for JS-button sites (API replication)
Set these on Render after reverse-engineering the button (F12 → Network → XHR → click "Download ALL"):
- `API_URL_TEMPLATE` e.g. `https://site.com/api/download/{id}`
- `API_METHOD` GET or POST
- `API_JSON_TEMPLATE` POST body, e.g. `{"id": "{id}"}`
- `API_EXTRA_HEADERS` JSON dict of any token headers the request carried
- `API_RESPONSE_MODE` `direct` (response IS the pdf) or `jsonurl` (JSON containing a pdf link)
- `PAGE_CAP` max reader pages to compile (default 120)

Full reverse-engineering walkthrough is documented at the top of `scraper/apiadapter.py`.

## Add your own site
Subclass `BaseAdapter`, implement `match()` + `fetch_pdf()` (and optionally
`fetch_image_urls()`), and put it in `ADAPTERS` BEFORE the generic catch-alls.
Only target sites whose terms permit programmatic download.
