"""Compile downloaded images into a single PDF, memory-safe for Render's 512 MB.

v4 — fixes the "stuck at Compiling PDF" hang:
  OLD: opened ALL pages, converted to RGB, held every frame in RAM
       (~330 MB for 48 hi-res pages), then one slow Pillow save(PDF).
       On Render's 512 MB free tier that triggered the OOM killer ->
       process restarted mid-compile -> status message froze forever.
  NEW: decode + resize + JPEG-encode each page ONE AT A TIME, close the
       frame immediately (48 pages ~= 10 MB of JPEG bytes), then assemble
       with img2pdf, which streams the JPEGs into the PDF with no
       re-encoding and near-zero extra RAM. Typical 47-page compile:
       a few seconds instead of minutes.
"""
import io
import img2pdf
from PIL import Image, UnidentifiedImageError, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 60_000_000

MAX_SIDE = 1600          # cap longest side — plenty for reading on any screen
JPEG_QUALITY = 85


def _page_to_jpeg(raw: bytes, max_side: int, quality: int) -> bytes | None:
    """Decode one page, flatten to RGB, resize, return JPEG bytes. Frame closed."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    try:
        if im.mode == "P":
            im = im.convert("RGBA")
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")

        w, h = im.size
        m = max(w, h)
        if m > max_side:
            scale = max_side / m
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    finally:
        try:
            im.close()
        except Exception:
            pass


def images_to_pdf(pages: list[bytes], max_bytes: int = 48 * 1024 * 1024) -> bytes:
    # Pass 1: normal settings, one page at a time (low peak RAM)
    jpegs = [j for raw in pages if (j := _page_to_jpeg(raw, MAX_SIDE, JPEG_QUALITY))]
    if not jpegs:
        raise ValueError("no images could be decoded")

    data = img2pdf.convert(jpegs)

    # Pass 2 (rare): too big for Telegram — re-encode smaller
    if len(data) > max_bytes:
        jpegs = [j for raw in pages if (j := _page_to_jpeg(raw, 1200, 70))]
        if not jpegs:
            raise ValueError("no images could be decoded")
        data = img2pdf.convert(jpegs)

    if len(data) > max_bytes:
        raise ValueError(f"compiled PDF is {len(data) // 1048576} MB, over Telegram's 50 MB limit")
    return data
