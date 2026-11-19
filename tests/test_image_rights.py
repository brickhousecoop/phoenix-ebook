"""Images whose rights for ebooks aren't cleared are left out and reported (#51): Defector's Getty photos."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import phoenix_ebook.processors  # noqa: F401 — registers the built-ins
from conftest import ok, png, post
from phoenix_ebook.canonical import IMAGE_LEFT_OUT
from phoenix_ebook.errors import SiteFileError
from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import get_processor
from phoenix_ebook.processors.rules import leave_out_images, load_site_rules

GETTY = "https://lede-admin.defector.com/wp-content/uploads/sites/28/2026/10/GettyImages-2284399382.jpg"
OWN = "https://lede-admin.defector.com/wp-content/uploads/sites/28/2026/10/chart.png"
SETTINGS = 'leave_out_images = ["GettyImages-*"]\nleave_out_credits = ["Getty Images"]\n'


def rules_from(folder, text=SETTINGS):
    (folder / "blog.toml").write_text(text, encoding="utf-8")
    return load_site_rules("blog", folder)


def cleaned(html: str, processor: str = "defector") -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    get_processor(processor).clean(soup, Post(slug="p", title="t", html=html))
    return soup


def reasons(soup) -> list[str]:
    return [m[IMAGE_LEFT_OUT] for m in soup.find_all(attrs={IMAGE_LEFT_OUT: True})]


# ---------------------------------------------------------------- recognising a photo

@pytest.mark.parametrize("src", [
    GETTY,
    GETTY + "?w=1600",
    "https://img.example/a/gettyimages-12.JPG",
    "/uploads/GettyImages-1234-scaled.jpg#top",
    "https://img.example/a/GettyImages%2D55.jpg",
])
def test_file_name_matches(tmp_path, src):
    assert rules_from(tmp_path).image_left_out(src) == "its file name matches GettyImages-*"


@pytest.mark.parametrize("src", [
    OWN,
    "https://img.example/GettyImages-archive/chart.png",  # a folder, not the file's name
    "https://img.example/chart.png?from=GettyImages-12.jpg",  # the query is ignored
    "https://img.example/not-GettyImages-12.jpg",  # the pattern is the whole name
    "",
])
def test_file_name_does_not_match(tmp_path, src):
    assert rules_from(tmp_path).image_left_out(src) is None


@pytest.mark.parametrize("caption", [
    "Photo by Jane Doe/Getty Images",
    "Ezra Shaw/AFP via GETTY IMAGES",
    "Jane Doe/Getty Images",
    "Jane Doe/Getty\n  Images",
])
def test_caption_credit_matches_ignoring_capitals_and_spacing(tmp_path, caption):
    assert rules_from(tmp_path).image_left_out(OWN, caption) == "its caption credits Getty Images"


def test_other_credits_and_no_settings_match_nothing(tmp_path):
    assert rules_from(tmp_path).image_left_out(OWN, "Jane Doe/AP Photo") is None
    assert rules_from(tmp_path, "").image_left_out(GETTY, "Jane Doe/Getty Images") is None


# ---------------------------------------------------------------- what goes

def test_figure_goes_whole_caption_included():
    soup = cleaned(f'<p>Before.</p><figure><img src="{GETTY}" alt="A dunk"/><figcaption>A dunk. Jane Doe/Getty Images'
                   '</figcaption></figure><p>After.</p>')
    assert reasons(soup) == ["its file name matches GettyImages-*"]
    assert soup.find("figure") is None and soup.find("img") is None
    assert soup.get_text() == "Before.After."


def test_caption_alone_is_enough():
    soup = cleaned(f'<figure><img src="{OWN}"/><figcaption>Jane Doe/AFP via Getty Images</figcaption></figure><p>Text.</p>')
    assert reasons(soup) == ["its caption credits Getty Images"] and soup.find("figure") is None


def test_other_images_stay():
    html = f'<figure><img src="{OWN}"/><figcaption>Chart: Defector</figcaption></figure><p><img src="{OWN}"/></p>'
    soup = cleaned(html)
    assert reasons(soup) == [] and len(soup.find_all("img")) == 2 and soup.find("figcaption") is not None


def test_a_wrapper_left_empty_goes_too():
    soup = cleaned(f'<p><a href="https://example.com/big"><img src="{GETTY}"/></a></p>'
                   f'<p>Words <img src="{GETTY}"/> around.</p>')
    assert len(reasons(soup)) == 2 and soup.find("a") is None
    assert [p.get_text() for p in soup.find_all("p")] == ["Words  around."]


def test_one_photo_among_several_in_a_figure(tmp_path):
    soup = BeautifulSoup(f'<figure><img src="{GETTY}"/><img src="{OWN}"/><figcaption>Two views</figcaption></figure>',
                         "html.parser")
    leave_out_images(soup, rules_from(tmp_path))
    assert [img["src"] for img in soup.find_all("img")] == [OWN] and soup.find("figcaption") is not None


def test_a_link_card_keeps_its_words_and_loses_the_picture():
    card = (f'<figure class="card"><img src="{GETTY}" data-decorative=""/>'
            '<p><a href="https://defector.com/a-story">A Story</a></p></figure>')
    soup = cleaned(card)
    assert reasons(soup) == ["its file name matches GettyImages-*"]
    assert soup.find("img") is None and soup.find("figure", class_="card").get_text() == "A Story"


def test_ghost_blogs_are_unaffected():
    html = f'<figure><img src="{GETTY}"/><figcaption>Jane Doe/Getty Images</figcaption></figure>'
    for processor in ("generic", "flaminghydra", "hellworld"):
        soup = cleaned(html, processor)
        assert reasons(soup) == [] and soup.find("img") is not None, processor


# ---------------------------------------------------------------- in a book

def test_book_leaves_them_out_without_downloading_and_reports_each(build_book):
    body = (f'<p>Intro.</p><figure><img src="{GETTY.replace("2284399382", "111")}"/></figure>'
            f'<figure><img src="{OWN}"/><figcaption>Jane Doe/Getty Images</figcaption></figure>'
            '<figure><img src="https://img.example/kept.png"/><figcaption>Chart: Defector</figcaption></figure>')
    built = build_book(post(body, feature_image=GETTY + "?w=1600"), {"https://img.example/kept.png": [ok(png())]},
                       processor="defector")

    assert [url for url, _ in built.session.log] == ["https://img.example/kept.png"]
    assert len(built.image_names()) == 1
    chapter = built.chapter()
    assert "Getty" not in chapter and "data-image-left-out" not in chapter and chapter.count("<img") == 1
    assert "Intro." in chapter and "Chart: Defector" in chapter

    left_out = [p for p in built.result.problems if p.kind == "image-rights-not-cleared"]
    assert [(p.url.rsplit("/", 1)[-1], p.detail.split(": ", 1)[1], p.caption) for p in left_out] == [
        ("GettyImages-2284399382.jpg?w=1600", "its file name matches GettyImages-*", None),
        ("GettyImages-111.jpg", "its file name matches GettyImages-*", None),
        ("chart.png", "its caption credits Getty Images", "Jane Doe/Getty Images"),
    ]
    assert all(p.post_slug == "p" and "rights for ebooks aren't cleared" in p.detail for p in left_out)
    # only the image that's in the book is asked for alt text
    assert [p.url for p in built.result.problems if p.kind == "image-missing-alt"] == ["https://img.example/kept.png"]


def test_a_non_getty_lead_image_stays(build_book):
    built = build_book(post(feature_image=OWN), {OWN: [ok(png())]}, processor="defector")
    assert len(built.image_names()) == 1
    assert "image-rights-not-cleared" not in {p.kind for p in built.result.problems}


def test_cli_summary(capsys):
    import build_epub
    from phoenix_ebook.models import BuildProblem
    problem = BuildProblem("image-rights-not-cleared", "p", GETTY,
                           "photo left out, its rights for ebooks aren't cleared: its file name matches GettyImages-*")
    build_epub._report_problems([problem, problem])
    err = capsys.readouterr().err
    assert f"warning: [p] {GETTY}: photo left out" in err
    assert "2 photos were left out: their rights for ebooks aren't cleared; see warnings above" in err


def test_website_names_the_warning():
    from web.problems import LABELS
    assert LABELS["image-rights-not-cleared"] == "Photo left out: rights not cleared"


# ---------------------------------------------------------------- the settings

def test_defector_has_the_two_settings_and_no_other_blog_does():
    defector = load_site_rules("defector")
    assert [written for written, _ in defector.leave_out_images] == ["GettyImages-*"]
    assert defector.leave_out_credits == ("Getty Images",)
    for name in ("generic", "flaminghydra", "hellworld"):
        blog = load_site_rules(name)
        assert not blog.leave_out_images and not blog.leave_out_credits


@pytest.mark.parametrize("text, problem", [
    ('leave_out_images = ["*"]', "has no letters"),
    ('leave_out_images = ["-*.123"]', "has no letters"),
    ('leave_out_credits = ["/ 2026"]', "has no words"),
    ('leave_out_images = "GettyImages-*"', "must be a list"),
    ('leave_out_credits = [""]', "must be a list"),
])
def test_mistakes_in_the_settings_are_named(tmp_path, text, problem):
    with pytest.raises(SiteFileError) as caught:
        rules_from(tmp_path, text)
    assert problem in str(caught.value) and "blog.toml" in str(caught.value)
