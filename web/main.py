"""The book-building website: form, checks, build with live progress, results (ADR 0002).

Run locally with ``uvicorn web.main:app --reload`` from the repo root.
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import date
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.epub_builder import PLACEHOLDER_TEXT
from phoenix_ebook.models import Progress

from web import problems, storage
from web.forms import MAX_POSTS, CheckResult, RawForm
from web.runs import Outcome, describe, run

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STREAM_TYPE = "application/x-ndjson"
ADVANCED_FIELDS = {
    "series", "series_number", "publisher", "description", "pub_date", "lang", "isbn", "issn",
    "rights", "image_max_width", "image_quality", "keep_original_images", "placeholders",
    "sort_names", "alt_text",
}


def _form_page(raw: RawForm, result: CheckResult) -> str:
    return templates.get_template("index.html").render(
        raw=raw,
        result=result,
        submitted=bool(result.errors),
        advanced_open=any(e.field in ADVANCED_FIELDS for e in result.errors),
        max_posts=MAX_POSTS,
        placeholder_pages=PLACEHOLDER_TEXT,
    )


def _outcome_page(raw: RawForm, outcome: Outcome) -> str:
    if not outcome.built:
        return _form_page(raw, outcome.check)
    groups = problems.group_by_post(outcome.problems, outcome.check.posts)
    return templates.get_template("results.html").render(
        spec=outcome.check.spec,
        url=outcome.url,
        groups=groups,
        kinds=[problems.kind(code) for code in dict.fromkeys(p.kind for p in outcome.problems)],
        counts={code: sum(p.kind == code for p in outcome.problems) for code in {p.kind for p in outcome.problems}},
        kind=problems.kind,
    )


def _announce(step: Progress) -> bool:
    """Whether screen readers hear this step: each stage's start and end, and every tenth post."""
    return step.done in (1, step.total) or step.done % 10 == 0


def _stream(raw: RawForm) -> Iterator[str]:
    """Progress lines, then the final page, as newline-delimited JSON.

    The build runs in its own thread so a closed tab doesn't stop it: it
    finishes and saves the book, which nobody then downloads.
    """
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            events.put(("page", _outcome_page(raw, run(raw, lambda step: events.put(("progress", step))))))
        except Exception:  # rendering failed; run() itself never raises
            events.put(("page", _form_page(raw, CheckResult(spec=None, errors=[]))))
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


@app.get("/", response_class=HTMLResponse)
async def form() -> HTMLResponse:
    # The server's date, which on Vercel is UTC; see #29.
    raw = RawForm(pub_date=date.today().isoformat())
    return HTMLResponse(_form_page(raw, CheckResult(spec=None)))


@app.post("/")
async def submit(
    request: Request,
    posts: str = Form(""),
    title: str = Form(""),
    subtitle: str = Form(""),
    editor: str = Form(""),
    cover: UploadFile | None = File(None),
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
    has_cover = cover is not None and bool(cover.filename)
    raw = RawForm(
        posts=posts, title=title, subtitle=subtitle, editor=editor,
        cover_bytes=(await cover.read() or None) if has_cover else None,
        cover_filename=cover.filename if has_cover else "",
        series=series, series_number=series_number, publisher=publisher, description=description,
        pub_date=pub_date, lang=lang or "en", isbn=isbn, issn=issn, rights=rights,
        image_max_width=image_max_width, image_quality=image_quality,
        keep_original_images=keep_original_images is not None,
        placeholders=placeholders is not None,
        sort_names=sort_names, alt_text=alt_text,
    )
    if STREAM_TYPE in request.headers.get("accept", ""):
        return StreamingResponse(_stream(raw), media_type=STREAM_TYPE)
    outcome = await run_in_threadpool(run, raw)
    return HTMLResponse(_outcome_page(raw, outcome))


@app.get(storage.LOCAL_URL_PREFIX + "/{token}/{name}")
async def local_book(token: str, name: str) -> FileResponse:
    path = storage.local_book(f"{token}/{name}")
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type=storage.EPUB_TYPE, filename=name)
