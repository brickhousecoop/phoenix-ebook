"""One submission, start to finish: checks, build, storage, with progress along the way."""
from __future__ import annotations

import logging
import re
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

from web.forms import UPLOAD_FIELDS, CheckResult, FormError, RawForm, check_submission
from web.storage import load_upload, new_folder, save_book, save_upload, setup_problem

log = logging.getLogger(__name__)

BUG_MESSAGE = ("Something went wrong that isn't your fault: it's a bug in phoenix-ebook. "
               "Try again; if it happens again, please report it.")


@dataclass
class Outcome:
    """How a submission ended: ``check.errors`` to show on the form, or a book at ``url``."""

    check: CheckResult
    url: str | None = None
    problems: list[BuildProblem] = field(default_factory=list)
    kept: dict[str, str] = field(default_factory=dict)  # BookSpec field -> its upload, kept next to the book

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
    is refilled from ``raw``. Uploads (new, or kept from the last build) live in
    a temporary folder for the whole run, deleted at the end however it ends.
    """
    if problem := setup_problem():
        return Outcome(CheckResult(spec=None, errors=[FormError(None, problem)]))
    session = session or requests.Session()
    try:
        with tempfile.TemporaryDirectory() as uploads:
            for field_name, ref in raw.kept.items():
                if field_name in raw.files:  # a new upload replaces the kept one
                    continue
                if (kept := load_upload(ref)) is None:
                    return Outcome(CheckResult(spec=None, errors=[FormError(
                        field_name, f"{UPLOAD_FIELDS[field_name]}: the file from the last build couldn't be found; "
                                    "choose it again")]))
                raw.files[field_name] = kept
            for field_name, (data, name) in raw.files.items():
                path = Path(uploads) / field_name / safe_name(name)
                path.parent.mkdir()
                path.write_bytes(data)
                raw.paths[field_name] = str(path)

            check = check_submission(raw, session, progress)
            if not check.ok:
                # Keep the uploads that were fine, so the refilled form can offer them again.
                failed = {e.field for e in check.errors}
                return Outcome(check, kept=_keep({k: v for k, v in raw.paths.items() if k not in failed},
                                                 new_folder()))
            return _build_and_store(check, session, progress, raw.paths)
    except PhoenixError as exc:
        return Outcome(CheckResult(spec=None, errors=[FormError(exc.field, str(exc))]))
    except Exception:
        log.exception("build failed")
        return Outcome(CheckResult(spec=None, errors=[FormError(None, BUG_MESSAGE)]))
    finally:
        raw.paths = {}


def safe_name(name: str) -> str:
    """An uploaded file's name, safe to use as a file name: 'My Foreword (v2).docx' -> 'My-Foreword-v2-.docx'."""
    stem, suffix = Path(Path(name).name).stem, Path(name).suffix.lower()
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")[:60] or "file") + re.sub(r"[^a-z0-9.]", "", suffix)


def _build_and_store(check: CheckResult, session: requests.Session, progress: Callable[[Progress], None],
                     uploads: dict[str, str]) -> Outcome:
    spec, source = check.spec, check.spec.source
    with tempfile.TemporaryDirectory() as tmp:
        spec.output = str(Path(tmp) / "book.epub")
        result = build(spec, check.posts, get_processor(source.processor), session=session,
                       image_base_url=source.url, platform=get_platform(source.platform), progress=progress)
        progress(Progress("save", 1, 1))
        folder = new_folder()
        try:
            url = save_book(result.path, spec.title, folder)
            kept = _keep(uploads, folder)
        except Exception as exc:
            log.exception("saving the book failed")
            raise PhoenixError(f"The book was built but couldn't be saved for download ({exc}). Try again.") from exc
    return Outcome(check, url=url, problems=result.problems, kept=kept)


def _keep(paths: dict[str, str], folder: str) -> dict[str, str]:
    """Store uploads (BookSpec field -> file) in ``folder`` for the next submission."""
    return {name: save_upload(path, f"{name}/{Path(path).name}", folder) for name, path in paths.items()}
