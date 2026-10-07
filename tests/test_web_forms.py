"""Form -> BookSpec mapping and the pre-build checks (#23)."""
from __future__ import annotations

import json

import pytest

from conftest import FakeResponse, FakeSession, png
from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.secrets import env_var_name
from web import forms
from web.forms import MAX_COVER_BYTES, MAX_POSTS, RawForm, check_submission, parse_post_addresses

KEY_ENV = env_var_name("ghost", "flaminghydra.ghost.io")


# ---------------------------------------------------------------- address parsing

def test_parse_post_addresses_strips_blank_lines_and_whitespace():
    assert parse_post_addresses("  a  \n\n b\n   \nc") == [(1, "a"), (3, "b"), (5, "c")]


@pytest.mark.parametrize("address", [
    "my-post",
    "https://flaminghydra.com/p/my-post",
    "https://flaminghydra.ghost.io/my-post/",
    "flaminghydra.ghost.io/my-post",
])
def test_post_list_accepts_slugs_and_known_addresses(address):
    slugs, errors = forms._check_post_list(address)
    assert slugs == ["my-post"]
    assert errors == []


def test_post_list_rejects_other_sites():
    slugs, errors = forms._check_post_list("good\nhttps://example.com/other\n")
    assert slugs == ["good"]
    assert len(errors) == 1
    assert errors[0].field == "posts"
    assert "line 2" in errors[0].message


def test_post_list_reports_duplicates_with_line_numbers():
    slugs, errors = forms._check_post_list("a\nb\na\n")
    assert slugs == ["a", "b"]
    assert any("line 3 repeats line 1" in e.message for e in errors)


def test_post_list_rejects_more_than_max_posts():
    text = "\n".join(f"post-{i}" for i in range(MAX_POSTS + 1))
    slugs, errors = forms._check_post_list(text)
    assert len(slugs) == MAX_POSTS + 1
    assert any(str(MAX_POSTS) in e.message for e in errors)


def test_post_list_requires_at_least_one_post():
    slugs, errors = forms._check_post_list("   \n\n")
    assert slugs == []
    assert errors == [forms.FormError("posts", "add at least one post address")]


# ---------------------------------------------------------------- cover checks

def test_cover_too_large_is_rejected():
    errors = forms._check_cover_bytes(b"x" * (MAX_COVER_BYTES + 1))
    assert errors and errors[0].field == "cover"


def test_cover_not_an_image_is_rejected():
    errors = forms._check_cover_bytes(b"not an image")
    assert errors and "image" in errors[0].message


def test_cover_valid_image_passes():
    assert forms._check_cover_bytes(png()) == []


# ---------------------------------------------------------------- key=value parsing

def test_parse_kv_lines():
    assert forms._parse_kv_lines('Name = Last, First\nhttps://x/img.png = "Alt text"\n') == {
        "Name": "Last, First",
        "https://x/img.png": '"Alt text"',
    }


# ---------------------------------------------------------------- BookSpec mapping

def test_build_spec_maps_simple_and_advanced_fields():
    raw = RawForm(posts="p", title="My Book", subtitle="Sub", editor="Ed",
                  series="S", series_number="2", publisher="Pub", description="Desc",
                  pub_date="2026-01-01", lang="fr", isbn="978-3-16-148410-0", rights="CC0",
                  image_max_width="500", image_quality="70", keep_original_images=True,
                  placeholders=False, sort_names="A B = B, A", alt_text="")
    spec, errors = forms._build_spec(raw, ["p"])
    assert errors == []
    assert spec.title == "My Book"
    assert spec.subtitle == "Sub"
    assert spec.editor == "Ed"
    assert spec.series == "S"
    assert spec.series_number == "2"
    assert spec.lang == "fr"
    assert spec.image_max_width == 500
    assert spec.image_quality == 70
    assert spec.optimize_images is False
    assert spec.placeholders is False
    assert spec.sort_names == {"A B": "B, A"}
    assert spec.source.platform == "ghost"
    assert spec.source.slugs == ["p"]


def test_build_spec_blank_fields_become_none():
    spec, _ = forms._build_spec(RawForm(posts="p"), ["p"])
    assert spec.title == "Collected Posts"
    assert spec.subtitle is None
    assert spec.editor is None
    assert spec.series is None


def test_build_spec_rejects_non_numeric_image_fields():
    raw = RawForm(posts="p", image_max_width="wide")
    spec, errors = forms._build_spec(raw, ["p"])
    assert spec.image_max_width == forms.DEFAULT_MAX_WIDTH
    assert any(e.field == "image_max_width" for e in errors)


# ---------------------------------------------------------------- check_submission (end to end)

ADMIN_KEY = "0123456789abcdef01234567:" + "ab" * 32


def _fake_posts_session(monkeypatch, routes):
    monkeypatch.setattr(forms.requests, "Session", lambda: FakeSession(routes))


def _post_body(slug: str) -> bytes:
    return json.dumps({"posts": [{"slug": slug, "title": slug, "authors": [], "html": "<p>x</p>"}]}).encode()


def test_check_submission_passes_with_known_posts(monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    routes = {f"{forms.SITE_URL}/ghost/api/admin/posts/slug/good/": [
        FakeResponse(200, _post_body("good"), "application/json")]}
    _fake_posts_session(monkeypatch, routes)

    result = check_submission(RawForm(posts="good", title="T"))
    assert result.ok, result.errors
    assert result.spec.source.slugs == ["good"]


def test_check_submission_reports_missing_post(monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    routes = {f"{forms.SITE_URL}/ghost/api/admin/posts/slug/missing/": [
        FakeResponse(404, b"{}", "application/json")]}
    _fake_posts_session(monkeypatch, routes)

    result = check_submission(RawForm(posts="missing", title="T"))
    assert not result.ok
    assert any("not found" in e.message for e in result.errors)


def test_check_submission_skips_network_when_post_list_invalid(monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    monkeypatch.setattr(forms.requests, "Session", lambda: (_ for _ in ()).throw(
        AssertionError("should not fetch when the post list itself is invalid")))
    result = check_submission(RawForm(posts="https://example.com/x", title="T"))
    assert not result.ok


def test_check_submission_reports_missing_secret(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    result = check_submission(RawForm(posts="good", title="T"))
    assert not result.ok
    assert any("No secret found" in e.message for e in result.errors)


def test_check_submission_reports_isbn_and_cover_together(monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    routes = {f"{forms.SITE_URL}/ghost/api/admin/posts/slug/good/": [
        FakeResponse(200, _post_body("good"), "application/json")]}
    _fake_posts_session(monkeypatch, routes)

    raw = RawForm(posts="good", title="T", isbn="not-an-isbn", cover_bytes=b"not an image")
    result = check_submission(raw)
    fields = {e.field for e in result.errors}
    assert "isbn" in fields
    assert "cover" in fields
