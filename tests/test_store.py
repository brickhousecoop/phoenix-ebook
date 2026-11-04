"""The website's store, with a fake in place of Postgres (#31, ADR 0004), and access by blog (#41)."""
from __future__ import annotations

import ast
import json
import logging

import pytest
import requests

from conftest import REPO, TEST_PASSWORD, TEST_USER, FakeResponse, FakeSession, users_json, web_client
from phoenix_ebook.secrets import env_var_name
from web import auth, forms, main, storage, store
from web.forms import RawForm, check_submission
from web.store import Blog, StoreKeyProblem, StoreUnavailable

BLOG = Blog("A Blog", "ghost", "https://ablog.ghost.io", ("ablog.com", "ablog.ghost.io"))
OTHER = Blog("Other", "ghost", "https://other.ghost.io", ("other.com",))
KEY = "0123456789abcdef01234567:" + "ab" * 32
NEW_KEY = "fedcba9876543210fedcba98:" + "cd" * 32
OLD_VARIABLE_KEY = "aaaaaaaaaaaaaaaaaaaaaaaa:" + "ee" * 32
MASTER = store.new_master_key()


class FakeStore:
    """Stands in for ``store.Store``. ``down`` makes it fail the way an outage does."""

    def __init__(self):
        self.user_list = {TEST_USER: json.loads(users_json())[TEST_USER]}
        self.blog_list = (BLOG,)
        self.keys = {BLOG.name: store.seal(KEY, BLOG, MASTER)}
        self.access = {(TEST_USER, BLOG.name)}
        self.down = False
        self.down_after_key = False

    def _check(self):
        if self.down:
            raise StoreUnavailable

    def users(self):
        self._check()
        return self.user_list

    def blogs(self):
        self._check()
        return self.blog_list

    def blogs_and_access(self, email):
        self._check()
        return self.blog_list, frozenset(blog for user, blog in self.access if user == email)

    def sealed_key(self, blog):
        self._check()
        self.down = self.down_after_key
        return self.keys.get(blog.name)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """The site connected to a fake store. The old variables are present, holding different values."""
    fake_store = FakeStore()
    monkeypatch.setattr(store, "_open", lambda url: fake_store)
    monkeypatch.setenv(store.URL_ENV, "postgresql://site:password@db.example/phoenix")
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    monkeypatch.setenv(env_var_name("ghost", "ablog.ghost.io"), OLD_VARIABLE_KEY)
    monkeypatch.setenv(auth.USERS_ENV, json.dumps({"old@example.org": auth.hash_password("old password")}))
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path / "books")
    return fake_store


def post_route(slug="a", url=BLOG.url):
    body = {"posts": [{"slug": slug, "title": slug.title(), "authors": [], "html": "<p>x</p>"}]}
    return {f"{url}/ghost/api/admin/posts/slug/{slug}/": [FakeResponse(200, json.dumps(body).encode(), "application/json")]}


def session_seeing_key(routes):
    """A session that records the Admin API token's key id, which says which key was used."""
    session = FakeSession(routes)
    original, session.used = session.get, []

    def get(url, **kwargs):
        session.used.append((kwargs.get("headers") or {}).get("Authorization", ""))
        return original(url, **kwargs)
    session.get = get
    return session


def key_id_used(session) -> str:
    import base64
    token = next(value for value in session.used if value).split()[-1]
    header = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))["kid"]


# ---------------------------------------------------------------- choosing the source

def test_with_the_store_connected_the_old_users_dont_count(fake):
    assert auth.load_users() == fake.user_list
    assert auth.check_password("old@example.org", "old password") is None
    assert auth.check_password(TEST_USER, TEST_PASSWORD) == TEST_USER


def test_with_the_store_connected_the_old_key_variable_is_ignored(fake):
    session = session_seeing_key(post_route())
    result = check_submission(RawForm(posts="ablog.com/a", title="T"), session)
    assert result.ok, result.errors
    assert key_id_used(session) == KEY.split(":")[0]


def test_with_the_store_connected_its_blogs_are_the_list(fake):
    errors = forms._check_post_list("flaminghydra.com/a", forms.current_blogs())[2]
    assert "only builds from A Blog" in errors[0].message
    page = web_client().get("/").text
    assert "Turns a list of posts from A Blog into an EPUB" in " ".join(page.split())
    assert "flaminghydra.com/…" not in page
    assert "ablog.com/…" in page


def test_a_blog_finds_its_rule_file_by_address(fake):
    """No row names the file: a blog's address picks its processor, as it always has."""
    hell = Blog("Hell", "ghost", "https://hellworld.ghost.io", ("hellworld.ghost.io",))
    fake.blog_list = (hell,)
    fake.keys = {hell.name: store.seal(KEY, hell, MASTER)}
    result = check_submission(RawForm(posts="hellworld.ghost.io/a", title="T"), FakeSession(post_route(url=hell.url)))
    assert result.spec.source.processor == "hellworld"


def test_a_blog_with_no_rule_file_gets_the_defaults(fake):
    result = check_submission(RawForm(posts="ablog.com/a", title="T"), FakeSession(post_route()))
    assert result.spec.source.processor == "generic"


def test_without_the_store_the_old_sources_still_work(monkeypatch):
    monkeypatch.delenv(store.URL_ENV, raising=False)
    assert store.active() is None
    assert forms.current_blogs() == forms.BLOGS
    assert auth.check_password(TEST_USER, TEST_PASSWORD) == TEST_USER


def test_on_vercel_with_neither_source_the_sign_in_page_says_so(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv(auth.SECRET_ENV, "x")
    monkeypatch.delenv(auth.USERS_ENV)
    assert "No users are set up" in web_client(None).get("/sign-in").text


def test_on_vercel_old_variables_with_no_store_work_which_is_the_way_back(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv(auth.SECRET_ENV, "x")
    assert auth.setup_problem() is None
    assert auth.check_password(TEST_USER, TEST_PASSWORD) == TEST_USER


def test_a_store_with_no_master_key_says_so(fake, monkeypatch):
    monkeypatch.delenv(store.MASTER_KEY_ENV)
    assert store.MASTER_KEY_ENV in web_client(None).get("/sign-in").text


def test_a_store_with_no_blogs_says_so_on_the_form(fake):
    fake.blog_list = ()
    page = web_client().post("/", data={"posts": "ablog.com/a"}).text
    assert "no blogs set up yet" in page


def test_a_blog_with_no_key_says_so_on_the_form(fake):
    fake.keys = {}
    page = web_client().post("/", data={"posts": "ablog.com/a"}).text
    assert "no key for A Blog" in page


# ---------------------------------------------------------------- changes take effect at once

def test_a_removed_user_is_signed_out_on_their_next_page(fake):
    client = web_client()
    assert client.get("/", follow_redirects=False).status_code == 200
    fake.user_list = {}
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/sign-in")


def test_a_new_user_can_sign_in_on_the_next_request(fake):
    fake.user_list = {**fake.user_list, "new@example.org": auth.hash_password("new password")}
    assert auth.check_password("New@Example.org ", "new password") == "new@example.org"


def test_a_replaced_key_works_on_the_next_request(fake):
    fake.keys = {BLOG.name: store.seal(NEW_KEY, BLOG, MASTER)}
    session = session_seeing_key(post_route())
    assert check_submission(RawForm(posts="ablog.com/a", title="T"), session).ok
    assert key_id_used(session) == NEW_KEY.split(":")[0]


# ---------------------------------------------------------------- outages

def test_an_outage_is_a_try_again_page_and_the_cookie_survives(fake):
    client = web_client()
    fake.down = True
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 503
    assert "Try again in a minute" in r.text
    assert r.headers["retry-after"] == "60"
    assert "set-cookie" not in r.headers
    fake.down = False
    assert client.get("/", follow_redirects=False).status_code == 200


def test_during_an_outage_signing_in_isnt_a_wrong_password(fake):
    fake.down = True
    client = web_client(None)
    assert client.get("/sign-in").status_code == 503
    r = client.post("/sign-in", data={"email": TEST_USER, "password": TEST_PASSWORD})
    assert r.status_code == 503
    assert "don&#39;t match" not in r.text and "don't match" not in r.text
    assert "Try again in a minute" in r.text


def test_an_outage_reaches_the_pages_script_as_a_page(fake):
    fake.down = True
    r = web_client().post("/", data={"posts": "ablog.com/a"}, headers={"Accept": main.STREAM_TYPE})
    assert r.status_code == 503
    assert "Try again in a minute" in json.loads(r.text)["html"]


def test_an_outage_while_reading_the_key_keeps_the_form_filled_in(fake):
    original = fake.sealed_key
    fake.sealed_key = lambda blog: (_ for _ in ()).throw(StoreUnavailable)
    page = web_client().post("/", data={"posts": "ablog.com/a", "title": "Kept"}).text
    fake.sealed_key = original
    assert "Try again in a minute" in page
    assert 'value="Kept"' in page


def test_a_running_build_finishes_if_the_store_fails_after_it_starts(fake, monkeypatch):
    fake.down_after_key = True
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(post_route()))
    r = web_client().post("/", data={"posts": "ablog.com/a", "title": "Finished"},
                          headers={"Accept": main.STREAM_TYPE})
    lines = [json.loads(line) for line in r.text.splitlines()]
    assert fake.down
    assert "Your book is ready" in lines[-1]["html"]


def test_a_real_connection_failure_is_an_outage_and_logs_no_address(monkeypatch, caplog):
    """Nothing is listening at this address, and the password in it must not reach the log."""
    monkeypatch.setattr(store, "CONNECT_TIMEOUT", 1)
    real = store.Store("postgresql://site:hunter2secret@127.0.0.1:1/phoenix")
    with caplog.at_level(logging.DEBUG), pytest.raises(StoreUnavailable):
        real.users()
    assert "hunter2secret" not in caplog.text
    assert "127.0.0.1" not in caplog.text


# ---------------------------------------------------------------- encryption

def test_a_key_round_trips():
    sealed = store.seal(KEY, BLOG, MASTER)
    assert KEY.encode() not in sealed.ciphertext
    assert store.unseal(sealed, BLOG, MASTER) == KEY


def test_each_sealing_is_different():
    assert store.seal(KEY, BLOG, MASTER).ciphertext != store.seal(KEY, BLOG, MASTER).ciphertext


def test_a_key_records_which_master_key_encrypted_it():
    assert store.seal(KEY, BLOG, MASTER).master_id == MASTER.split(".")[0]


def test_the_wrong_master_key_says_which_one_is_needed_without_showing_either():
    sealed = store.seal(KEY, BLOG, MASTER)
    other = store.new_master_key()
    with pytest.raises(StoreKeyProblem) as err:
        store.unseal(sealed, BLOG, other)
    assert MASTER.split(".")[0] in str(err.value)
    assert MASTER.split(".")[1] not in str(err.value)
    assert other.split(".")[1] not in str(err.value)


def test_a_wrong_master_key_with_the_same_id_doesnt_decrypt():
    sealed = store.seal(KEY, BLOG, MASTER)
    impostor = MASTER.split(".")[0] + "." + store.new_master_key().split(".")[1]
    with pytest.raises(StoreKeyProblem):
        store.unseal(sealed, BLOG, impostor)


def test_an_altered_key_doesnt_decrypt():
    sealed = store.seal(KEY, BLOG, MASTER)
    changed = bytes([sealed.ciphertext[-1] ^ 1])
    with pytest.raises(StoreKeyProblem):
        store.unseal(store.SealedKey(sealed.ciphertext[:-1] + changed, sealed.master_id), BLOG, MASTER)


def test_a_key_copied_to_another_blog_doesnt_decrypt():
    with pytest.raises(StoreKeyProblem):
        store.unseal(store.seal(KEY, BLOG, MASTER), OTHER, MASTER)


@pytest.mark.parametrize("bad", ["", "no-dot", "id.short", "id.!!!not base64!!!"])
def test_a_malformed_master_key_is_a_clear_problem(bad):
    with pytest.raises(StoreKeyProblem, match=store.MASTER_KEY_ENV):
        store.seal(KEY, BLOG, bad)


# ---------------------------------------------------------------- secrets stay out of logs and pages

def test_no_key_appears_in_logs_or_pages(fake, monkeypatch, caplog):
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(post_route()))
    with caplog.at_level(logging.DEBUG):
        page = web_client().post("/", data={"posts": "ablog.com/a", "title": "T"}).text
        fake.keys = {BLOG.name: store.SealedKey(b"x" * 40, MASTER.split(".")[0])}
        failed = web_client().post("/", data={"posts": "ablog.com/a", "title": "T"}).text
    for secret in (KEY, KEY.split(":")[1], MASTER, MASTER.split(".")[1], OLD_VARIABLE_KEY):
        assert secret not in caplog.text
        assert secret not in page
        assert secret not in failed


# ---------------------------------------------------------------- the library stays out of it

def test_the_library_has_no_database_dependency():
    for path in (REPO / "phoenix_ebook").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n.split(".")[0] in ("psycopg", "web", "cryptography") for n in names), path
    requirements = (REPO / "requirements.txt").read_text()
    assert "psycopg" not in requirements and "cryptography" not in requirements




# ---------------------------------------------------------------- access by blog (#41)

def two_blogs(fake):
    fake.blog_list = (BLOG, OTHER)
    fake.keys[OTHER.name] = store.seal(KEY, OTHER, MASTER)


def test_a_user_builds_from_their_blog(fake, monkeypatch):
    two_blogs(fake)
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(post_route()))
    assert "Your book is ready" in web_client().post("/", data={"posts": "ablog.com/a", "title": "T"}).text


def test_a_blog_the_user_wasnt_given_is_refused_before_anything_is_fetched(fake, monkeypatch, caplog):
    two_blogs(fake)
    session = FakeSession({})
    monkeypatch.setattr(requests, "Session", lambda: session)
    with caplog.at_level(logging.INFO, logger="web.activity"):
        page = web_client().post("/", data={"posts": "other.com/a\nother.com/b", "title": "T"}).text
    text = " ".join(page.replace("&#39;", "'").split())
    assert "your account can't build from Other: lines 1, 2" in text
    assert "You can build from A Blog" in text and "ask whoever runs this website" in text
    assert session.calls == {}
    assert f"build: user={TEST_USER} site={OTHER.url} posts=2 refused" in caplog.text


def test_the_alt_text_rebuild_is_refused_the_same_way(fake, monkeypatch):
    two_blogs(fake)
    session = FakeSession({})
    monkeypatch.setattr(requests, "Session", lambda: session)
    page = web_client().post("/", data={"posts": "other.com/a", "title": "T", "fix_url_0": "https://other.com/i.png",
                                        "fix_alt_0": "A picture", "fix_current_0": ""}).text
    assert "build from Other" in page.replace("&#39;", "'")
    assert session.calls == {}


def test_the_form_names_only_the_users_blogs(fake):
    two_blogs(fake)
    page = " ".join(web_client().get("/").text.split())
    assert "Turns a list of posts from A Blog into an EPUB" in page
    assert "other.com/…" not in page and "Other" not in page


def test_a_user_with_no_blogs_gets_a_message_not_a_form(fake):
    fake.access = set()
    page = web_client().get("/").text.replace("&#39;", "'")
    assert "Your account can't build from any blog yet" in page
    assert 'id="book-form"' not in page
    posted = web_client().post("/", data={"posts": "ablog.com/a"}).text.replace("&#39;", "'")
    assert "can't build from any blog yet" in posted


def test_taking_a_blog_away_counts_on_the_next_request(fake, monkeypatch):
    two_blogs(fake)
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(post_route()))
    client = web_client()
    fake.access = set()
    assert "Your book is ready" not in client.post("/", data={"posts": "ablog.com/a", "title": "T"}).text


def test_without_the_store_everyone_may_use_every_blog(monkeypatch):
    monkeypatch.delenv(store.URL_ENV, raising=False)
    assert forms.blogs_for("anyone@example.org") == (forms.BLOGS, forms.BLOGS)
