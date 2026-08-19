# Fix bundle — what changed and why

Drop these files into your repo root (overwrites `bot.py`, `scraper/base.py`,
`scraper/generic.py`, `scraper/pdf.py`, `scraper/apiadapter.py`,
`requirements.txt`). Nothing else needs to change; `render.yaml` and env vars
still work.

---

## 1. "It only downloads thumbnails"

**Root cause.** The old `HTMLImageAdapter` iterated attributes in a fixed
order and stopped on the first hit — but that hit is often `src="thumb.jpg"`
because the hi-res URL lives in an attribute the old list didn't cover
(`data-original`, `data-page`, `data-zoom`, `__NEXT_DATA__` JSON, or a
`<noscript>` mirror). It also silently dropped anything without a plain
`.jpg/.png/.webp/.avif` extension, which killed CDN URLs that use a proxied
path.

**Fix in `scraper/generic.py`.**
- Collect **every** plausible URL per `<img>` (all `data-*`, `srcset`,
  `src`).
- Also mine `<noscript>` mirrors and inline JSON: `__NEXT_DATA__`,
  `application/ld+json`, `window.__DATA__`, `pages=[...]` assignments.
- Score-rank candidates: penalize URLs containing `thumb|small|preview|
  placeholder|cover|icon|sprite|=s\d+|_s.jpg|/t/…`, boost ones containing
  `full|large|orig|hd|hires|/page/`. Keep every candidate within 4 points of
  the top score, drop the rest — so thumbnails get filtered but a real page
  variant is never lost.
- Numeric sort by the last integer in the URL path (page 2 before page 10).

## 2. "100 KB takes 5+ minutes"

**Root cause.** The old `_get` had `timeout=40` (total), `1.5 × attempt`
sleeps between retries, `concurrency=4`, no per-host limit, no streaming, no
content-type filter, and no size guard. A single slow CDN response could
stall four workers for 40 s each while the retry backoff added another 9 s
per failure. And when a captcha/HTML page returned 200, the bytes went into
Pillow and blew up the whole batch.

**Fix.**
- One tuned `aiohttp.ClientSession` per request:
  `TCPConnector(limit=24, limit_per_host=6, ttl_dns_cache=300)` — matches
  what browsers actually do against CDNs.
- Split timeouts: `connect=10s`, `sock_read=30s`, no total cap, so slow-first
  -byte doesn't kill the whole request but a truly dead socket dies fast.
- `_stream_image`: streams in 64 KB chunks, rejects any response whose
  `Content-Type` isn't an image (kills captcha/HTML pages instantly), enforces
  a 20 MB per-page cap, and skips 4xx immediately (no retry storm).
- Backoff dropped from `1.5, 3.0, 4.5` s to `0.4, 0.8` s; total retry budget
  for a dead page ~1.2 s instead of ~9 s.
- Default `CONCURRENCY=12` (was 4). Tunable via env var.

## 3. "No progress bar"

**Root cause.** `bot.py` sent one status message ("⏳ Fetching…"), never
edited it, and `process()` had no callback into it.

**Fix in `bot.py`.**
- New `Progress` class edits the status message in place, throttled to at
  most one edit / ~1.2 s (Telegram rate-limit safe), and coalesces identical
  text.
- `download_images` now takes an `on_progress(delta)` callback and fires it
  after every page finishes.
- Status message shows the current stage plus a combined bar:
  `⬇️ Downloading 80 pages…`
  `▓▓▓▓▓▓▓░░░░░░░░░░░  40%  (32/80)`

## 4. Robustness bonuses

- `scraper/pdf.py` now flattens RGBA/palette/CMYK correctly, skips
  undecodable bytes instead of crashing the batch, and downscales oversized
  pages so the compiled PDF stays under Telegram's 50 MB limit.
- `PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True` so a partial CDN read still
  produces a usable page.
- Broader `Accept` header (adds `image/avif`, `image/webp`, `Accept-Language`).

## New environment variables (all optional)

- `CONCURRENCY` — parallel image downloads, default `12`.
- `PER_HOST` — per-host connection cap, default `6`.
- `PAGE_CAP` — unchanged, still defaults to `120`.
