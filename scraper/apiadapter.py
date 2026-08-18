"""Generic 'replicate the JS button's XHR call' adapter + Next.js data sniffing.

THE GENERAL SKILL, PRODUCTIZED
------------------------------
When a site has a JS-driven "Download" button, reverse-engineer it like this:

  1. Open the page in Chrome/Firefox, press F12 -> Network tab.
  2. Filter by "Fetch/XHR" (hides images/CSS noise).
  3. Click the "Download ALL" button and watch what fires.
  4. Click the new request, inspect:
       - Request URL      -> this is the endpoint you replicate
       - Method (GET/POST)
       - Request headers  -> note any Authorization / X-* tokens
       - Payload (POSTs)  -> the JSON body shape
       - Response         -> is it the PDF itself, or JSON containing a URL?
  5. If the URL contains the gallery/item id (e.g. /api/download/12345),
     write the template with {id}:  /api/download/{id}
  6. Many Next.js sites skip step 3-5 entirely: the page HTML already embeds
     a <script id="__NEXT_DATA__"> JSON blob containing every prop the page
     was rendered with -- including download URLs and full image lists.
     sniff_next_data() below digs those out automatically.

Configure via env vars (no code changes needed on Render):

  API_URL_TEMPLATE   e.g. https://example.com/api/download/{id}
  API_METHOD         GET (default) or POST
  API_JSON_TEMPLATE  POST body template, e.g. {"gallery_id": "{id}"}
  API_EXTRA_HEADERS  JSON object merged into request headers (tokens etc.)
  API_RESPONSE_MODE  "direct" (response IS the pdf) or
                     "jsonurl" (JSON response, find a .pdf URL inside)
  ID_REGEX           optional custom regex to pull {id} from the user's URL;
                     default grabs the last run of digits in the path.
"""
from __future__ import annotations
import json, os, re
from urllib.parse import urljoin
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from .base import BaseAdapter, HEADERS, MAX_PDF_BYTES
from .generic import _get

DEFAULT_ID_RE = r"(\d+)(?!.*\d)"  # last digit-run in the path


def extract_id(url: str) -> str | None:
    pat = os.environ.get("ID_REGEX") or DEFAULT_ID_RE
    path = url.split("?")[0].split("#")[0]
    m = re.search(pat, path)
    return m.group(1) if m else None


def sniff_next_data(html: bytes | str) -> dict:
    """Return parsed __NEXT_DATA__ JSON, or {} if absent."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return {}
    try:
        return json.loads(tag.string)
    except Exception:
        return {}


def find_urls_in_json(obj, suffixes=(".pdf",)) -> list[str]:
    """Recursively collect string values that look like file URLs."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            low = o.lower().split("?")[0]
            if any(low.endswith(s) for s in suffixes):
                found.append(o)

    walk(obj)
    return found


def find_image_urls_in_json(obj) -> list[str]:
    return find_urls_in_json(obj, (".jpg", ".jpeg", ".png", ".webp", ".avif"))


class APIReplicateAdapter(BaseAdapter):
    """Replays the exact XHR the site's download button fires."""
    name = "api_replicate"

    def __init__(self):
        self.template = os.environ.get("API_URL_TEMPLATE", "")
        self.method = os.environ.get("API_METHOD", "GET").upper()
        self.json_template = os.environ.get("API_JSON_TEMPLATE", "")
        self.response_mode = os.environ.get("API_RESPONSE_MODE", "direct").lower()
        try:
            self.extra_headers = json.loads(os.environ.get("API_EXTRA_HEADERS", "{}"))
        except Exception:
            self.extra_headers = {}

    def match(self, url: str) -> bool:
        return bool(self.template) and extract_id(url) is not None

    async def fetch_pdf(self, session: ClientSession, url: str):
        if not self.match(url):
            return None
        item_id = extract_id(url)
        api_url = self.template.replace("{id}", item_id)
        headers = {**HEADERS, **self.extra_headers, "Referer": url}
        kwargs = {"headers": headers, "timeout": 60}
        if self.method == "POST":
            kwargs["json"] = json.loads(self.json_template.replace("{id}", item_id)) \
                if self.json_template else {"id": item_id}
        try:
            if self.method == "POST":
                async with session.post(api_url, **kwargs) as r:
                    r.raise_for_status()
                    body = await r.read()
                    ctype = r.headers.get("Content-Type", "")
            else:
                async with session.get(api_url, **kwargs) as r:
                    r.raise_for_status()
                    body = await r.read()
                    ctype = r.headers.get("Content-Type", "")
        except Exception:
            return None

        if self.response_mode == "direct" or body[:4] == b"%PDF" or "pdf" in ctype:
            return body if 0 < len(body) <= MAX_PDF_BYTES and body[:4] == b"%PDF" else None

        # jsonurl mode: response is JSON containing a pdf link
        try:
            data = json.loads(body)
        except Exception:
            return None
        for cand in find_urls_in_json(data):
            pdf_url = urljoin(api_url, cand)
            try:
                _, _, pdf = await _get(session, pdf_url)
                if pdf[:4] == b"%PDF" and 0 < len(pdf) <= MAX_PDF_BYTES:
                    return pdf
            except Exception:
                continue
        return None
