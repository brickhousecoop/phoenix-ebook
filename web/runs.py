"""One submission, start to finish: checks, build, storage, with progress along the way."""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from phoenix_ebook.epub_builder import build
from phoenix_ebook.errors import PhoenixError
from phoenix_ebook.models import BuildProblem, Progress
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.processors.base import get_processor

from web.forms import CheckResult, FormError, RawForm, check_submission
from web.storage import load_upload, new_folder, save_book, save_upload

log = logging.getLogger(__name__)

BUG_MESSAGE = ("Something went wrong that isn't your fault: it's a bug in phoenix-ebook. "
               "Try again; if it happens again, please report it.")


@dataclass
class Outcome:
    """How a submission ended: ``check.errors`` to show on the form, or a book at ``url``."""

    check: CheckResult
    url: str | None = None
    problems: list[BuildProblem] = field(default_factory=list)
    kept_cover: str = ""  # the cover, kept next to the book for a rebuild

    @property
    def built(self) -> bool:
        return self.url is not None


def describe(step: Progress) -> str:
    """The progress line shown while building."""
    title = f": {step.title}" if step.title else ""
    if step.stage == "fetch":
        return f"Fetching post {step.done} of {step.total}{title}"
    if step.stage == "post":
        return f"Downloading images for post {step.done} of {step.total}{title}"
    if step.stage == "write":
        return "Putting the book together"
    return "Saving the book"


def run(raw: RawForm, progress: Callable[[Progress], None] = lambda step: None,
        session: requests.Session | None = None) -> Outcome:
    """Check ``raw`` and, if it passes, build and store the book.

    Never raises: a failure part-way ends with its message on the form, which
    is refilled from ``raw``. The uploaded cover lives in a temporary file for
    the whole run and is deleted at the end, however the run ends.
    """
    session = session or requests.Session()
    cover_tmp = None
    try:
        if not raw.cover_bytes and raw.kept_cover:
            kept = load_upload(raw.kept_cover)
            if kept is None:
                return Outcome(CheckResult(spec=None, errors=[FormError(
                    "cover", "the cover from the last build couldn't be found; choose it again")]))
            raw.cover_bytes, raw.cover_filename = kept
        if raw.cover_bytes:
            fd, cover_tmp = tempfile.mkstemp(suffix=Path(raw.cover_filename).suffix or ".bin")
            with os.fdopen(fd, "wb") as f:
                f.write(raw.cover_bytes)
            raw.cover_path = cover_tmp

        check = check_submission(raw, session, progress)
        if not check.ok:
            return Outcome(check)
        return _build_and_store(check, session, progress, cover_tmp)
    except PhoenixError as exc:
        return Outcome(CheckResult(spec=None, errors=[FormError(exc.field, str(exc))]))
    except Exception:
        log.exception("build failed")
        return Outcome(CheckResult(spec=None, errors=[FormError(None, BUG_MESSAGE)]))
    finally:
        raw.cover_path = None
        if cover_tmp:
            os.unlink(cover_tmp)


def _build_and_store(check: CheckResult, session: requests.Session,
                     progress: Callable[[Progress], None], cover_path: str | None) -> Outcome:
    spec, source = check.spec, check.spec.source
    with tempfile.TemporaryDirectory() as tmp:
        spec.output = str(Path(tmp) / "book.epub")
        result = build(spec, check.posts, get_processor(source.processor), session=session,
                       image_base_url=source.url, platform=get_platform(source.platform), progress=progress)
        progress(Progress("save", 1, 1))
        folder = new_folder()
        try:
            url = save_book(result.path, spec.title, folder)
            kept_cover = save_upload(cover_path, "cover" + Path(cover_path).suffix, folder) if cover_path else ""
        except Exception as exc:
            log.exception("saving the book failed")
            raise PhoenixError(f"The book was built but couldn't be saved for download ({exc}). Try again.") from exc
    return Outcome(check, url=url, problems=result.problems, kept_cover=kept_cover)
