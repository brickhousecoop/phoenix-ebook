"""Deploying the website to Vercel (#27): config files and the storage safeguard."""
from __future__ import annotations

import json
import re
import tomllib

from fastapi.testclient import TestClient

from conftest import REPO
from web import storage
from web.main import app


def _requirements(name: str) -> set[str]:
    """Package names in a requirements file, following ``-r`` includes."""
    names = set()
    for line in (REPO / name).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("-r "):
            names |= _requirements(line[3:].strip())
        elif line:
            names.add(re.split(r"[<>=!~ ]", line)[0].lower())
    return names


def test_pyproject_lists_the_websites_requirements():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())
    listed = {re.split(r"[<>=!~ ]", dep)[0].lower() for dep in project["project"]["dependencies"]}
    assert listed == _requirements("requirements-web.txt")
    assert project["project"]["requires-python"] == ">=3.14"
    assert project["tool"]["vercel"]["entrypoint"] == "web.main:app"


def test_vercel_config():
    config = json.loads((REPO / "vercel.json").read_text())
    function = config["functions"]["web/main.py"]
    assert function["maxDuration"] == 800
    assert "tests/**" in function["excludeFiles"]


def test_upload_list_is_an_allow_list():
    """Only the site's own folders are uploaded; local notes, keys and test books never are."""
    lines = [l for l in (REPO / ".vercelignore").read_text().splitlines() if l and not l.startswith("#")]
    assert lines[0] == "/*"
    assert {l for l in lines if l.startswith("!")} == {
        "!/web", "!/phoenix_ebook", "!/docs", "!/pyproject.toml", "!/vercel.json"}


def test_on_vercel_without_blob_the_site_says_so_instead_of_building(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path / "books")
    page = TestClient(app).post("/", data={"posts": "anything"}).text
    assert "isn&#39;t connected to its Blob store" in page or "isn't connected to its Blob store" in page
    assert not (tmp_path / "books").exists()


def test_locally_and_with_blob_there_is_no_setup_problem(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    assert storage.setup_problem() is None
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv(storage.BLOB_TOKEN_ENV, "vercel_blob_rw_x")
    assert storage.setup_problem() is None
