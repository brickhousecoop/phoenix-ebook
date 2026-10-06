"""Fetching, validating and optimizing images embedded in chapters."""
from __future__ import annotations

import io
import time
import xml.etree.ElementTree as ET

import requests
from PIL import Image, ImageOps

from phoenix_ebook.errors import explain_response


DEFAULT_MAX_WIDTH = 1100
DEFAULT_QUALITY = 85

FETCH_TIMEOUT = 15
RETRY_DELAY = 1.0


class ImageFetchError(Exception):
    """An image URL didn't yield a usable image. The message is the reason."""


def _is_svg(data: bytes) -> bool:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    return root.tag == "svg" or root.tag.endswith("}svg")


_EXT_FOR_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}


def validate_image(data: bytes, ext: str) -> str:
    """Return the extension to store the image under, or raise ImageFetchError.

    Judged by the bytes, not the server's Content-Type: rasters must decode
    with Pillow, SVGs must be XML with an <svg> root.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            # The bytes decide, not the URL: servers often send PNG or WebP from a ".jpg" URL.
            return _EXT_FOR_FORMAT.get(img.format, ext)
    except Exception:
        pass
    if _is_svg(data):
        return ".svg"
    raise ImageFetchError("response is not a valid image")


def fetch_image(
    session: requests.Session,
    url: str,
    *,
    timeout: float = FETCH_TIMEOUT,
    retry_delay: float = RETRY_DELAY,
) -> tuple[bytes, str]:
    """Download an image, retrying once on transient failures.

    Returns (body, Content-Type). Transient: connection errors, timeouts,
    HTTP 5xx and 429; other HTTP errors fail immediately. Raises
    ImageFetchError with a short reason.
    """
    for attempt in (1, 2):
        try:
            resp = session.get(url, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            transient = True
        except requests.RequestException as exc:
            raise ImageFetchError(f"{type(exc).__name__}: {exc}") from exc
        else:
            if 200 <= resp.status_code < 300:
                content_type = resp.headers.get("Content-Type", "unknown")
                return resp.content, content_type
            why = explain_response(resp)
            reason = f"HTTP {resp.status_code}" + (f": {why}" if why else "")
            transient = resp.status_code >= 500 or resp.status_code == 429
        if not transient or attempt == 2:
            raise ImageFetchError(reason)
        time.sleep(retry_delay)


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
