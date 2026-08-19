"""Shared async HTTP helpers: streaming image download + parallel bulk fetch."""
from __future__ import annotations
import asyncio
from aiohttp import ClientSession, ClientResponseError

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
IMAGE_CTYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/avif", "image/gif")


async def _stream_image(session: ClientSession, url: str, referer: str | None = None,
                        max_bytes: int = 20 * 1024 * 1024, retries: int = 2) -> bytes | None:
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


async def download_images(session: ClientSession, urls: list[str], concurrency: int = 12,
                          on_progress=None, referer: str | None = None) -> list[bytes]:
    sem = asyncio.Semaphore(concurrency)
    results: list[bytes | None] = [None] * len(urls)

    async def one(i: int, u: str):
        async with sem:
            results[i] = await _stream_image(session, u, referer=referer)
            if on_progress is not None:
                try:
                    r = on_progress(1)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass

    await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
    return [b for b in results if b]
