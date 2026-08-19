# Fix bundle v2 — what changed and why

Drop these files into your repo root (overwrites `bot.py`, `requirements.txt`,
and everything under `scraper/`). Then redeploy on Render.

## Files

```
bot.py                  ← site-adapter chain + live progress bar
requirements.txt        ← adds curl_cffi>=0.7 (Cloudflare bypass)
CHANGES.md              ← this file
scraper/
  __init__.py
  base.py               ← adds fetch_pdf_via_images() adapter hook
  cinblue.py            ← NEW: cin.blue dedicated adapter
  nhentai.py            ← NEW: nhentai.net dedicated adapter
  generic.py            ← fast HTTP + generic auto-detect fallback
  pdf.py                ← safe decode + auto-downscale under 50 MB
  apiadapter.py         ← unchanged XHR-replication adapter
```

## 1. cin.blue — was: "All page downloads failed"

**Root cause (from your Render log 02:21:31):** every `<img>` tag on cin.blue
is a 1x1 base64 GIF placeholder. Real image URLs exist ONLY inside the
`<script id="__NEXT_DATA__">` JSON blob at:

    data['props']['pageProps']['data']['images']['pages'][i]['t']

(verified live: 83 pages, e.g. `https://a.kontol.online/api/imageV2/i/4123809/1.webp`)

**Second problem found during testing:** the image CDN (a/b/c.kontol.online)
is behind Cloudflare and returns a 9-byte `Forbidden` to datacenter IPs —
even with full Chrome TLS impersonation, cookies, and browser headers. It
blocks by IP, not by header.

**Fix:** `scraper/cinblue.py`
1. Extracts page URLs from `__NEXT_DATA__` with the exact parser you provided.
2. Downloads via `curl_cffi` (Chrome impersonation), 8 threads in parallel.
3. On any 403, falls back to the **nhentai CDN mirror**: cin.blue's JSON
   exposes `media_id` which is identical to nhentai's (verified: gallery
   673508 → media_id 4123809 → `https://i4.nhentai.net/galleries/4123809/<n>.webp`
   serves the same bytes with HTTP 200).

## 2. nhentai.net — was: instant 403, then 429

**Root cause:** Cloudflare fingerprints TLS and blocks plain
aiohttp/requests/curl (403) regardless of User-Agent. Then, once through,
fetching 47 pages back-to-back triggers 429 rate limits (the "limit error"
you saw on the API).

**Fix:** `scraper/nhentai.py`
1. Uses `curl_cffi` with `impersonate="chrome"` → Cloudflare 200. (Verified
   live: plain curl = 403, curl_cffi = 200.)
2. Sequential pagination exactly as you described: user drops
   `https://nhentai.net/g/644028` (or `/g/644028/1/`), the bot fetches
   `/g/644028/1/`, extracts the real image URL
   (`https://i<n>.nhentai.net/galleries/<media_id>/<n>.webp`), downloads it,
   then `/2/`, `/3/` … until a page returns **404** (verified: page 48 of a
   47-page gallery = 404).
3. **Rate-limit handling:** 1.5 s pacing between page fetches (env
   `NH_PAGE_DELAY`), and on a 429 it honors `Retry-After` or backs off 8 s,
   16 s, 24 s (env `NH_BACKOFF_429`, up to 4 retries per page).
4. We deliberately do NOT use `/api/gallery/<id>` — you reported limit errors
   there; the HTML pagination path worked through a full 47-page gallery in
   testing (final PDF: 19 MB).

## 3. Progress bar — page counter + percent bar (both)

`bot.py` edits the status message in place (throttled to ~1 edit / 1.2 s so
Telegram doesn't rate-limit the bot). During a download the user sees:

```
⬇️ nhentai: downloading…
▓▓▓▓▓▓▓░░░░░░░░░░░ 40%  (19/47)
```

For nhentai the total is discovered as it goes (it shows the counter
incrementing live), then the final PDF size when done.

## 4. 403 policy (as you chose)

If a site blocks Render's IP outright (Cloudflare 403 that impersonation
can't fix), the bot logs the blocked URL and replies:

```
❌ RuntimeError: nhentai blocked the request (Cloudflare 403). ...
```

("Is there any way to fix it?" — yes, but only via a residential proxy or a
hosting provider whose IPs aren't on Cloudflare's datacenter blocklist. If
you later get a proxy, ask and I'll wire a `PROXY_URL` env var through both
adapters.)

## 5. Health-check HEAD fix

Your UptimeRobot pings were hitting the aiohttp server with `HEAD /` — the
old app only routed `GET`, so every ping logged a 405/404 pair. `bot.py` now
routes both `GET /` and `HEAD /` → 200. Logs stay clean.

## New environment variables (all optional)

- `CONCURRENCY` — parallel downloads for generic fallback, default `12`
- `PER_HOST` — per-host connection cap, default `6`
- `PAGE_CAP` — max pages for generic fallback, default `120`
- `NH_PAGE_DELAY` — seconds between nhentai page fetches, default `1.5`
- `NH_BACKOFF_429` — base backoff after a 429, default `8`

## Verified live before packaging (2026-08-19)

- cin.blue `/v/673508`: extracted 83 page URLs from `__NEXT_DATA__`; 3-page
  pipeline produced an 822 KB valid PDF via the mirror fallback.
- nhentai `/g/644028/`: full 47-page scrape, 404 termination, two 429s
  retried successfully, 19 MB valid PDF compiled.
- All modules pass syntax + import checks; URL matching unit tests pass for
  both adapters (incl. rejecting other domains).
