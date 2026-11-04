"""The book-building website: form, checks, build with live progress, results (ADR 0002).

Every page but sign-in needs a signed-in user (ADR 0003), and names and accepts only the blogs
that user may build from (#41). ``/jobs/delete-expired-books`` is for Vercel Cron alone (#33).

Run locally with ``uvicorn web.main:app --reload`` from the repo root.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import threading
from datetime import date
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.templating import Jinja2Templates

from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.epub_builder import PLACEHOLDER_TEXT
from phoenix_ebook.models import Progress
from phoenix_ebook.platforms.base import get_platform

from web import auth, problems, storage, store
from web.forms import (ALT_KINDS, CONTENT_FIELDS, MAX_OPEN_ALT_BOXES, MAX_POSTS, UPLOAD_FIELDS, Blog, CheckResult,
                       RawForm, alt_fixes_from, blogs_for, merge_alt_fixes)
from web.runs import Outcome, describe, run

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Who built what, and failed sign-ins: one line each in the site's logs (Vercel keeps them briefly).
activity = logging.getLogger("web.activity")
activity.setLevel(logging.INFO)
if not activity.handlers:
    activity.addHandler(logging.StreamHandler())

SIGN_IN = "/sign-in"
EXPIRE_PATH = "/jobs/delete-expired-books"  # called by Vercel Cron; see vercel.json
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

STREAM_TYPE = "application/x-ndjson"
# A rebuild sends 3 fields per alt-text box, and a 100-post book can have thousands of images.
MAX_FORM_FIELDS = 30_000
ADVANCED_FIELDS = {
    "series", "series_number", "publisher", "description", "pub_date", "lang", "isbn", "issn",
    "rights", "image_max_width", "image_quality", "keep_original_images", "placeholders",
    "sort_names", "alt_text", *CONTENT_FIELDS,
}


def _post_examples(blogs: tuple[Blog, ...]) -> str:
    return " or ".join(f"<code>{blog.hosts[0]}/…</code>" for blog in blogs)


def _form_page(raw: RawForm, result: CheckResult, user: str, blogs: tuple[Blog, ...]) -> str:
    return templates.get_template("index.html").render(
        user=user,
        raw=raw,
        result=result,
        submitted=bool(result.errors),
        advanced_open=any(e.field in ADVANCED_FIELDS for e in result.errors),
        max_posts=MAX_POSTS,
        blogs=blogs,
        post_examples=_post_examples(blogs),
        placeholder_pages=PLACEHOLDER_TEXT,
    )


def _outcome_page(raw: RawForm, outcome: Outcome, user: str, blogs: tuple[Blog, ...]) -> str:
    raw.kept = outcome.kept
    if not outcome.built:
        return _form_page(raw, outcome.check, user, blogs)
    spec = outcome.check.spec
    platform = get_platform(spec.source.platform)
    fixable = [p for p in outcome.problems if p.kind in ALT_KINDS]
    fix_numbers = {id(p): n for n, p in enumerate(fixable)}
    return templates.get_template("results.html").render(
        user=user,
        keep_hours=round(storage.KEEP_FOR.total_seconds() / 3600),
        spec=spec,
        url=outcome.url,
        groups=problems.group_by_post(outcome.problems, outcome.check.posts),
        kinds=[problems.kind(code) for code in dict.fromkeys(p.kind for p in outcome.problems)],
        counts={code: sum(p.kind == code for p in outcome.problems) for code in {p.kind for p in outcome.problems}},
        kind=problems.kind,
        fix_count=len(fixable),
        alt_kinds=ALT_KINDS,
        collapse_alt=len(fixable) > MAX_OPEN_ALT_BOXES,
        fix_number=lambda p: fix_numbers.get(id(p)),
        thumbnail=platform.thumbnail_url,
        canonical=platform.canonical_image_url,
        # the form behind "Change settings" and the rebuild
        raw=raw,
        result=CheckResult(spec=None),
        advanced_open=False,
        max_posts=MAX_POSTS,
        blogs=blogs,
        post_examples=_post_examples(blogs),
        placeholder_pages=PLACEHOLDER_TEXT,
    )


def _announce(step: Progress) -> bool:
    """Whether screen readers hear this step: each stage's start and end, and every tenth post."""
    return step.done in (1, step.total) or step.done % 10 == 0


def _stream(raw: RawForm, user: str, blogs: tuple[Blog, ...], usable: tuple[Blog, ...]) -> Iterator[str]:
    """Progress lines, then the final page, as newline-delimited JSON.

    The build runs in its own thread so a closed tab doesn't stop it: it
    finishes and saves the book, which nobody then downloads. ``blogs`` was read before the build
    began, so its last page doesn't need the store, and a book that got built is never lost to an outage.
    """
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            outcome = run(raw, lambda step: events.put(("progress", step)), user=user, blogs=blogs, usable=usable)
            _log_build(user, raw, outcome)
            events.put(("page", _outcome_page(raw, outcome, user, usable)))
        except Exception:  # rendering failed; run() itself never raises
            events.put(("page", _form_page(raw, CheckResult(spec=None, errors=[]), user, usable)))
            raise

    threading.Thread(target=work, daemon=True).start()
    while True:
        kind, value = events.get()
        if kind == "progress":
            yield json.dumps({"type": "progress", "message": describe(value), "done": value.done,
                              "total": value.total, "announce": _announce(value)}) + "\n"
        else:
            yield json.dumps({"type": "page", "html": value}) + "\n"
            return


def _log_build(user: str, raw: RawForm, outcome: Outcome) -> None:
    spec = outcome.check.spec
    site = spec.source.url if spec and spec.source else "-"
    count = sum(1 for line in raw.posts.splitlines() if line.strip())
    if refused := outcome.check.refused:  # the blog wasn't theirs: logged so attempts can be seen (#41)
        activity.info("build: user=%s site=%s posts=%d refused", user, refused.url, count)
        return
    activity.info("build: user=%s site=%s posts=%d %s", user, site, count,
                  "built" if outcome.built else "not built")


def _sign_in_page(next_path: str = "/", error: str | None = None, status: int = 200,
                  email: str = "") -> HTMLResponse:
    return HTMLResponse(templates.get_template("sign_in.html").render(
        user=None, next=next_path, error=error, email=email, problem=auth.setup_problem()), status_code=status)


def _try_again(request: Request) -> Response:
    """The store can't be read: say so. Not a wrong password, and the sign-in cookie is left alone."""
    page = templates.get_template("try_again.html").render(user=None, message=store.TRY_AGAIN)
    headers = {"Retry-After": "60"}
    if request.method == "POST" and STREAM_TYPE in request.headers.get("accept", ""):
        return Response(json.dumps({"type": "page", "html": page}) + "\n", media_type=STREAM_TYPE, status_code=503,
                        headers=headers)
    return HTMLResponse(page, status_code=503, headers=headers)


@app.exception_handler(store.StoreUnavailable)
async def store_unavailable(request: Request, exc: store.StoreUnavailable) -> Response:
    return _try_again(request)


def _same_origin(request: Request) -> bool:
    """Whether a form was sent from this site's own pages: browsers name the sending page's
    site in ``Origin`` on every POST, and another site's page can't change it. Behind a proxy
    the site's own address may arrive as ``X-Forwarded-Host``."""
    origin = request.headers.get("origin")
    ours = {request.headers.get("host"), request.headers.get("x-forwarded-host")} - {None}
    return bool(origin) and urlsplit(origin).netloc in ours


def _safe_next(path: str) -> str:
    """Where to go after signing in: a path on this site only, never another site."""
    return path if path.startswith("/") and not path.startswith(("//", "/\\")) else "/"


@app.middleware("http")
async def require_sign_in(request: Request, call_next) -> Response:
    """Every route but the sign-in page needs a signed-in user (``request.state.user``),
    and every POST must come from this site's own pages."""
    if request.method == "POST" and not _same_origin(request):
        return PlainTextResponse("This form must be sent from the site's own pages.", status_code=403)
    if request.url.path in (SIGN_IN, EXPIRE_PATH):  # the job checks its own secret
        return await call_next(request)
    try:
        user = auth.user_from_cookie(request.cookies.get(auth.COOKIE))
    except store.StoreUnavailable:  # raised here, it wouldn't reach the app's exception handlers
        return _try_again(request)
    if user is None:
        if request.method == "POST" and STREAM_TYPE in request.headers.get("accept", ""):
            # The page's script shows whatever page comes back: here, the sign-in page.
            page = _sign_in_page(error="You've been signed out. Sign in again, then build the book.")
            return Response(json.dumps({"type": "page", "html": page.body.decode()}) + "\n",
                            media_type=STREAM_TYPE, status_code=401)
        next_path = request.url.path if request.method == "GET" else "/"
        return RedirectResponse(f"{SIGN_IN}?next={quote(next_path)}", status_code=303)
    request.state.user = user
    return await call_next(request)


@app.get(EXPIRE_PATH)
async def delete_expired_books(request: Request) -> PlainTextResponse:
    """Vercel Cron's call to delete old books (#33). Only a caller holding ``CRON_SECRET`` may run it;
    without that variable nobody can."""
    secret = os.environ.get("CRON_SECRET", "")
    given = request.headers.get("authorization", "")
    if not secret or not hmac.compare_digest(given.encode(), f"Bearer {secret}".encode()):
        return PlainTextResponse("Not allowed.", status_code=401)
    deleted = await run_in_threadpool(storage.delete_expired)
    activity.info("expired books: deleted %d folders", deleted)
    return PlainTextResponse(f"Deleted {deleted} book folders.")


@app.get(SIGN_IN, response_class=HTMLResponse)
async def sign_in_form(next: str = "/") -> HTMLResponse:
    return _sign_in_page(_safe_next(next))


def _secure_cookie(request: Request) -> bool:
    """Cookies are HTTPS-only, except on this computer's own address, which some browsers
    won't give an HTTPS-only cookie over plain HTTP."""
    return request.url.hostname not in LOCAL_HOSTS


@app.post(SIGN_IN)
async def sign_in(request: Request, email: str = Form(""), password: str = Form(""),
                  next: str = Form("/")) -> Response:
    user = await run_in_threadpool(auth.check_password, email, password) if not auth.setup_problem() else None
    if user is None:
        activity.info("sign-in failed: email=%s", auth.normalize(email) or "-")
        return _sign_in_page(_safe_next(next), error="That email address and password don't match a user.",
                             status=401, email=email.strip())
    activity.info("sign-in: user=%s", user)
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(auth.COOKIE, auth.make_cookie(user), max_age=auth.MAX_AGE,
                        secure=_secure_cookie(request), httponly=True, samesite="lax")
    return response


@app.post("/sign-out")
async def sign_out(request: Request) -> RedirectResponse:
    response = RedirectResponse(SIGN_IN, status_code=303)
    response.delete_cookie(auth.COOKIE, secure=_secure_cookie(request), httponly=True, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
async def form(request: Request) -> HTMLResponse:
    # The server's date, which on Vercel is UTC; see #29.
    raw = RawForm(pub_date=date.today().isoformat())
    user = request.state.user
    return HTMLResponse(_form_page(raw, CheckResult(spec=None), user, blogs_for(user)[1]))


@app.post("/")
async def submit(request: Request):
    # Read here, not through Form() parameters: those would use the form parser's limit of 1,000 fields.
    form = await request.form(max_fields=MAX_FORM_FIELDS)

    def text(name: str) -> str:
        value = form.get(name, "")
        return value if isinstance(value, str) else ""

    files = {}
    for name in UPLOAD_FIELDS:
        upload = form.get(name)
        if getattr(upload, "filename", None) and (data := await upload.read()):
            files[name] = (data, upload.filename)
    raw = RawForm(
        posts=text("posts"), title=text("title"), subtitle=text("subtitle"), editor=text("editor"),
        files=files, kept={name: form[f"kept_{name}"] for name in UPLOAD_FIELDS if form.get(f"kept_{name}")},
        series=text("series"), series_number=text("series_number"), publisher=text("publisher"),
        description=text("description"), pub_date=text("pub_date"), lang=text("lang") or "en", isbn=text("isbn"),
        issn=text("issn"), rights=text("rights"), image_max_width=text("image_max_width"),
        image_quality=text("image_quality"), keep_original_images="keep_original_images" in form,
        placeholders="placeholders" in form,
        sort_names=text("sort_names"), alt_text=merge_alt_fixes(text("alt_text"), alt_fixes_from(form)),
    )
    user = request.state.user
    blogs, usable = blogs_for(user)
    if STREAM_TYPE in request.headers.get("accept", ""):
        return StreamingResponse(_stream(raw, user, blogs, usable), media_type=STREAM_TYPE)
    outcome = await run_in_threadpool(run, raw, user=user, blogs=blogs, usable=usable)
    _log_build(user, raw, outcome)
    return HTMLResponse(_outcome_page(raw, outcome, user, usable))


@app.get(storage.LOCAL_URL_PREFIX + "/{token}/{name}")
async def local_book(token: str, name: str) -> FileResponse:
    path = storage.local_book(f"{token}/{name}")
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type=storage.EPUB_TYPE, filename=name)
