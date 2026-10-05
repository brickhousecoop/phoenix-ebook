from __future__ import annotations

import io

from PIL import Image

from conftest import encode, jpeg, png, SVG
from phoenix_ebook.images import optimize_image


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def noise(size=(2000, 1200)) -> Image.Image:
    return Image.effect_noise(size, 60).convert("RGB")


def test_wide_opaque_png_is_resized_and_converted_to_jpeg():
    out, ext, size = optimize_image(encode(noise(), "PNG"), ".png")
    assert ext == ".jpg" and size == (1100, 660)
    assert opened(out).format == "JPEG"


def test_narrow_image_is_not_enlarged():
    out, ext, size = optimize_image(encode(noise((600, 400)), "PNG"), ".png")
    assert size == (600, 400)


def test_max_width_zero_disables_resizing():
    _, ext, size = optimize_image(encode(noise(), "PNG"), ".png", max_width=0)
    assert size == (2000, 1200) and ext == ".jpg"


def test_custom_max_width():
    _, _, size = optimize_image(encode(noise(), "PNG"), ".png", max_width=600)
    assert size == (600, 360)


def test_real_transparency_stays_png():
    img = noise((800, 600)).convert("RGBA")
    for x in range(300, 500):
        for y in range(200, 400):
            img.putpixel((x, y), (0, 0, 0, 0))
    out, ext, _ = optimize_image(encode(img, "PNG"), ".png")
    assert ext == ".png" and opened(out).mode == "RGBA"


def test_rgba_that_is_fully_opaque_becomes_jpeg():
    _, ext, _ = optimize_image(encode(noise().convert("RGBA"), "PNG"), ".png")
    assert ext == ".jpg"


def test_transparent_borders_are_trimmed_then_converted():
    img = Image.new("RGBA", (1000, 400), (0, 0, 0, 0))
    img.paste(noise((300, 400)).convert("RGBA"), (350, 0))
    out, ext, size = optimize_image(encode(img, "PNG"), ".png")
    assert ext == ".jpg" and size == (300, 400)


def test_animated_gif_passes_through_unchanged():
    frames = [Image.new("RGB", (300, 200), c) for c in ("red", "green", "blue")]
    data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=200, loop=0)
    out, ext, size = optimize_image(data, ".gif")
    assert out == data and ext == ".gif" and size == (300, 200)


def test_svg_passes_through_unchanged():
    assert optimize_image(SVG, ".svg") == (SVG, ".svg", None)


def test_undecodable_bytes_pass_through():
    assert optimize_image(b"not an image", ".jpg") == (b"not an image", ".jpg", None)


def test_small_jpeg_is_kept_when_reencoding_would_not_shrink_it():
    data = encode(Image.effect_noise((600, 400), 20).convert("RGB"), "JPEG", quality=60)
    out, ext, _ = optimize_image(data, ".jpg")
    assert out == data and ext == ".jpg"


def test_tiny_static_gif_is_kept_as_is():
    data = encode(Image.new("P", (300, 200), 3), "GIF")
    out, ext, _ = optimize_image(data, ".gif")
    assert out == data and ext == ".gif"


def test_exif_rotation_is_applied():
    exif = Image.new("RGB", (1, 1)).getexif()
    exif[0x0112] = 6  # rotate 90° clockwise
    data = jpeg(size=(800, 400), exif=exif)
    out, _, size = optimize_image(data, ".jpg")
    assert size == (400, 800)
    assert opened(out).getexif().get(0x0112) in (None, 1)


def test_cmyk_jpeg_is_converted_to_rgb():
    data = encode(Image.new("CMYK", (1500, 900), (10, 20, 30, 0)), "JPEG")
    out, ext, size = optimize_image(data, ".jpg")
    assert ext == ".jpg" and size == (1100, 660) and opened(out).mode == "RGB"
