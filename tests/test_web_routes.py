"""The website's routes: form render, checks, accessibility wiring (#23)."""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from conftest import FakeResponse, FakeSession, jpeg, png
from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.secrets import env_var_name
from web import forms
from web.main import app

KEY_ENV = env_var_name("ghost", "flaminghydra.ghost.io")
ADMIN_KEY = "0123456789abcdef01234567:" + "ab" * 32


@pytest.fixture
def client():
    return TestClient(app)


def _post_body(slug: str) -> bytes:
    return json.dumps({"posts": [{"slug": slug, "title": slug, "authors": [], "html": "<p>x</p>"}]}).encode()


def _wire_fake_posts(monkeypatch, *slugs):
    routes = {f"{forms.SITE_URL}/ghost/api/admin/posts/slug/{slug}/": [
        FakeResponse(200, _post_body(slug), "application/json")] for slug in slugs}
    monkeypatch.setattr(forms.requests, "Session", lambda: FakeSession(routes))


# ---------------------------------------------------------------- GET /

def test_get_form_is_noindex_and_has_labels(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<meta name="robots" content="noindex">' in r.text
    assert '<label for="posts">' in r.text
    assert '<label for="title">' in r.text
    assert '<label for="cover">' in r.text
    assert "Checks passed" not in r.text


def test_get_form_advanced_section_closed_by_default(client):
    r = client.get("/")
    assert "<details open>" not in r.text


# ---------------------------------------------------------------- POST errors + accessibility

def test_post_with_errors_shows_summary_tied_to_fields(client):
    r = client.post("/", data={"posts": "", "title": "T"})
    assert r.status_code == 200
    assert 'id="error-summary"' in r.text
    assert 'role="alert"' in r.text
    assert 'tabindex="-1"' in r.text
    assert "autofocus" in r.text
    assert "add at least one post address" in r.text
    assert 'aria-describedby="posts-errors"' in r.text
    assert 'aria-invalid="true"' in r.text


def test_post_with_advanced_field_error_opens_advanced(client):
    r = client.post("/", data={"posts": "good", "title": "T", "isbn": "bad"})
    assert "<details open>" in r.text


def test_post_without_errors_does_not_show_advanced_open(client, monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    _wire_fake_posts(monkeypatch, "good")
    r = client.post("/", data={"posts": "good", "title": "T"})
    assert "<details open>" not in r.text


# ---------------------------------------------------------------- POST success placeholder

def test_post_checks_pass_shows_placeholder(client, monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    _wire_fake_posts(monkeypatch, "good")
    r = client.post("/", data={"posts": "good", "title": "My Book"})
    assert r.status_code == 200
    assert "Checks passed" in r.text
    assert "My Book" in r.text
    assert "isn&#39;t wired up yet" in r.text or "isn't wired up yet" in r.text
    assert "<form" not in r.text


# ---------------------------------------------------------------- cover upload

def test_post_with_oversized_cover_is_rejected(client, monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    _wire_fake_posts(monkeypatch, "good")
    big = jpeg(size=(2000, 2000))
    # Pad past the limit without touching the image bytes PIL needs to decode first.
    big = big + b"0" * max(0, forms.MAX_COVER_BYTES + 1 - len(big))
    r = client.post("/", data={"posts": "good", "title": "T"},
                     files={"cover": ("c.jpg", io.BytesIO(big), "image/jpeg")})
    assert "must be under" in r.text


def test_post_with_bad_cover_file_is_rejected(client, monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    _wire_fake_posts(monkeypatch, "good")
    r = client.post("/", data={"posts": "good", "title": "T"},
                     files={"cover": ("c.jpg", io.BytesIO(b"not an image"), "image/jpeg")})
    assert "isn&#39;t a recognisable image" in r.text or "isn't a recognisable image" in r.text


def test_post_with_valid_cover_passes(client, monkeypatch):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    _wire_fake_posts(monkeypatch, "good")
    r = client.post("/", data={"posts": "good", "title": "T"},
                     files={"cover": ("c.png", io.BytesIO(png()), "image/png")})
    assert "Checks passed" in r.text


def test_cover_temp_file_is_cleaned_up(client, monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    _wire_fake_posts(monkeypatch, "good")
    seen_paths = []
    real_build_spec = forms._build_spec

    def spying_build_spec(raw, slugs):
        if raw.cover_path:
            seen_paths.append(raw.cover_path)
        return real_build_spec(raw, slugs)

    monkeypatch.setattr(forms, "_build_spec", spying_build_spec)
    client.post("/", data={"posts": "good", "title": "T"},
                files={"cover": ("c.png", io.BytesIO(png()), "image/png")})
    assert seen_paths, "cover upload should have been saved to a temp path"
    from pathlib import Path
    assert not Path(seen_paths[0]).exists()
