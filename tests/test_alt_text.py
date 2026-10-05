"""Alt text: missing/suspicious reporting, overrides, decorative images (#10)."""
from __future__ import annotations

import pytest

import build_epub
from conftest import assert_valid_epub, ok, png, post
from phoenix_ebook.alt_text import normalize_image_url, suspicious_alt
from phoenix_ebook.models import BuildProblem

U = "https://img.test/"


def alt_problems(book, kind=None):
    kinds = {kind} if kind else {"image-missing-alt", "image-suspicious-alt", "alt-override-unused"}
    return [p for p in book.result.problems if p.kind in kinds]


def routes(*names):
    return {U + n: [ok(png())] for n in names}


# ---------------------------------------------------------------- reporting

def test_missing_and_empty_alt_are_reported_with_location_and_caption(build_book):
    html = (f'<figure><img src="{U}a.png"/><figcaption>Klee, 1920</figcaption></figure>'
            f'<img src="{U}b.png" alt="  "/><img src="{U}c.png" alt="A red square."/>')
    book = build_book(post(html, slug="s"), routes("a.png", "b.png", "c.png"))
    missing = alt_problems(book, "image-missing-alt")
    assert [(p.post_slug, p.url, p.location, p.caption) for p in missing] == [
        ("s", U + "a.png", "image 1 of 3", "Klee, 1920"),
        ("s", U + "b.png", "image 2 of 3", None),
    ]
    assert 'alt=""' in book.chapter("s")  # missing alt becomes an explicit empty alt


def test_feature_image_counts_first_and_is_checked(build_book):
    book = build_book(post(f'<img src="{U}b.png" alt="Body."/>', feature_image=U + "f.png"), routes("f.png", "b.png"))
    [problem] = alt_problems(book)
    assert (problem.url, problem.location) == (U + "f.png", "image 1 of 2")


@pytest.mark.parametrize("alt", ["IMG_4521.jpg", "DSC0012", "PXL_20260803", "photo.png", "Screenshot 2026-08-03 at 10.15",
                                 "image", "Photo", " screenshot "])
def test_suspicious_alt_detected(alt):
    assert suspicious_alt(alt, None)


@pytest.mark.parametrize("alt", ["A photo of an old screenshot on a monitor.", "Image of the Brooklyn Bridge at dusk",
                                 "Walter Benjamin in 1928", "png"])
def test_ordinary_alt_not_flagged(alt):
    assert suspicious_alt(alt, None) is None


def test_alt_equal_to_caption_is_suspicious():
    assert "caption" in suspicious_alt("Paul  Klee, Angelus Novus", "paul klee, angelus novus")


def test_suspicious_alt_reported_in_book(build_book):
    html = f'<figure><img src="{U}a.png" alt="IMG_1.jpg"/><figcaption>c</figcaption></figure>'
    [problem] = alt_problems(build_book(post(html), routes("a.png")))
    assert problem.kind == "image-suspicious-alt" and "file name" in problem.detail and problem.caption == "c"


def test_failed_images_are_not_alt_checked(build_book):
    from conftest import FakeResponse
    book = build_book(post(f'<img src="{U}gone.png"/>'), {U + "gone.png": [FakeResponse(404)]})
    assert not alt_problems(book)


# ---------------------------------------------------------------- overrides

def test_override_fills_missing_and_replaces_existing_and_is_escaped(build_book):
    html = f'<img src="{U}a.png"/><img src="{U}b.png" alt="IMG_9.jpg"/>'
    book = build_book(post(html), routes("a.png", "b.png"),
                      alt_text={U + "a.png": 'Cats & "dogs" <3', U + "b.png": "A dog asleep."})
    chapter = book.chapter()
    assert 'alt="Cats &amp; &quot;dogs&quot; &lt;3"' in chapter and 'alt="A dog asleep."' in chapter
    assert not alt_problems(book)


def test_empty_override_marks_decorative(build_book):
    book = build_book(post(f'<img src="{U}divider.png"/>'), routes("divider.png"), alt_text={U + "divider.png": ""})
    assert 'alt=""' in book.chapter() and not alt_problems(book)


def test_url_matching_ignores_size_variants_query_and_relative_src(build_book):
    site = "https://site.test"
    full = f"{site}/content/images/2026/08/a.png"
    html = ('<img src="/content/images/size/w1000/2026/08/a.png?v=2"/>'
            f'<img src="{site}/content/images/size/w600h400/2026/08/a.png"/>')
    book = build_book(post(html), {f"{site}/content/images/size/w1000/2026/08/a.png?v=2": [ok(png())],
                                   f"{site}/content/images/size/w600h400/2026/08/a.png": [ok(png())]},
                      image_base_url=site, alt_text={full: "One entry, every variant."})
    assert book.chapter().count('alt="One entry, every variant."') == 2 and not alt_problems(book)


def test_normalize_image_url():
    assert normalize_image_url("https://x/content/images/size/w1000/2026/a.png?v=2#f") == "https://x/content/images/2026/a.png"
    assert normalize_image_url("https://x/a.png") == "https://x/a.png"


def test_feature_image_override(build_book):
    book = build_book(post(feature_image=U + "f.png"), routes("f.png"), alt_text={U + "f.png": "A comic hero."})
    assert 'alt="A comic hero."' in book.chapter() and not alt_problems(book)


def test_unused_override_is_reported(build_book):
    book = build_book(post(f'<img src="{U}a.png" alt="Fine."/>'), routes("a.png"),
                      alt_text={U + "typo.png": "Lost description."})
    [problem] = alt_problems(book)
    assert (problem.kind, problem.url, problem.post_slug) == ("alt-override-unused", U + "typo.png", None)


# ---------------------------------------------------------------- CLI output

def test_cli_report_has_summary_and_commented_toml(capsys):
    build_epub._report_problems([
        BuildProblem("image-missing-alt", "p", U + "a.png", "no alt text", "image 1 of 2", "Klee"),
        BuildProblem("image-missing-alt", "q", U + "a.png", "no alt text", "image 3 of 3", None),
        BuildProblem("image-suspicious-alt", "p", U + "b.png", "alt text is only a generic word: 'image'", "image 2 of 2"),
        BuildProblem("alt-override-unused", None, U + "typo.png", "no image in the book has this URL"),
    ])
    err = capsys.readouterr().err
    assert 'warning: [p] image 1 of 2: https://img.test/a.png: no alt text (caption: "Klee")' in err
    assert "warning: https://img.test/typo.png: no image in the book has this URL" in err
    assert "2 images have no alt text" in err and "1 image has alt text that looks unhelpful" in err
    assert "1 [alt_text] entry matches no image in the book" in err
    toml_lines = [line for line in err.splitlines() if line.startswith('# "')]
    assert toml_lines == ['# "https://img.test/a.png" = ""   # p, image 1 of 2: "Klee"',
                          '# "https://img.test/b.png" = ""   # p, image 2 of 2']
    assert all(line.startswith("#") for line in err.split("# [alt_text]")[1].strip().splitlines())


def test_manifest_alt_text_table(tmp_path):
    path = tmp_path / "b.toml"
    path.write_text('posts=["s"]\n[alt_text]\n"https://x/a.png" = "Alt."\n"https://x/d.png" = ""\n'
                    '[book]\ntitle="t"\n[source]\nplatform="ghost"\nurl="https://flaminghydra.ghost.io"\n')
    spec = build_epub._spec_from_manifest(str(path))
    assert spec.alt_text == {"https://x/a.png": "Alt.", "https://x/d.png": ""}


def test_book_with_overrides_and_decorative_images_is_valid(build_book):
    html = f'<figure><img src="{U}a.png"/><figcaption>c</figcaption></figure><img src="{U}d.png"/>'
    book = build_book(post(html), routes("a.png", "d.png"), alt_text={U + "a.png": "A & B", U + "d.png": ""})
    assert_valid_epub(book.path)
