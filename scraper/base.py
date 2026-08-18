"""Adapter interface. Add one adapter per site you have rights to download from."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from aiohttp import ClientSession

MAX_PDF_BYTES = 48 * 1024 * 1024  # stay under Telegram Bot API 50 MB upload cap

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf,image/webp,*/*;q=0.8",
}

@dataclass
class Result:
    pdf: Optional[bytes] = None
    filename: str = "document.pdf"
    error: Optional[str] = None

class BaseAdapter:
    name = "base"
    def match(self, url: str) -> bool: return False
    async def fetch_pdf(self, session: ClientSession, url: str) -> Optional[bytes]:
        """Return PDF bytes if a direct PDF exists, else None to trigger fallback."""
        return None
    async def fetch_image_urls(self, session: ClientSession, url: str) -> list[str]:
        """Fallback: ordered list of full-resolution image URLs (handles lazy-load)."""
        return []
