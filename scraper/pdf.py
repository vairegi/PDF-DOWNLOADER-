"""Compile downloaded images into a single PDF, memory-safe for Render's 512 MB."""
import io
from PIL import Image

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard

def images_to_pdf(pages: list[bytes], max_bytes: int = 48 * 1024 * 1024) -> bytes:
    frames = []
    for raw in pages:
        im = Image.open(io.BytesIO(raw))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        elif im.mode == "L":
            im = im.convert("RGB")
        frames.append(im)
    if not frames:
        raise ValueError("no images")
    buf = io.BytesIO()
    frames[0].save(buf, format="PDF", save_all=True,
                   append_images=frames[1:], resolution=100.0)
    for f in frames:
        f.close()
    data = buf.getvalue()
    if len(data) > max_bytes:
        raise ValueError(f"compiled PDF is {len(data)//1048576} MB, over limit")
    return data
