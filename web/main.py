"""The book-building website: form, checks, build with live progress, results (ADR 0002).

Run locally with ``uvicorn web.main:app --reload`` from the repo root.
"""
from __future__ import annotations

import json
import logging
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

from web import auth, problems, storage
from web.forms import (ALT_KINDS, CONTENT_FIELDS, MAX_POSTS, UPLOAD_FIELDS, CheckResult, RawForm, alt_fixes_from,
                       merge_alt_fixes)
from web.runs import Outcome, describe, run

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Who built what, and failed sign-ins: one line each in the site's logs (Vercel keeps them briefly).
activity = logging.getLogger("web.activity")
activity.setLevel(logging.INFO)
if not activity.handlers:
    activity.addHandler(logging.StreamHandler())

SIGN_IN = "/sign-in"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

STREAM_TYPE = "application/x-ndjson"
ADVANCED_FIELDS = {
    "series", "series_number", "publisher", "description", "pub_date", "lang", "isbn", "issn",
    "rights", "image_max_width", "image_quality", "keep_original_images", "placeholders",
    "sort_names", "alt_text", *CONTENT_FIELDS,
}


def _form_page(raw: RawForm, result: CheckResult, user: str) -> str:
    return templates.get_template("index.html").render(
        user=user,
        raw=raw,
        result=result,
        submitted=bool(result.errors),
        advanced_open=any(e.field in ADVANCED_FIELDS for e in result.errors),
        max_posts=MAX_POSTS,
        placeholder_pages=PLACEHOLDER_TEXT,
    )


def _outcome_page(raw: RawForm, outcome: Outcome, user: str) -> str:
    raw.kept = outcome.kept
    if not outcome.built:
        return _form_page(raw, outcome.check, user)
    spec = outcome.check.spec
    platform = get_platform(spec.source.platform)
    fixable = [p for p in outcome.problems if p.kind in ALT_KINDS]
    fix_numbers = {id(p): n for n, p in enumerate(fixable)}
    return templates.get_template("results.html").render(
        user=user,
        spec=spec,
        url=outcome.url,
        groups=problems.group_by_post(outcome.problems, outcome.check.posts),
        kinds=[problems.kind(code) for code in dict.fromkeys(p.kind for p in outcome.problems)],
        counts={code: sum(p.kind == code for p in outcome.problems) for code in {p.kind for p in outcome.problems}},
        kind=problems.kind,
        fix_count=len(fixable),
        fix_number=lambda p: fix_numbers.get(id(p)),
        thumbnail=platform.thumbnail_url,
        canonical=platform.canonical_image_url,
        # the form behind "Change settings" and the rebuild
        raw=raw,
        result=CheckResult(spec=None),
        advanced_open=False,
        max_posts=MAX_POSTS,
        placeholder_pages=PLACEHOLDER_TEXT,
    )


def _announce(step: Progress) -> bool:
    """Whether screen readers hear this step: each stage's start and end, and every tenth post."""
    return step.done in (1, step.total) or step.done % 10 == 0


def _stream(raw: RawForm, user: str) -> Iterator[str]:
    """Progress lines, then the final page, as newline-delimited JSON.

    The build runs in its own thread so a closed tab doesn't stop it: it
    finishes and saves the book, which nobody then downloads.
    """
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            outcome = run(raw, lambda step: events.put(("progress", step)), user=user)
            _log_build(user, raw, outcome)
            events.put(("page", _outcome_page(raw, outcome, user)))
        except Exception:  # rendering failed; run() itself never raises
            events.put(("page", _form_page(raw, CheckResult(spec=None, errors=[]), user)))
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
    activity.info("build: user=%s site=%s posts=%d %s", user, site, count,
                  "built" if outcome.built else "not built")


def _sign_in_page(next_path: str = "/", error: str | None = None, status: int = 200,
                  email: str = "") -> HTMLResponse:
    return HTMLResponse(templates.get_template("sign_in.html").render(
        user=None, next=next_path, error=error, email=email, problem=auth.setup_problem()), status_code=status)


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
    if request.url.path == SIGN_IN:
        return await call_next(request)
    user = auth.user_from_cookie(request.cookies.get(auth.COOKIE))
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
    return HTMLResponse(_form_page(raw, CheckResult(spec=None), request.state.user))


@app.post("/")
async def submit(
    request: Request,
    posts: str = Form(""),
    title: str = Form(""),
    subtitle: str = Form(""),
    editor: str = Form(""),
    series: str = Form(""),
    series_number: str = Form(""),
    publisher: str = Form(""),
    description: str = Form(""),
    pub_date: str = Form(""),
    lang: str = Form("en"),
    isbn: str = Form(""),
    issn: str = Form(""),
    rights: str = Form(""),
    image_max_width: str = Form(""),
    image_quality: str = Form(""),
    keep_original_images: str | None = Form(None),
    placeholders: str | None = Form(None),
    sort_names: str = Form(""),
    alt_text: str = Form(""),
):
    form = await request.form()
    files = {}
    for name in UPLOAD_FIELDS:
        upload = form.get(name)
        if getattr(upload, "filename", None) and (data := await upload.read()):
            files[name] = (data, upload.filename)
    raw = RawForm(
        posts=posts, title=title, subtitle=subtitle, editor=editor,
        files=files, kept={name: form[f"kept_{name}"] for name in UPLOAD_FIELDS if form.get(f"kept_{name}")},
        series=series, series_number=series_number, publisher=publisher, description=description,
        pub_date=pub_date, lang=lang or "en", isbn=isbn, issn=issn, rights=rights,
        image_max_width=image_max_width, image_quality=image_quality,
        keep_original_images=keep_original_images is not None,
        placeholders=placeholders is not None,
        sort_names=sort_names, alt_text=merge_alt_fixes(alt_text, alt_fixes_from(form)),
    )
    user = request.state.user
    if STREAM_TYPE in request.headers.get("accept", ""):
        return StreamingResponse(_stream(raw, user), media_type=STREAM_TYPE)
    outcome = await run_in_threadpool(run, raw, user=user)
    _log_build(user, raw, outcome)
    return HTMLResponse(_outcome_page(raw, outcome, user))


@app.get(storage.LOCAL_URL_PREFIX + "/{token}/{name}")
async def local_book(token: str, name: str) -> FileResponse:
    path = storage.local_book(f"{token}/{name}")
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type=storage.EPUB_TYPE, filename=name)
