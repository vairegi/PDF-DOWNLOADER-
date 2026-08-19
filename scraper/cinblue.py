"""cin.blue adapter — pulls hi-res page URLs out of the embedded `__NEXT_DATA__`
JSON blob, with an nhentai-CDN mirror fallback.

Verified 2026-08-19 against /v/673508:

  * HTML host (cin.blue) serves the page fine, but every <img> is a 1x1 GIF
    placeholder. Real URLs live ONLY in the JSON blob:
        data['props']['pageProps']['data']['images']['pages'][i]['t']
      e.g. https://a.kontol.online/api/imageV2/i/4123809/1.webp
  * The image CDN (a/b/c.kontol.online) is behind Cloudflare and returns a
    9-byte "Forbidden" to datacenter IPs — even with full Chrome TLS
    impersonation, cookies, and browser headers. It blocks by IP, not headers.
  * cin.blue mirrors nhentai: the JSON's `media_id` (4123809) is identical to
    nhentai's, and `https://i4.nhentai.net/galleries/<media_id>/<n>.webp`
    serves the same bytes with a 200 via curl_cffi impersonation.

Download order per page: original kontol.online URL first (works from
residential IPs / if the block lifts), nhentai mirror on any failure.
"""
from __future__ import annotations
import asyncio, json, logging, re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from .base import BaseAdapter
from .generic import _get

log = logging.getLogger("pdfbot.cinblue")

_HOST_RE = re.compile(r"(^|\.)cin\.blue$", re.I)
_PAGE_NUM_RE = re.compile(r"/(\d+)\.(webp|jpe?g|png|gif)(\?|#|$)", re.I)
_MIRROR_HOST = "i4.nhentai.net"


class CinBlueAdapter(BaseAdapter):
    name = "cin.blue"

    def match(self, url: str) -> bool:
        return bool(_HOST_RE.search(urlparse(url).netloc))

    # ---- extraction (exact parser logic provided by the user) ----

    def _extract(self, html: bytes | str) -> tuple[list[str], str | None]:
        """Return (image_urls, media_id)."""
        soup = BeautifulSoup(html, "html.parser")
        script_tag = soup.find("script", id="__NEXT_DATA__")
        if not script_tag or not script_tag.string:
            return [], None
        try:
            data = json.loads(script_tag.string)
        except Exception:
            return [], None
        d = (data.get("props", {})
                  .get("pageProps", {})
                  .get("data", {}))
        pages = d.get("images", {}).get("pages", [])
        urls = [p["t"] for p in pages if isinstance(p, dict) and p.get("t")]
        media_id = d.get("media_id")
        return urls, (str(media_id) if media_id else None)

    # ---- downloading ----

    @staticmethod
    def _mirror_url(original: str, media_id: str) -> str | None:
        m = _PAGE_NUM_RE.search(urlparse(original).path)
        if not m:
            return None
        n, ext = m.group(1), m.group(2).lower()
        if ext == "jpeg":
            ext = "jpg"
        return f"https://{_MIRROR_HOST}/galleries/{media_id}/{n}.{ext}"

    async def fetch_pdf_via_images(self, session: ClientSession, url: str,
                                   on_progress=None, set_total=None):
        try:
            from curl_cffi import requests as cf_requests
        except ImportError:
            raise RuntimeError(
                "curl_cffi is required (Cloudflare bypass). "
                "Add `curl_cffi>=0.7` to requirements.txt."
            )

        _, _, body = await _get(session, url)
        urls, media_id = self._extract(body)
        if not urls:
            return None
        if set_total:
            set_total(len(urls))

        mirrors = [self._mirror_url(u, media_id) if media_id else None for u in urls]
        total = len(urls)
        loop = asyncio.get_event_loop()

        def _fetch_one(i: int) -> bytes | None:
            u = urls[i]
            try:
                r = cf_requests.get(u, impersonate="chrome", timeout=60,
                                    headers={"Referer": url})
                if r.status_code == 403 and mirrors[i]:
                    log.info("cin.blue page %d: primary 403, using nhentai mirror", i + 1)
                    r = cf_requests.get(mirrors[i], impersonate="chrome", timeout=60)
                if r.status_code == 200 and len(r.content) > 500:  # not the 9-byte Forbidden
                    return r.content
                log.warning("cin.blue page %d: HTTP %d (%d bytes)", i + 1, r.status_code, len(r.content))
            except Exception as e:
                log.warning("cin.blue page %d fetch error: %s", i + 1, e)
            return None

        def _scrape() -> list[bytes]:
            results: list[bytes | None] = [None] * total
            done = 0
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_fetch_one, i): i for i in range(total)}
                for fut in futures:
                    pass  # submitted
                from concurrent.futures import as_completed
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        results[i] = fut.result()
                    except Exception as e:
                        log.warning("cin.blue page %d exception: %s", i + 1, e)
                    done += 1
                    if on_progress:
                        try:
                            asyncio.run_coroutine_threadsafe(on_progress(1), loop)
                        except Exception:
                            pass
            return [b for b in results if b]

        pages = await asyncio.to_thread(_scrape)
        if not pages:
            raise RuntimeError(
                "cin.blue: every page download failed — the image CDN "
                "(kontol.online) is blocking this server's IP and the "
                "nhentai mirror didn't have this gallery."
            )
        from .pdf import images_to_pdf
        return await asyncio.to_thread(images_to_pdf, pages)
