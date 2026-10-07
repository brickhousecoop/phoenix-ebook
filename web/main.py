"""The book-building website: a form, pre-build checks, nothing else yet (see ADR 0002).

Run locally with ``uvicorn web.main:app --reload`` from the repo root.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run

from web.forms import MAX_POSTS, CheckResult, RawForm, check_submission

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

ADVANCED_FIELDS = {
    "series", "series_number", "publisher", "description", "pub_date", "lang", "isbn", "issn",
    "rights", "image_max_width", "image_quality", "keep_original_images", "placeholders",
    "sort_names", "alt_text",
}


def _render(request: Request, *, raw: RawForm, result: CheckResult, submitted: bool) -> HTMLResponse:
    advanced_open = any(e.field in ADVANCED_FIELDS for e in result.errors)
    return templates.TemplateResponse(request, "index.html", {
        "raw": raw,
        "result": result,
        "submitted": submitted,
        "advanced_open": advanced_open,
        "max_posts": MAX_POSTS,
    })


@app.get("/", response_class=HTMLResponse)
async def form(request: Request) -> HTMLResponse:
    return _render(request, raw=RawForm(), result=CheckResult(spec=None), submitted=False)


@app.post("/", response_class=HTMLResponse)
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
) -> HTMLResponse:
    cover_bytes: bytes | None = None
    cover_path: str | None = None
    if cover is not None and cover.filename:
        cover_bytes = await cover.read()

    tmp_handle = None
    try:
        if cover_bytes:
            suffix = Path(cover.filename).suffix or ".bin"
            tmp_handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp_handle.write(cover_bytes)
            tmp_handle.close()
            cover_path = tmp_handle.name

        raw = RawForm(
            posts=posts, title=title, subtitle=subtitle, editor=editor,
            cover_path=cover_path, cover_bytes=cover_bytes,
            series=series, series_number=series_number, publisher=publisher, description=description,
            pub_date=pub_date, lang=lang or "en", isbn=isbn, issn=issn, rights=rights,
            image_max_width=image_max_width, image_quality=image_quality,
            keep_original_images=keep_original_images is not None,
            placeholders=placeholders is not None,
            sort_names=sort_names, alt_text=alt_text,
        )
        result = check_submission(raw)
        return _render(request, raw=raw, result=result, submitted=True)
    finally:
        if tmp_handle is not None:
            os.unlink(tmp_handle.name)
