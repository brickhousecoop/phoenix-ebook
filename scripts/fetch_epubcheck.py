#!/usr/bin/env python3
"""Download the pinned epubcheck release into .tools/ (gitignored) for the test suite."""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import requests

VERSION = "5.4.0"
URL = f"https://github.com/w3c/epubcheck/releases/download/v{VERSION}/epubcheck-{VERSION}.zip"
TOOLS = Path(__file__).resolve().parent.parent / ".tools"
JAR = TOOLS / f"epubcheck-{VERSION}" / "epubcheck.jar"


def main() -> None:
    if JAR.exists():
        print(f"already present: {JAR}")
        return
    resp = requests.get(URL, timeout=120)
    resp.raise_for_status()
    TOOLS.mkdir(exist_ok=True)
    zipfile.ZipFile(io.BytesIO(resp.content)).extractall(TOOLS)
    if not JAR.exists():
        sys.exit(f"downloaded {URL} but {JAR} is missing")
    print(f"installed: {JAR}")


if __name__ == "__main__":
    main()
