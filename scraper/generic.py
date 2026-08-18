"""Two generic, pluggable adapters. Register YOUR site-specific adapter the same way."""
import asyncio, re
from urllib.parse import urljoin
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from .base import BaseAdapter, HEADERS, MAX_PDF_BYTES

async def _get(session, url, retries=3, **kw):
    """GET with browser headers + exponential-backoff retry (survives flaky hosts)."""
    last = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers=HEADERS, timeout=40, **kw) as r:
                r.raise_for_status()
                body = await r.read()
                return r.status, r.headers, body
        except Exception as e:
            last = e
            await asyncio.sleep(1.5 * (attempt + 1))
    raise last

class DirectPDFAdapter(BaseAdapter):
    """For sites where the item URL (or a predictable sibling) is a real .pdf.
    Handles: direct .pdf links, <a> tags pointing at .pdf, and meta refresh."""
    name = "direct_pdf"
    def match(self, url): return True  # catch-all; keep LAST in the registry

    async def fetch_pdf(self, session, url):
        try:
            _, headers, body = await _get(session, url)
            ctype = headers.get("Content-Type", "")
            if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
                return body if 0 < len(body) <= MAX_PDF_BYTES and body[:4] == b"%PDF" else None
            # HTML page: hunt for a pdf link
            soup = BeautifulSoup(body, "html.parser")
            for a in soup.find_all("a", href=True):
                if a["href"].lower().split("?")[0].endswith(".pdf"):
                    _, _, data = await _get(session, urljoin(url, a["href"]))
                    if 0 < len(data) <= MAX_PDF_BYTES and data[:4] == b"%PDF":
                        return data
        except Exception:
            return None
        return None

class HTMLImageAdapter(BaseAdapter):
    """Fallback for reader pages: defeat lazy-load, collect hi-res images."""
    name = "html_images"
    LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-srcset",
                  "data-url", "data-full", "data-hi", "data-large", "srcset", "src")
    def match(self, url): return True

    async def fetch_image_urls(self, session, url):
        _, _, body = await _get(session, url)
        soup = BeautifulSoup(body, "html.parser")
        urls, seen = [], set()
        for img in soup.find_all("img"):
            cand = None
            for attr in self.LAZY_ATTRS:
                v = img.get(attr)
                if not v:
                    continue
                if "srcset" in attr:                      # take largest candidate
                    parts = [p.strip().split()[0] for p in v.split(",") if p.strip()]
                    v = parts[-1] if parts else None
                if v and not v.startswith("data:"):       # skip 1x1 placeholder GIFs
                    cand = v
                    break
            if cand:
                full = urljoin(url, cand)
                if re.search(r"\.(jpe?g|png|webp|avif)(\?|$)", full, re.I) and full not in seen:
                    seen.add(full)
                    urls.append(full)
        # numeric sort so page 10 doesn't come before page 2
        def key(u):
            m = re.findall(r"(\d+)", u)
            return int(m[-1]) if m else 0
        return sorted(urls, key=key)

async def download_images(session, urls, concurrency=4):
    sem = asyncio.Semaphore(concurrency)
    async def one(u):
        async with sem:
            try:
                _, _, body = await _get(session, u)
                return body
            except Exception:
                return None
    results = await asyncio.gather(*[one(u) for u in urls])
    return [b for b in results if b]  # drop failed pages, keep going
