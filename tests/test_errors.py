"""User-facing errors: typed library errors, one-line CLI messages, no partial books (#14)."""
from __future__ import annotations

import json

import pytest
import requests

import build_epub
from conftest import FakeResponse, FakeSession, ok, png
from phoenix_ebook import epub_builder, secrets
from phoenix_ebook.errors import (AuthError, InvalidBookSpec, ManifestError, MissingContentFile, MissingSecret,
                                  OutputError, PhoenixError, PostNotFound, SourceUnreachable, explain_response)
from phoenix_ebook.images import ImageFetchError, fetch_image
from phoenix_ebook.platforms.ghost import GhostPlatform

SITE = "https://site.ghost.io"
KEY = "0123456789abcdef01234567:" + "ab" * 32


def post_url(slug: str) -> str:
    return f"{SITE}/ghost/api/admin/posts/slug/{slug}/"


def ghost_post(slug: str) -> FakeResponse:
    body = {"posts": [{"slug": slug, "title": slug.title(), "html": "<p>Text.</p>", "authors": []}]}
    return FakeResponse(200, json.dumps(body).encode(), "application/json")


def ghost_error(status: int, message: str) -> FakeResponse:
    return FakeResponse(status, json.dumps({"errors": [{"message": message}]}).encode(), "application/json")


@pytest.fixture
def cli(monkeypatch, tmp_path, capsys):
    """cli(args, routes) -> (exit code, stderr, session). Runs main() against a fake Ghost."""
    def run(args, routes=None, *, output=True):
        session = FakeSession(routes or {})
        monkeypatch.setattr(build_epub.requests, "Session", lambda: session)
        argv = ["--url", SITE] + (["--output", str(tmp_path / "book.epub")] if output else []) + args
        try:
            build_epub.main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
            if isinstance(exc.code, str):
                print(exc.code, file=__import__("sys").stderr)
        return code, capsys.readouterr().err, session
    return run


def assert_clean_error(code, err, *fragments):
    assert code == 1, err
    assert "Traceback" not in err, err
    lines = [line for line in err.strip().splitlines() if line.startswith("error:")]
    assert len(lines) == 1, err
    for fragment in fragments:
        assert fragment in err, (fragment, err)


# ---------------------------------------------------------------- credentials

def test_wrong_key_stops_after_first_request(cli):
    slugs = [f"s{i}" for i in range(10)]
    code, err, session = cli(["--admin-key", KEY] + slugs, {post_url("s0"): [ghost_error(401, "Unknown Admin API Key")]})
    assert_clean_error(code, err, "Ghost rejected the Admin API key for site.ghost.io (HTTP 401): Unknown Admin API Key",
                       "--set-secret")
    assert [u for u, _ in session.log if "/posts/" in u] == [post_url("s0")]


def test_malformed_key_makes_no_request(cli):
    code, err, session = cli(["--admin-key", "notakey", "s"])
    assert_clean_error(code, err, "must look like id:secret")
    assert not session.log


def test_missing_secret_has_no_traceback(cli, monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "SECRET_FILE", tmp_path / "none.json")
    monkeypatch.setattr(secrets, "LEGACY_GHOST_SECRET_FILE", tmp_path / "legacy.json")
    monkeypatch.setattr(secrets, "keyring", None)
    monkeypatch.delenv("PHOENIX_SECRET_GHOST_SITE_GHOST_IO", raising=False)
    code, err, _ = cli(["s"])
    assert_clean_error(code, err, "No secret found", "--set-secret")


def test_redirect_is_named_instead_of_blaming_the_key(cli):
    redirected = FakeResponse(401, b"", "text/plain", url="https://other.ghost.io/ghost/api/admin/posts/slug/s/",
                              history=[FakeResponse(301)])
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [redirected]})
    assert_clean_error(code, err, "redirected to https://other.ghost.io", "--url https://other.ghost.io")
    assert "rejected" not in err


# ---------------------------------------------------------------- posts

def test_all_missing_slugs_reported_together(cli, tmp_path):
    routes = {post_url("a"): [ghost_post("a")], post_url("typo1"): [ghost_error(404, "Post not found.")],
              post_url("b"): [ghost_post("b")], post_url("typo2"): [ghost_error(404, "Post not found.")]}
    code, err, session = cli(["--admin-key", KEY, "a", "typo1", "b", "typo2"], routes)
    assert_clean_error(code, err, '2 posts not found on site.ghost.io: "typo1", "typo2"')
    assert {u for u, _ in session.log if "/posts/" in u} == set(routes)
    assert not (tmp_path / "book.epub").exists()


@pytest.mark.parametrize("response", [
    FakeResponse(404, b"<html><body>Not found</body></html>", "text/html"),
    FakeResponse(200, b"<!doctype html><html>Welcome</html>", "text/html"),
    FakeResponse(200, b'{"hello": "world"}', "application/json"),
], ids=["html-404", "html-200", "json-without-posts"])
def test_wrong_site_url_is_not_mistaken_for_a_missing_post(cli, response):
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [response]})
    assert_clean_error(code, err, "Ghost address")
    assert "not found on" not in err


# ---------------------------------------------------------------- network

def test_firewall_403_shows_its_explanation(cli):
    body = b"Approval required for site.ghost.io:443.\n\nReview and respond with:\n  sbx policy approval ls"
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [FakeResponse(403, body, "text/plain")]})
    assert_clean_error(code, err, "can't reach site.ghost.io (HTTP 403): Approval required for site.ghost.io:443.")
    assert "rejected" not in err  # a firewall block isn't a bad key


@pytest.mark.parametrize("exc", [requests.ConnectionError("dns"), requests.Timeout("slow")])
def test_unreachable_host(cli, exc):
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [exc]})
    assert_clean_error(code, err, "can't reach site.ghost.io", type(exc).__name__)


# ---------------------------------------------------------------- manifests, options

def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


def test_invalid_toml(cli, tmp_path):
    code, err, _ = cli(["--manifest", write(tmp_path, "m.toml", 'posts = ["a"]\ntitle = \n[book]\n')], output=False)
    assert_clean_error(code, err, "isn't valid TOML", "line 2")


def test_manifest_without_source(cli, tmp_path):
    code, err, _ = cli(["--manifest", write(tmp_path, "m.toml", 'posts=["a"]\n[book]\ntitle="t"\n')], output=False)
    assert_clean_error(code, err, "[source] table with platform and url")


def test_manifest_not_found(cli, tmp_path):
    code, err, _ = cli(["--manifest", str(tmp_path / "nope.toml")], output=False)
    assert_clean_error(code, err, "manifest not found")


@pytest.mark.parametrize("flag, value, text", [("--platform", "substack", "unknown platform 'substack'; available: ghost"),
                                               ("--processor", "nope", "unknown processor 'nope'")])
def test_unknown_platform_or_processor(cli, flag, value, text):
    code, err, _ = cli(["--admin-key", KEY, flag, value, "s"])
    assert_clean_error(code, err, text)


def test_set_secret_without_site(cli, monkeypatch):
    monkeypatch.setattr(build_epub, "build_parser", build_epub.build_parser)
    code, err, _ = cli(["--set-secret", "--url", ""], output=False)
    assert_clean_error(code, err, "--set-secret needs --url")


def test_non_utf8_content_file_makes_no_request(cli, tmp_path):
    path = write(tmp_path, "foreword.html", "<p>Café</p>", encoding="latin-1")
    code, err, session = cli(["--admin-key", KEY, "--foreword-file", path, "s"])
    assert_clean_error(code, err, "--foreword-file", "isn't UTF-8")
    assert not session.log


# ---------------------------------------------------------------- output

def test_missing_output_folder_makes_no_request(cli, tmp_path, monkeypatch):
    session = FakeSession({})
    monkeypatch.setattr(build_epub.requests, "Session", lambda: session)
    with pytest.raises(SystemExit) as exit_info:
        build_epub.main(["--admin-key", KEY, "--output", str(tmp_path / "no-such-dir" / "b.epub"), "s"])
    assert "doesn't exist" in str(exit_info.value.code) and not session.log


def test_output_that_is_a_folder(cli, tmp_path):
    code, err, _ = cli(["--admin-key", KEY, "--output", str(tmp_path), "s"], output=False)
    assert_clean_error(code, err, "it's a folder")


def test_write_failure_is_clean_and_keeps_existing_book(cli, tmp_path, monkeypatch):
    existing = tmp_path / "book.epub"
    existing.write_bytes(b"previous book")

    def full_disk(self):  # writes part of the file, then fails, like a real full disk
        with open(self.file_name, "wb") as f:
            f.write(b"PK\x03\x04 half a book")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(epub_builder._Writer, "write", full_disk)
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [ghost_post("s")]})
    assert_clean_error(code, err, "can't write", "No space left on device")
    assert existing.read_bytes() == b"previous book"
    assert [p.name for p in tmp_path.iterdir()] == ["book.epub"]  # no temp file left


def test_interrupt_while_writing_keeps_existing_book(cli, tmp_path, monkeypatch):
    existing = tmp_path / "book.epub"
    existing.write_bytes(b"previous book")

    def interrupted(self):  # Ctrl-C halfway through writing
        with open(self.file_name, "wb") as f:
            f.write(b"PK\x03\x04 half a book")
        raise KeyboardInterrupt
    monkeypatch.setattr(epub_builder._Writer, "write", interrupted)
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [ghost_post("s")]})
    assert code == 130 and err.strip() == "interrupted"
    assert existing.read_bytes() == b"previous book"
    assert [p.name for p in tmp_path.iterdir()] == ["book.epub"]


def test_ctrl_c_while_fetching(cli, tmp_path):
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [KeyboardInterrupt()]})
    assert code == 130 and "Traceback" not in err and err.strip() == "interrupted"
    assert not (tmp_path / "book.epub").exists()


def test_success_leaves_only_the_book(cli, tmp_path):
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [ghost_post("s")]})
    assert code == 0, err
    assert [p.name for p in tmp_path.iterdir()] == ["book.epub"]


def test_warnings_still_exit_zero(cli, tmp_path):
    body = {"posts": [{"slug": "s", "title": "S", "html": '<img src="https://img.test/x.png"/>', "authors": []}]}
    routes = {post_url("s"): [FakeResponse(200, json.dumps(body).encode(), "application/json")],
              "https://img.test/x.png": [FakeResponse(404)]}
    code, err, _ = cli(["--admin-key", KEY, "s"], routes)
    assert code == 0 and "warning:" in err and "error:" not in err


# ---------------------------------------------------------------- debug, bugs

def test_debug_shows_traceback_for_handled_errors(cli):
    code, err, _ = cli(["--debug", "--admin-key", KEY, "s"], {post_url("s"): [ghost_error(401, "nope")]})
    assert code == 1 and "Traceback" in err and "error: Ghost rejected" in err


def test_unexpected_error_always_shows_traceback(cli, monkeypatch):
    def broken(*a, **k):
        raise ZeroDivisionError("oops")
    monkeypatch.setattr(build_epub, "build", broken)
    code, err, _ = cli(["--admin-key", KEY, "s"], {post_url("s"): [ghost_post("s")]})
    assert code == 1 and "Traceback" in err and "ZeroDivisionError" in err and "bug in phoenix-ebook" in err


# ---------------------------------------------------------------- library level

def test_library_raises_typed_errors():
    platform = GhostPlatform()
    cases = [
        ({post_url("s"): [ghost_error(401, "bad")]}, AuthError),
        ({post_url("s"): [ghost_error(404, "Post not found.")]}, PostNotFound),
        ({post_url("s"): [requests.ConnectionError("down")]}, SourceUnreachable),
    ]
    for routes, error in cases:
        with pytest.raises(error):
            platform.fetch_post(FakeSession(routes), SITE, KEY, "s")
    with pytest.raises(AuthError):
        platform.fetch_post(FakeSession({}), SITE, "bad-key", "s")


def test_error_hierarchy():
    for error in (AuthError, PostNotFound, SourceUnreachable, ManifestError, MissingSecret, OutputError,
                  InvalidBookSpec, MissingContentFile):
        assert issubclass(error, PhoenixError)
    assert issubclass(InvalidBookSpec, ValueError)
    assert epub_builder.InvalidBookSpec is InvalidBookSpec  # old import path still works


def test_library_build_raises_output_error(tmp_path):
    from conftest import post
    from phoenix_ebook.epub_builder import build
    from phoenix_ebook.models import BookSpec
    from phoenix_ebook.processors.base import get_processor
    with pytest.raises(OutputError):
        build(BookSpec(title="t", output=str(tmp_path / "missing" / "b.epub")), [post()], get_processor("generic"),
              session=FakeSession())


# ---------------------------------------------------------------- response bodies

@pytest.mark.parametrize("response, expected", [
    (FakeResponse(403, b"Approval required for x:443.\n\nReview it\nmore", "text/plain"), "Approval required for x:443. Review it"),
    (FakeResponse(403, b"Approval required for x:443.\n\nReview and respond with:\n  sbx policy approval ls\nmore",
                  "text/plain"), "Approval required for x:443. Review and respond with: sbx policy approval ls"),
    (ghost_error(401, "Unknown Admin API Key"), "Unknown Admin API Key"),
    (FakeResponse(500, b"<html><body>Oops</body></html>", "text/html"), None),
    (FakeResponse(502, b"", "text/plain"), None),
])
def test_explain_response(response, expected):
    assert explain_response(response) == expected


def test_long_bodies_are_truncated():
    assert len(explain_response(FakeResponse(500, b"x" * 1000, "text/plain"))) == 300


def test_image_warnings_include_the_explanation():
    session = FakeSession({"https://img.test/a.png": [FakeResponse(403, b"Blocked by network policy: domain img.test", "text/plain")]})
    with pytest.raises(ImageFetchError, match="HTTP 403: Blocked by network policy"):
        fetch_image(session, "https://img.test/a.png")
