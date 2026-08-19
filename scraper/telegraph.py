"""Fetch a Telegra.ph Instant View article and extract its image URLs in order.

Telegra.ph has no anti-bot protection: plain GET with a browser UA works.
Article body is inside <article>; page images are <img src="/file/….jpg|png">.
"""
from __future__ import annotations
from urllib.parse import urljoin
from aiohttp import ClientSession
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


async def fetch_telegraph_image_urls(session: ClientSession, tg_url: str) -> list[str]:
    async with session.get(tg_url, headers=HEADERS) as r:
        r.raise_for_status()
        html = await r.text()
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article") or soup

    urls: list[str] = []
    seen: set[str] = set()
    for img in article.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        full = urljoin(tg_url, src)
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)
    return urls
