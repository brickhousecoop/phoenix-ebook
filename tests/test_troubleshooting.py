"""docs/troubleshooting.md covers every message a build can print (#15)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import build_epub
from conftest import REPO
from phoenix_ebook import errors
from phoenix_ebook.models import BuildProblem

DOC = (REPO / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
SOURCES = sorted((REPO / "phoenix_ebook").rglob("*.py")) + [REPO / "build_epub.py"]


def _error_classes() -> set[str]:
    found, todo = set(), [errors.PhoenixError]
    while todo:
        cls = todo.pop()
        found.add(cls.__name__)
        todo.extend(cls.__subclasses__())
    return found


def _normalize(text: str) -> str:
    """Lower case, without Markdown and punctuation that differ between code and prose."""
    return " ".join(re.sub(r"[`*\[\]()<>'\";:,.!?…]", " ", text).lower().split())


NORMALIZED_DOC = _normalize(DOC)

# Messages that can't appear on the supported Python (tomllib is always there on 3.14).
UNREACHABLE = {"TOML manifests need Python 3.11 or newer"}


def _problem_kinds() -> set[str]:
    kinds = set()
    for path in SOURCES:
        kinds |= set(re.findall(r'BuildProblem\(\s*kind="([a-z-]+)"', path.read_text(encoding="utf-8")))
    return kinds


def _error_messages() -> list[tuple[str, str]]:
    """(file:line, message text) for every PhoenixError subclass constructed with a message."""
    classes = _error_classes()
    messages = []
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in classes
                    and node.args):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                chunks = [arg.value]
            elif isinstance(arg, ast.JoinedStr):
                chunks = [v.value for v in arg.values if isinstance(v, ast.Constant)]
            elif isinstance(arg, ast.BinOp):  # "a" + (b if c else "") …: take the literal parts
                parts = [n for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                chunks = [n.value for n in sorted(parts, key=lambda n: (n.lineno, n.col_offset))]  # source order
            else:
                continue
            messages.append((f"{path.name}:{node.lineno}", chunks))
    return messages


def test_problem_kinds_found():
    assert {"image-download-failed", "image-missing-alt", "link-repaired", "embed-removed"} <= _problem_kinds()


@pytest.mark.parametrize("kind", sorted(_problem_kinds()))
def test_every_warning_kind_has_an_entry(kind):
    assert f"### `{kind}`" in DOC, f"add a '### `{kind}`' entry to docs/troubleshooting.md"


@pytest.mark.parametrize("where, chunks", _error_messages(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_error_message_is_explained(where, chunks):
    text = " ".join(chunks)
    if any(u in text for u in UNREACHABLE):
        pytest.skip("unreachable on Python 3.14")
    # The message's first words (its first literal part with two or more): what a user searches the page for.
    start = next((_normalize(c) for c in chunks if len(_normalize(c).split()) >= 2), "")
    probe = " ".join(start.split()[:3])
    assert probe and probe in NORMALIZED_DOC, f"{where}: no entry for a message starting {start!r}"


def test_post_not_found_is_explained():
    message = str(errors.PostNotFound("example.ghost.io", ["a-slug"]))
    assert "post not found on" in message and "post not found on" in NORMALIZED_DOC


def test_every_summary_line_is_quoted(capsys):
    """Each warning's summary line (as printed) appears on the page, so users can search for it."""
    for kind in sorted(_problem_kinds()):
        build_epub._report_problems([BuildProblem(kind, "p", "https://x", "d")] * 2)
        summary = [line for line in capsys.readouterr().err.splitlines() if line.startswith("2 ")]
        assert summary, f"{kind}: no summary line"
        assert _normalize(summary[0].replace("2 ", "N ", 1)) in NORMALIZED_DOC, summary[0]


def test_report_points_to_the_page_after_the_summary(capsys):
    build_epub._report_problems([BuildProblem("link-repaired", "p", "#a", "now #b (matched)")])
    lines = capsys.readouterr().err.strip().splitlines()
    assert lines[-1] == f"What these warnings mean: {build_epub.TROUBLESHOOTING_URL}"


def test_pointer_comes_before_the_alt_text_block(capsys):
    build_epub._report_problems([BuildProblem("image-missing-alt", "p", "https://x/a.png", "no alt text", "image 1 of 1")])
    err = capsys.readouterr().err
    assert err.index("What these warnings mean:") < err.index("# [alt_text]")
    assert build_epub.TROUBLESHOOTING_URL.endswith("/docs/troubleshooting.md")


def test_no_pointer_without_warnings(capsys):
    build_epub._report_problems([])
    assert capsys.readouterr().err == ""


def test_readme_links_to_the_page():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/troubleshooting.md" in readme
    assert "i.vimeocdn.com" not in readme  # the host list lives on the troubleshooting page only
    assert Path(REPO / "docs" / "troubleshooting.md").exists()
