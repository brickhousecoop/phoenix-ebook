"""Website sign-in (#34, ADR 0003): users, cookies, protected routes, forged forms, activity log."""
from __future__ import annotations

import json
import logging
import time

import pytest

from conftest import TEST_PASSWORD, TEST_USER, FakeResponse, FakeSession, web_client
from phoenix_ebook.secrets import env_var_name
from web import auth, forms, main, storage

KEY_ENV = env_var_name("ghost", "flaminghydra.ghost.io")
ADMIN_KEY = "0123456789abcdef01234567:" + "ab" * 32


@pytest.fixture(autouse=True)
def books_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path / "books")


def wire_post(monkeypatch, slug="good"):
    body = json.dumps({"posts": [{"slug": slug, "title": slug, "authors": [], "html": "<p>x</p>"}]}).encode()
    routes = {f"{forms.SITE_URL}/ghost/api/admin/posts/slug/{slug}/": [FakeResponse(200, body, "application/json")]}
    monkeypatch.setenv(KEY_ENV, ADMIN_KEY)
    monkeypatch.setattr(forms.requests, "Session", lambda: FakeSession(routes))


def sign_in(client, email=TEST_USER, password=TEST_PASSWORD, next_path="/"):
    return client.post("/sign-in", data={"email": email, "password": password, "next": next_path},
                       follow_redirects=False)


# ---------------------------------------------------------------- signed out

def test_signed_out_form_redirects_to_sign_in():
    r = web_client(None).get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/sign-in?next=/"


def test_signed_out_post_builds_nothing(monkeypatch):
    built = []
    monkeypatch.setattr(main, "run", lambda *a, **k: built.append(1))
    r = web_client(None).post("/", data={"posts": "good"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/sign-in")
    assert not built


def test_signed_out_streamed_post_gets_the_sign_in_page_as_its_page(monkeypatch):
    built = []
    monkeypatch.setattr(main, "run", lambda *a, **k: built.append(1))
    r = web_client(None).post("/", data={"posts": "good"}, headers={"Accept": main.STREAM_TYPE})
    assert r.status_code == 401
    message = json.loads(r.text.splitlines()[0])
    assert message["type"] == "page"
    assert "You&#39;ve been signed out" in message["html"] or "You've been signed out" in message["html"]
    assert not built


def test_signed_out_local_download_is_refused(tmp_path):
    (tmp_path / "books" / "abc").mkdir(parents=True)
    (tmp_path / "books" / "abc" / "b.epub").write_bytes(b"epub")
    assert web_client().get("/books/abc/b.epub").status_code == 200
    r = web_client(None).get("/books/abc/b.epub", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/sign-in?next=/books/abc/b.epub"


def test_sign_in_page_is_open_and_labelled():
    html = web_client(None).get("/sign-in").text
    assert '<label for="email">' in html and '<label for="password">' in html
    assert 'autocomplete="current-password"' in html
    assert "Signed in as" not in html


# ---------------------------------------------------------------- signing in

def test_right_password_signs_in_and_returns_to_next():
    client = web_client(None)
    r = sign_in(client, next_path="/books/x")
    assert r.status_code == 303 and r.headers["location"] == "/books/x"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.COOKIE}=")
    for flag in ("Secure", "HttpOnly", "SameSite=lax", f"Max-Age={30 * 24 * 60 * 60}"):
        assert flag in cookie, flag
    assert f"Signed in as {TEST_USER}" in client.get("/").text


def test_cookie_works_over_plain_http_on_this_computer_only():
    from fastapi.testclient import TestClient
    local = TestClient(main.app, base_url="http://localhost:8000", headers={"Origin": "http://localhost:8000"})
    assert "Secure" not in sign_in(local).headers["set-cookie"]
    assert "Secure" in sign_in(web_client(None)).headers["set-cookie"]


def test_email_capitals_and_spaces_dont_matter():
    assert sign_in(web_client(None), email=f"  {TEST_USER.upper()} ").status_code == 303


@pytest.mark.parametrize("email, password", [(TEST_USER, "wrong"), ("nobody@example.org", TEST_PASSWORD)])
def test_wrong_password_or_unknown_user_gets_one_message_and_a_log_line(email, password, caplog):
    with caplog.at_level(logging.INFO, logger="web.activity"):
        r = sign_in(web_client(None), email=email, password=password)
    assert r.status_code == 401
    assert "don&#39;t match a user" in r.text or "don't match a user" in r.text
    assert "set-cookie" not in r.headers
    assert f'value="{email}"' in r.text  # the address is kept, the password isn't
    assert f"sign-in failed: email={email}" in caplog.text
    assert password not in caplog.text


def test_next_never_leads_off_the_site():
    for target in ("https://evil.example/", "//evil.example/", "/\\evil.example/"):
        assert sign_in(web_client(None), next_path=target).headers["location"] == "/"


def test_sign_out_clears_the_cookie():
    client = web_client(None)
    sign_in(client)
    r = client.post("/sign-out", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/sign-in"
    assert client.get("/", follow_redirects=False).status_code == 303


# ---------------------------------------------------------------- cookies

def test_removed_user_is_signed_out_at_the_next_request(monkeypatch):
    client = web_client()
    assert client.get("/").status_code == 200
    monkeypatch.setenv(auth.USERS_ENV, json.dumps({"someone-else@example.org": "x"}))
    assert client.get("/", follow_redirects=False).status_code == 303


def test_cookie_expires_after_30_days():
    cookie = auth.make_cookie(TEST_USER, now=time.time() - auth.MAX_AGE - 1)
    assert auth.user_from_cookie(cookie) is None
    assert auth.user_from_cookie(auth.make_cookie(TEST_USER)) == TEST_USER


def test_tampered_cookie_is_refused():
    payload, signature = auth.make_cookie(TEST_USER).rsplit(".", 1)
    forged = auth.make_cookie("someone@example.org").rsplit(".", 1)[0]
    assert auth.user_from_cookie(f"{forged}.{signature}") is None
    assert auth.user_from_cookie("junk") is None


def test_changing_the_secret_signs_everyone_out(monkeypatch):
    monkeypatch.setenv(auth.SECRET_ENV, "one")
    cookie = auth.make_cookie(TEST_USER)
    monkeypatch.setenv(auth.SECRET_ENV, "two")
    assert auth.user_from_cookie(cookie) is None


# ---------------------------------------------------------------- forged forms

@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_post_from_elsewhere_is_refused(origin, monkeypatch):
    built = []
    monkeypatch.setattr(main, "run", lambda *a, **k: built.append(1))
    client = web_client()
    if origin is None:
        del client.headers["Origin"]
    else:
        client.headers["Origin"] = origin
    assert client.post("/", data={"posts": "good"}).status_code == 403
    assert client.post("/sign-in", data={"email": TEST_USER, "password": TEST_PASSWORD}).status_code == 403
    assert not built


def test_post_through_a_proxy_naming_the_site_in_x_forwarded_host_is_accepted():
    client = web_client()
    client.headers.update({"Origin": "https://phoenix-ebook.vercel.app",
                           "X-Forwarded-Host": "phoenix-ebook.vercel.app"})
    assert client.post("/", data={"posts": ""}).status_code == 200


# ---------------------------------------------------------------- where users come from

def test_no_users_anywhere_means_nobody_signs_in(monkeypatch):
    monkeypatch.delenv(auth.USERS_ENV)
    client = web_client(None)
    assert "No users are set up" in client.get("/sign-in").text
    assert sign_in(client).status_code == 401
    assert web_client().get("/", follow_redirects=False).status_code == 303


def test_invalid_users_json_means_nobody_signs_in_and_is_logged(monkeypatch, caplog):
    monkeypatch.setenv(auth.USERS_ENV, "{not json")
    assert sign_in(web_client(None)).status_code == 401
    assert "isn't valid JSON" in caplog.text


def test_users_file_is_used_when_the_variable_isnt_set(monkeypatch):
    users = json.loads(auth.os.environ[auth.USERS_ENV])
    monkeypatch.delenv(auth.USERS_ENV)
    auth.USERS_FILE.write_text(json.dumps(users))
    assert sign_in(web_client(None)).status_code == 303


def test_on_vercel_without_a_session_secret_nobody_signs_in(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    page = web_client(None).get("/sign-in").text
    assert auth.SECRET_ENV in page
    assert sign_in(web_client(None)).status_code == 401


# ---------------------------------------------------------------- builds

def test_build_is_logged_with_user_site_and_count(monkeypatch, caplog):
    wire_post(monkeypatch)
    with caplog.at_level(logging.INFO, logger="web.activity"):
        page = web_client().post("/", data={"posts": "good", "title": "T"}).text
    assert "Your book is ready" in page
    assert f"build: user={TEST_USER} site={forms.SITE_URL} posts=1 built" in caplog.text


def test_may_build_is_asked_before_building(monkeypatch):
    wire_post(monkeypatch)
    asked = []
    monkeypatch.setattr("web.runs.may_build", lambda user, source: asked.append((user, source.url)) or False)
    page = web_client().post("/", data={"posts": "good", "title": "T"}).text
    assert asked == [(TEST_USER, forms.SITE_URL)]
    assert "can&#39;t build books from" in page or "can't build books from" in page
    assert "Your book is ready" not in page


def test_every_page_shows_who_is_signed_in_and_sign_out(monkeypatch):
    wire_post(monkeypatch)
    client = web_client()
    for html in (client.get("/").text, client.post("/", data={"posts": "good", "title": "T"}).text):
        assert f"Signed in as {TEST_USER}" in html
        assert 'action="/sign-out"' in html


def test_progress_script_handles_only_the_book_form(monkeypatch):
    """The Sign out button is a form too; the script that streams builds must leave it alone."""
    wire_post(monkeypatch)
    client = web_client()
    for html in (client.get("/").text, client.post("/", data={"posts": "good", "title": "T"}).text):
        assert 'document.getElementById("book-form")' in html
        assert html.count('id="book-form"') == 1
        assert 'querySelector("form")' not in html
