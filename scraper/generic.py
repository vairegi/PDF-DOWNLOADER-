"""Two generic, pluggable adapters + shared HTTP helpers.

Used as fallback when no site-specific adapter (cin.blue / nhentai) matches.
"""
from __future__ import annotations
import asyncio, json, re
from urllib.parse import urljoin, urlparse
from aiohttp import ClientSession, ClientResponseError
from bs4 import BeautifulSoup
from .base import BaseAdapter, HEADERS, MAX_PDF_BYTES, IMAGE_CTYPES

# --------------------------- low-level HTTP helpers ---------------------------

async def _get(session: ClientSession, url: str, retries: int = 3, **kw):
    """GET with browser headers + short exponential backoff.

    Returns (status, headers, body_bytes). Raises on final failure.
    """
    last = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers=HEADERS, **kw) as r:
                r.raise_for_status()
                body = await r.read()
                return r.status, r.headers, body
        except ClientResponseError as e:
            last = e
            if 400 <= e.status < 500 and e.status not in (408, 425, 429):
                raise
            await asyncio.sleep(0.4 * (attempt + 1))
        except Exception as e:
            last = e
            await asyncio.sleep(0.4 * (attempt + 1))
    raise last


async def _stream_image(session: ClientSession, url: str, referer: str | None = None,
                        max_bytes: int = 20 * 1024 * 1024, retries: int = 2) -> bytes | None:
    """Stream an image URL, reject non-image content early, size-cap."""
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    for attempt in range(retries + 1):
        try:
            async with session.get(url, headers=headers) as r:
                if r.status >= 400:
                    if 400 <= r.status < 500 and r.status not in (408, 425, 429):
                        return None
                    raise ClientResponseError(r.request_info, r.history, status=r.status,
                                              message=r.reason or "http error", headers=r.headers)
                ctype = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
                if ctype and not any(ctype.startswith(c) for c in IMAGE_CTYPES):
                    return None
                clen = r.headers.get("Content-Length")
                if clen and clen.isdigit() and int(clen) > max_bytes:
                    return None
                buf = bytearray()
                async for chunk in r.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return None
                return bytes(buf)
        except Exception:
            await asyncio.sleep(0.4 * (attempt + 1))
    return None


# --------------------------- image-URL extraction -----------------------------

_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp|avif|gif)(\?|#|$)", re.I)
_NUM_RE = re.compile(r"(\d+)")

_HI_ATTRS = (
    "data-hi", "data-hires", "data-hi-res",
    "data-full", "data-full-src", "data-original", "data-original-src",
    "data-zoom", "data-zoom-src", "data-large", "data-image", "data-src-large",
    "data-lazy-src", "data-src", "data-url", "data-page",
)
_SRCSET_ATTRS = ("data-srcset", "srcset")
_SRC_ATTRS = ("src",)

_THUMB_HINTS = re.compile(
    r"(thumb|thumbnail|small|preview|placeholder|cover|icon|sprite|logo|avatar|_s\.|_t\.|-s\.|-t\.|/t/|/s/|/cover/|=s\d+)",
    re.I,
)


def _srcset_largest(v: str) -> str | None:
    best_url, best_w = None, -1
    for part in v.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        if not bits:
            continue
        u = bits[0]
        w = -1
        if len(bits) > 1:
            m = re.match(r"(\d+)(w|x)", bits[1])
            if m:
                w = int(m.group(1))
        if w > best_w:
            best_w = w
            best_url = u
    return best_url


def _collect_candidates_from_img(img) -> list[str]:
    cands: list[str] = []
    for a in _HI_ATTRS:
        v = img.get(a)
        if v and not v.startswith("data:"):
            cands.append(v.strip())
    for a in _SRCSET_ATTRS:
        v = img.get(a)
        if v:
            u = _srcset_largest(v)
            if u and not u.startswith("data:"):
                cands.append(u.strip())
    for a in _SRC_ATTRS:
        v = img.get(a)
        if v and not v.startswith("data:"):
            cands.append(v.strip())
    return cands


def _walk_json_for_images(obj) -> list[str]:
    out = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            s = o.strip()
            if s.startswith(("http://", "https://", "//", "/")) and _IMG_EXT_RE.search(s):
                out.append(s)

    walk(obj)
    return out


def _extract_json_blobs(soup: BeautifulSoup) -> list[dict]:
    blobs: list[dict] = []
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            blobs.append(json.loads(tag.string))
        except Exception:
            pass
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            blobs.append(json.loads(tag.string))
        except Exception:
            pass
    inline_re = re.compile(
        r"(?:window\.)?(?:__(?:NUXT|DATA|INITIAL_STATE|APOLLO_STATE)__|pages|images|gallery)\s*=\s*(\[.+?\]|\{.+?\})\s*[;<]",
        re.S,
    )
    for tag in soup.find_all("script"):
        s = tag.string or ""
        if not s or len(s) > 2_000_000:
            continue
        for m in inline_re.finditer(s):
            raw = m.group(1)
            try:
                blobs.append(json.loads(raw))
            except Exception:
                try:
                    blobs.append(json.loads(raw.rstrip(",;")))
                except Exception:
                    continue
    return blobs


def _numeric_sort_key(u: str) -> tuple:
    path = urlparse(u).path
    nums = _NUM_RE.findall(path)
    return (int(nums[-1]) if nums else 0, u)


def _score(u: str) -> int:
    score = 0
    if _IMG_EXT_RE.search(u):
        score += 3
    if _THUMB_HINTS.search(u):
        score -= 5
    if re.search(r"(full|large|orig|hd|hires|1080|1200|1600|2000|/p/|/page/)", u, re.I):
        score += 4
    score += min(len(urlparse(u).path) // 20, 3)
    return score


# --------------------------- Adapters ----------------------------------------

class DirectPDFAdapter(BaseAdapter):
    name = "direct_pdf"

    def match(self, url):
        return True

    async def fetch_pdf(self, session, url):
        try:
            _, headers, body = await _get(session, url)
            ctype = (headers.get("Content-Type") or "").lower()
            if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
                if 0 < len(body) <= MAX_PDF_BYTES and body[:4] == b"%PDF":
                    return body
                return None
            soup = BeautifulSoup(body, "html.parser")
            for a in soup.find_all("a", href=True):
                if a["href"].lower().split("?")[0].endswith(".pdf"):
                    try:
                        _, _, data = await _get(session, urljoin(url, a["href"]))
                        if 0 < len(data) <= MAX_PDF_BYTES and data[:4] == b"%PDF":
                            return data
                    except Exception:
                        continue
        except Exception:
            return None
        return None


class HTMLImageAdapter(BaseAdapter):
    """Generic fallback: reader / gallery page → ordered list of hi-res image URLs."""
    name = "html_images"

    def match(self, url):
        return True

    async def fetch_image_urls(self, session, url):
        _, _, body = await _get(session, url)
        soup = BeautifulSoup(body, "html.parser")

        candidates: list[str] = []
        for img in soup.find_all("img"):
            candidates.extend(_collect_candidates_from_img(img))
        for ns in soup.find_all("noscript"):
            try:
                inner = BeautifulSoup(ns.decode_contents(), "html.parser")
            except Exception:
                continue
            for img in inner.find_all("img"):
                candidates.extend(_collect_candidates_from_img(img))
        for blob in _extract_json_blobs(soup):
            candidates.extend(_walk_json_for_images(blob))

        seen: set[str] = set()
        pool: list[str] = []
        for c in candidates:
            full = urljoin(url, c)
            if not _IMG_EXT_RE.search(full):
                continue
            if full in seen:
                continue
            seen.add(full)
            pool.append(full)

        if not pool:
            return []

        scored = [(u, _score(u)) for u in pool]
        scored.sort(key=lambda x: -x[1])
        top_score = scored[0][1]
        kept = [u for u, s in scored if s >= top_score - 4]
        if len(kept) < 2 and len(pool) >= 2:
            kept = pool
        kept.sort(key=_numeric_sort_key)
        return kept


# --------------------------- Bulk downloader ---------------------------------

async def download_images(session: ClientSession, urls: list[str], concurrency: int = 12,
                          on_progress=None, referer: str | None = None) -> list[bytes]:
    sem = asyncio.Semaphore(concurrency)
    results: list[bytes | None] = [None] * len(urls)

    async def one(i: int, u: str):
        async with sem:
            data = await _stream_image(session, u, referer=referer)
            results[i] = data
            if on_progress is not None:
                try:
                    r = on_progress(1)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass

    await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
    return [b for b in results if b]
