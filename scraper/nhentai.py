"""nhentai.net adapter — sequential pagination with Cloudflare bypass.

Strategy (verified 2026-08-19 against /g/644028):

  * Plain aiohttp/requests get 403 from Cloudflare. `curl_cffi` with Chrome
    TLS impersonation gets 200.
  * The HTML at /g/<id>/<n>/ contains the real image URL:
        <img ... src="https://i4.nhentai.net/galleries/3890662/1.webp" ...>
  * Page 48 on a 47-page gallery returns 404 -> clean termination signal.
  * The CDN (i<n>.nhentai.net) serves the full webp with no rate limit
    observed during testing.

We intentionally DO NOT use the /api/gallery/<id> endpoint because the user
reported rate-limit errors on it; the sequential page-by-page approach
spreads requests across time and uses only public HTML.
"""
from __future__ import annotations
import asyncio, logging, os, re, time
from urllib.parse import urlparse
from aiohttp import ClientSession
from .base import BaseAdapter

log = logging.getLogger("pdfbot.nhentai")

# Polite pacing: nhentai returns 429 if pages are fetched back-to-back.
PAGE_DELAY = float(os.environ.get("NH_PAGE_DELAY", "1.5"))   # s between page fetches
BACKOFF_429 = float(os.environ.get("NH_BACKOFF_429", "8"))  # s wait after a 429
MAX_RETRIES = 4

_IMG_RE = re.compile(
    r'src="(https://i\d*\.nhentai\.net/galleries/[^"]+\.(?:jpg|jpeg|png|webp|gif))"',
    re.I,
)
_GALLERY_PATH_RE = re.compile(r"^/g/(\d+)", re.I)
_HOST_RE = re.compile(r"(^|\.)nhentai\.net$", re.I)


class NHentaiAdapter(BaseAdapter):
    name = "nhentai"

    def match(self, url: str) -> bool:
        return self._gallery_id(url) is not None

    def _gallery_id(self, url: str) -> str | None:
        p = urlparse(url)
        if not _HOST_RE.search(p.netloc):
            return None
        m = _GALLERY_PATH_RE.match(p.path)
        return m.group(1) if m else None

    async def fetch_pdf_via_images(self, session: ClientSession, url: str,
                                   on_progress=None, set_total=None):
        try:
            from curl_cffi import requests as cf_requests
        except ImportError:
            raise RuntimeError(
                "curl_cffi is required for nhentai (Cloudflare bypass). "
                "Add `curl_cffi>=0.7` to requirements.txt."
            )

        gid = self._gallery_id(url)
        if not gid:
            return None

        def _scrape() -> list[bytes]:
            """Blocking scrape; run in a thread so we don't stall the event loop.

            Sequential pagination /g/<id>/1/ -> /2/ -> ... until 404, with
            polite pacing + 429-aware backoff so nhentai doesn't rate-limit us.
            """
            sess = cf_requests.Session(impersonate="chrome")
            pages: list[bytes] = []
            n = 1
            last_req = 0.0

            def paced_get(u: str, **kw):
                nonlocal last_req
                wait = PAGE_DELAY - (time.monotonic() - last_req)
                if wait > 0:
                    time.sleep(wait)
                last_req = time.monotonic()
                last_exc = None
                for attempt in range(MAX_RETRIES):
                    try:
                        r = sess.get(u, **kw)
                        if r.status_code == 429:
                            retry_after = r.headers.get("Retry-After")
                            pause = float(retry_after) if (retry_after or "").isdigit() \
                                else BACKOFF_429 * (attempt + 1)
                            log.warning("nhentai 429 on %s; backing off %.0fs", u, pause)
                            time.sleep(pause)
                            continue
                        return r
                    except Exception as e:
                        last_exc = e
                        time.sleep(2 * (attempt + 1))
                if last_exc:
                    raise last_exc
                return r  # type: ignore[unreachable]

            while True:
                page_url = f"https://nhentai.net/g/{gid}/{n}/"
                try:
                    r = paced_get(page_url, timeout=30)
                except Exception as e:
                    log.warning("nhentai page %d fetch error: %s", n, e)
                    break
                if r.status_code == 404:
                    break
                if r.status_code == 403:
                    raise RuntimeError(
                        "nhentai blocked the request (Cloudflare 403). "
                        "Render's IP may be flagged; try again later."
                    )
                r.raise_for_status()

                m = _IMG_RE.search(r.text)
                if not m:
                    log.warning("nhentai page %d: no image tag found", n)
                    break
                img_url = m.group(1)

                try:
                    ri = paced_get(img_url, timeout=60, headers={"Referer": page_url})
                    ri.raise_for_status()
                except Exception as e:
                    log.warning("nhentai page %d img fetch error: %s", n, e)
                    break
                pages.append(ri.content)

                if on_progress:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(on_progress(1), loop)
                    except Exception:
                        pass
                n += 1
            return pages

        pages = await asyncio.to_thread(_scrape)
        if not pages:
            return None
        if set_total:
            set_total(len(pages))
        from .pdf import images_to_pdf
        return await asyncio.to_thread(images_to_pdf, pages)
