"""Compile downloaded images into a single PDF, memory-safe for Render's 512 MB."""
import io
from PIL import Image, UnidentifiedImageError, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 60_000_000

MAX_SIDE = 2200


def _open_rgb(raw: bytes) -> Image.Image | None:
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if im.mode == "P":
        im = im.convert("RGBA")
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")

    w, h = im.size
    m = max(w, h)
    if m > MAX_SIDE:
        scale = MAX_SIDE / m
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return im


def images_to_pdf(pages: list[bytes], max_bytes: int = 48 * 1024 * 1024) -> bytes:
    frames: list[Image.Image] = []
    for raw in pages:
        im = _open_rgb(raw)
        if im is not None:
            frames.append(im)
    if not frames:
        raise ValueError("no images could be decoded")

    buf = io.BytesIO()
    frames[0].save(buf, format="PDF", save_all=True,
                   append_images=frames[1:], resolution=100.0)
    data = buf.getvalue()

    if len(data) > max_bytes:
        smaller_side = 1600
        rescaled: list[Image.Image] = []
        for f in frames:
            w, h = f.size
            m = max(w, h)
            if m > smaller_side:
                scale = smaller_side / m
                rescaled.append(f.resize((int(w * scale), int(h * scale)), Image.LANCZOS))
            else:
                rescaled.append(f)
        buf = io.BytesIO()
        rescaled[0].save(buf, format="PDF", save_all=True,
                         append_images=rescaled[1:], resolution=90.0)
        data = buf.getvalue()
        for f in rescaled:
            f.close()

    for f in frames:
        try:
            f.close()
        except Exception:
            pass

    if len(data) > max_bytes:
        raise ValueError(f"compiled PDF is {len(data) // 1048576} MB, over Telegram's 50 MB limit")
    return data
