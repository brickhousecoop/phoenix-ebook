from __future__ import annotations

import io

from PIL import Image, ImageOps


DEFAULT_MAX_WIDTH = 1100
DEFAULT_QUALITY = 85


def _has_transparency(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        return img.getchannel("A").getextrema()[0] < 255
    if img.mode == "P" and "transparency" in img.info:
        return _has_transparency(img.convert("RGBA"))
    return False


def optimize_image(
    data: bytes,
    ext: str,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    quality: int = DEFAULT_QUALITY,
) -> tuple[bytes, str, tuple[int, int] | None]:
    """Shrink and re-encode a downloaded image for an ebook.

    Returns (data, ext, (width, height) or None). Wider-than-max images are
    scaled down; everything is re-encoded as JPEG except images with real
    transparency (kept as PNG), animated GIFs and SVGs (passed through).
    Anything Pillow can't read is returned unchanged.
    """
    if ext == ".svg":
        return data, ext, None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return data, ext, None

    if getattr(img, "is_animated", False):
        return data, ext, img.size

    rotated = img.getexif().get(0x0112, 1) != 1  # EXIF Orientation tag
    img = ImageOps.exif_transpose(img)

    # Trim fully transparent borders (e.g. a book cover padded to a wide frame);
    # what remains is often opaque and can then become a JPEG.
    trimmed = False
    if img.mode in ("RGBA", "LA", "P") and _has_transparency(img):
        img = img.convert("RGBA")
        bbox = img.getchannel("A").getbbox()
        if bbox and bbox != (0, 0, img.width, img.height):
            img = img.crop(bbox)
            trimmed = True

    resized = bool(max_width) and img.width > max_width
    if resized:
        height = round(img.height * max_width / img.width)
        img = img.resize((max_width, height), Image.LANCZOS)

    buf = io.BytesIO()
    if _has_transparency(img):
        img.convert("RGBA").save(buf, format="PNG", optimize=True)
        out_ext = ".png"
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        out_ext = ".jpg"
    out = buf.getvalue()

    # If re-encoding doesn't shrink an image we didn't need to resize or rotate
    # (already-small JPEGs, compact line-art PNGs/GIFs), keep the original.
    if not (resized or rotated or trimmed) and len(out) >= len(data):
        return data, ext, img.size

    return out, out_ext, img.size
