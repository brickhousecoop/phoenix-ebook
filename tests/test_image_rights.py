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


# ---------------------------------------------------------------- credits from the blog's media library

import json  # noqa: E402
import re  # noqa: E402

import requests  # noqa: E402

from conftest import FakeResponse, FakeSession  # noqa: E402
from phoenix_ebook.errors import SourceUnreachable  # noqa: E402
from phoenix_ebook.models import ImageDetails, PostImages  # noqa: E402
from phoenix_ebook.platforms.wordpress_feed import WordPressFeedPlatform  # noqa: E402

API = "https://lede-admin.defector.com/graphql"
LEAD = "https://img.example/photo-collage.png.jpeg?w=1600"
BODY = "https://img.example/track.jpeg"


def graphql(posts: dict | None = None, media: dict | None = None, *, without: tuple[str, ...] = ()):
    """An API that knows these posts' lead images (by slug) and media items (by id); others are null."""
    def answer(request):
        query = request["query"]
        for field in without:
            if field in query:
                return FakeResponse(200, json.dumps({"errors": [{"message": f'Cannot query field "{field}" on type '
                                                                            '"MediaItem".'}]}).encode())
        data = {}
        for alias, slug in re.findall(r'(p\d+): post\(id: "([^"]+)"', query):
            data[alias] = {"featuredImage": {"node": posts[slug]}} if posts and slug in posts else None
        for alias, media_id in re.findall(r"(m\d+): mediaItem\(id: (\d+)", query):
            data[alias] = (media or {}).get(media_id)
        return FakeResponse(200, json.dumps({"data": data}).encode())
    return answer


def details_book(build_book, body, posts=None, media=None, *, lead=LEAD, images=(), **kwargs):
    routes = {API: [graphql(posts, media, **kwargs)], **{url: [ok(png())] for url in images}}
    return build_book(post(body, feature_image=lead), routes, processor="defector", platform=WordPressFeedPlatform())


def left_out(built) -> list[tuple[str, str]]:
    return [(p.url.rsplit("/", 1)[-1], p.detail.split(": ", 1)[1]) for p in built.result.problems
            if p.kind == "image-rights-not-cleared"]


def test_wordpress_images_keep_their_media_id():
    html = WordPressFeedPlatform().normalize_html('<figure class="wp-block-image"><img src="a.png" '
                                                  'class="wp-image-77 size-large"/></figure>')
    assert html == '<figure><img data-media-id="77" src="a.png"/></figure>'


def test_credit_and_alt_text_are_shown_on_a_lead_image(build_book):
    built = details_book(build_book, "<p>Body.</p>", images=[LEAD],
                         posts={"p": {"altText": "A collage", "caption": None, "credit": "Art by Chris Thompson"}})
    chapter = built.chapter()
    assert 'alt="A collage"' in chapter
    assert '<figcaption><span class="credit">Art by Chris Thompson</span></figcaption>' in chapter
    assert not [p for p in built.result.problems if p.kind in ("image-missing-alt", "image-rights-not-cleared")]
    assert "data-media-id" not in chapter and "data-image-" not in chapter


def test_a_lead_image_credited_to_getty_is_left_out_whatever_its_file_name(build_book):
    built = details_book(build_book, "<p>Body.</p>", posts={"p": {
        "altText": "A collage", "caption": None, "credit": "Jonathan Kozub/NHLI and Dimitar Dilkoff/AFP via Getty Images"}})
    assert left_out(built) == [("photo-collage.png.jpeg?w=1600", "its caption credits Getty Images")]
    assert built.image_names() == [] and "<figure" not in built.chapter()
    assert [url for url, _ in built.session.log] == [API]  # asked once, and the photo never downloaded


def test_a_lead_image_gets_its_library_caption_then_its_credit(build_book):
    built = details_book(build_book, "<p>Body.</p>", images=[LEAD],
                         posts={"p": {"altText": "", "caption": "<p>That&#8217;s the lake creature.</p>\n",
                                      "credit": "Screenshot: TMJ4 &amp; friends"}})
    assert ('<figcaption>That’s the lake creature. <span class="credit">(Screenshot: TMJ4 &amp; friends)</span>'
            "</figcaption>") in built.chapter()


def test_images_in_a_post_get_credits_by_media_id(build_book):
    body = (f'<figure><img src="{BODY}" data-media-id="7" alt=""/></figure>'
            '<figure><img src="https://img.example/b.png" data-media-id="8" alt="Own words"/>'
            "<figcaption>Photo: Jane Doe/AP</figcaption></figure>"
            '<figure><img src="https://img.example/c.png" data-media-id="9"/><figcaption>A grid.</figcaption></figure>'
            '<figure><img src="https://img.example/d.png" data-media-id="10"/></figure>')
    media = {"7": {"altText": "A bike", "caption": "", "credit": "Loic Venance/AFP via Getty Images"},
             "8": {"altText": "Library words", "caption": "<p>ignored</p>", "credit": "jane doe/ap"},
             "9": {"altText": "", "caption": "", "credit": "Illustration: Jim Cooke"},
             "10": {"altText": "", "caption": "", "credit": ""}}
    built = details_book(build_book, body, media=media, lead=None,
                         images=["https://img.example/b.png", "https://img.example/c.png", "https://img.example/d.png"])
    chapter = built.chapter()
    assert left_out(built) == [("track.jpeg", "its caption credits Getty Images")]
    assert 'alt="Own words"' in chapter and "<figcaption>Photo: Jane Doe/AP</figcaption>" in chapter  # already credited
    assert '<figcaption>A grid. <span class="credit">(Illustration: Jim Cooke)</span></figcaption>' in chapter
    assert chapter.count("<figcaption") == 2 and "ignored" not in chapter  # no credit on file: kept, no caption
    assert len(built.image_names()) == 3


def test_an_image_not_in_the_library_is_kept_without_a_warning(build_book):
    hotlinked = "https://pbs.twimg.example/x.jpg"
    built = details_book(build_book, f'<figure><img src="{hotlinked}" alt="A tweet"/></figure>', lead=None, images=[hotlinked])
    assert len(built.image_names()) == 1 and left_out(built) == []
    assert built.session.log == [(hotlinked, built.session.log[0][1])]  # nothing to ask the API


def test_an_image_the_api_has_no_answer_for_is_left_out_with_its_own_warning(build_book):
    body = (f'<figure><img src="{BODY}" data-media-id="7"/></figure>'
            '<figure><img src="https://img.example/b.png" data-media-id="8"/></figure>')
    built = details_book(build_book, body, posts={}, media={"8": {"altText": "", "caption": "", "credit": ""}},
                         images=["https://img.example/b.png"])
    assert [(name, why.split(" (")[0]) for name, why in left_out(built)] == [
        ("photo-collage.png.jpeg?w=1600", "its credit couldn't be checked"), ("track.jpeg", "its credit couldn't be checked")]
    assert len(built.image_names()) == 1


@pytest.mark.parametrize("outcome, why", [
    (requests.ConnectionError("https://lede-admin.defector.com/graphql refused"), "ConnectionError"),
    (requests.Timeout(), "Timeout"),
    (FakeResponse(503, b"Service Unavailable"), "HTTP 503"),
    (FakeResponse(200, b"<html>maintenance</html>"), "didn't answer with image details"),
    (FakeResponse(200, b'{"errors": [{"message": "Internal server error"}]}'), "didn't answer with image details"),
])
def test_an_outage_stops_the_build(build_book, tmp_path, outcome, why):
    with pytest.raises(SourceUnreachable) as caught:
        build_book(post(feature_image=LEAD), {API: [outcome]}, processor="defector", platform=WordPressFeedPlatform())
    message = str(caught.value)
    assert why in message and "lede-admin.defector.com" in message and "/graphql" not in message
    assert "no book was built. Try again in a few minutes." in message
    assert not list(tmp_path.glob("*.epub"))


def test_an_api_without_credits_still_gives_alt_text():
    session = FakeSession({API: [graphql({"p": {"altText": "A collage", "caption": None}}, without=("credit",))]})
    found = WordPressFeedPlatform().image_details(session, API, [post(feature_image=LEAD), post(slug="q", feature_image=LEAD)])
    assert found["p"] == PostImages(lead=ImageDetails(alt="A collage")) and found["q"] == PostImages()
    assert len(session.log) == 2  # asked again without credit, once


def test_posts_are_asked_about_ten_at_a_time():
    posts = [post(f'<img src="a.png" data-media-id="{n}"/>', slug=f"s{n}") for n in range(23)]
    session = FakeSession({API: [graphql(media={str(n): {"credit": f"C{n}"} for n in range(23)})]})
    found = WordPressFeedPlatform().image_details(session, API, posts)
    assert len(session.log) == 3 and len(found) == 23
    assert found["s22"].images == {"22": ImageDetails(credit="C22")} and found["s22"].lead is None


def test_a_blog_without_the_setting_is_never_asked(build_book):
    body = '<figure><img src="https://img.example/b.png" data-media-id="8"/></figure>'
    built = build_book(post(body), {"https://img.example/b.png": [ok(png())]}, platform=WordPressFeedPlatform())
    assert [url for url, _ in built.session.log] == ["https://img.example/b.png"]
    assert "data-media-id" not in built.chapter()


def test_only_defector_has_the_setting():
    assert load_site_rules("defector").image_details == API
    assert all(load_site_rules(name).image_details is None for name in ("generic", "flaminghydra", "hellworld"))


@pytest.mark.parametrize("value", ['"lede-admin.defector.com/graphql"', '"http://example.com/graphql"', '""', "true"])
def test_the_setting_must_be_an_https_address(tmp_path, value):
    with pytest.raises(SiteFileError, match="image_details must be the https:// address"):
        rules_from(tmp_path, f"image_details = {value}")
