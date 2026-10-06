"""Accessibility metadata, ARIA roles, package language (#8)."""
from __future__ import annotations

import io
import re

import pytest
from PIL import Image

from conftest import assert_valid_epub, ok, png, post

U = "https://img.test/"


def schema(book, prop: str) -> list[str]:
    return re.findall(rf'<meta property="schema:{prop}">([^<]*)</meta>', book.text("content.opf"))


def animated_gif() -> bytes:
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "green")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


BASE_FEATURES = ["tableOfContents", "readingOrder", "structuralNavigation", "displayTransformability"]


def test_book_without_images(build_book):
    book = build_book(post("<p>Text only.</p>"))
    assert schema(book, "accessMode") == ["textual"]
    assert schema(book, "accessModeSufficient") == ["textual"]
    assert schema(book, "accessibilityFeature") == BASE_FEATURES
    assert schema(book, "accessibilityHazard") == ["none"]
    [summary] = schema(book, "accessibilitySummary")
    assert "table of contents" in summary and "images" not in summary


def test_all_images_described_or_decorative(build_book):
    html = f'<img src="{U}a.png" alt="A red square."/><img src="{U}d.png"/>'
    book = build_book(post(html), {U + "a.png": [ok(png())], U + "d.png": [ok(png())]}, alt_text={U + "d.png": ""})
    assert schema(book, "accessMode") == ["textual", "visual"]
    assert schema(book, "accessModeSufficient") == ["textual", "textual,visual"]
    assert schema(book, "accessibilityFeature") == BASE_FEATURES + ["alternativeText"]
    [summary] = schema(book, "accessibilitySummary")
    assert "1 of 2 images have text descriptions; 1 is decorative." in summary


def test_image_missing_alt_means_no_alternative_text_claim(build_book):
    html = f'<img src="{U}a.png" alt="Described."/><img src="{U}b.png"/>'
    book = build_book(post(html), {U + "a.png": [ok(png())], U + "b.png": [ok(png())]})
    assert "alternativeText" not in schema(book, "accessibilityFeature")
    assert schema(book, "accessModeSufficient") == ["textual,visual"]
    assert "1 of 2 images have text descriptions." in schema(book, "accessibilitySummary")[0]


def test_animated_gif_means_unknown_flashing_hazard(build_book):
    book = build_book(post(f'<img src="{U}a.gif" alt="An animation."/>'), {U + "a.gif": [ok(animated_gif(), "image/gif")]})
    assert schema(book, "accessibilityHazard") == ["unknownFlashingHazard", "noMotionSimulationHazard", "noSoundHazard"]


def test_static_gif_is_no_hazard(build_book):
    still = io.BytesIO()
    Image.new("P", (40, 30), 3).save(still, format="GIF")
    book = build_book(post(f'<img src="{U}s.gif" alt="Still."/>'), {U + "s.gif": [ok(still.getvalue(), "image/gif")]})
    assert schema(book, "accessibilityHazard") == ["none"]


def test_summary_override(build_book):
    book = build_book(post(), accessibility_summary="Written by the publisher.")
    assert schema(book, "accessibilitySummary") == ["Written by the publisher."]


def test_no_conformance_claim(build_book):
    opf = build_book(post(f'<img src="{U}a.png" alt="x"/>'), {U + "a.png": [ok(png())]}).text("content.opf")
    assert "conformsTo" not in opf and "certifiedBy" not in opf


def test_package_language(build_book):
    assert re.search(r'<package [^>]*xml:lang="en-US"', build_book(post(), lang="en-US").text("content.opf"))


def test_aria_roles_match_epub_types(build_book, tmp_path):
    intro = tmp_path / "intro.txt"
    intro.write_text("Hello.")
    book = build_book([post(slug="one")], intro_file=str(intro))
    expected = {
        "one.xhtml": ("chapter", "doc-chapter"),
        "foreword.xhtml": ("foreword", "doc-foreword"),
        "intro.xhtml": ("introduction", "doc-introduction"),
        "acknowledgements.xhtml": ("acknowledgments", "doc-acknowledgments"),
    }
    for page, (epub_type, role) in expected.items():
        assert re.search(rf'<section [^>]*epub:type="{epub_type}" role="{role}"', book.text(page)), page
    assert 'role="doc-toc"' in book.text("nav.xhtml")
    for page in ("copyright.xhtml", "imprint.xhtml", "titlepage.xhtml"):  # no ARIA counterpart
        assert "role=" not in book.text(page), page


def test_manifest_accessibility_summary(tmp_path):
    import build_epub
    path = tmp_path / "b.toml"
    path.write_text('posts=["s"]\n[book]\ntitle="t"\naccessibility_summary="Custom."\n'
                    '[source]\nplatform="ghost"\nurl="https://flaminghydra.ghost.io"\n')
    assert build_epub._spec_from_manifest(str(path)).accessibility_summary == "Custom."


def test_book_with_metadata_and_roles_is_valid(build_book):
    html = f'<img src="{U}a.gif" alt="Anim."/><img src="{U}b.png"/>'
    book = build_book(post(html), {U + "a.gif": [ok(animated_gif(), "image/gif")], U + "b.png": [ok(png())]})
    assert_valid_epub(book.path)
