"""CLI flags and TOML manifests keep working (back-compat is a project rule)."""
from __future__ import annotations

import re

import pytest

import build_epub
from conftest import REPO
from phoenix_ebook.secrets import SecretStore

SLUG = ["some-post"]


def spec_for(argv: list[str]):
    return build_epub._spec_from_args(build_epub.build_parser().parse_args(argv))


# Each flag that feeds BookSpec / SourceSpec, with the value it should produce.
SPEC_FLAGS = [
    ("--url", "https://other.test", lambda s: s.source.url == "https://other.test"),
    ("--platform", "ghost", lambda s: s.source.platform == "ghost"),
    ("--processor", "generic", lambda s: s.source.processor == "generic"),
    ("--title", "My Book", lambda s: s.title == "My Book"),
    ("--editor", "Ed", lambda s: s.editor == "Ed"),
    ("--author", "Ed", lambda s: s.editor == "Ed"),  # old name for --editor
    ("--subtitle", "Sub", lambda s: s.subtitle == "Sub"),
    ("--series", "Digest", lambda s: s.series == "Digest"),
    ("--series-number", "367", lambda s: s.series_number == "367"),
    ("--rights", "© P", lambda s: s.rights == "© P"),
    ("--issn", "0317-8471", lambda s: s.issn == "0317-8471"),
    ("--publisher", "Pub", lambda s: s.publisher == "Pub"),
    ("--description", "Desc", lambda s: s.description == "Desc"),
    ("--pub-date", "2026-09-29", lambda s: s.pub_date == "2026-09-29"),
    ("--isbn", "9781234567897", lambda s: s.isbn == "9781234567897"),
    ("--lang", "en-US", lambda s: s.lang == "en-US"),
    ("--cover", "c.jpg", lambda s: s.cover == "c.jpg"),
    ("--copyright-file", "c.html", lambda s: s.copyright_file == "c.html"),
    ("--imprint-file", "i.html", lambda s: s.imprint_file == "i.html"),
    ("--foreword-file", "f.html", lambda s: s.foreword_file == "f.html"),
    ("--intro-file", "in.txt", lambda s: s.intro_file == "in.txt"),
    ("--notes-file", "n.html", lambda s: s.notes_file == "n.html"),
    ("--acknowledgements-file", "a.html", lambda s: s.acknowledgements_file == "a.html"),
    ("--about-file", "ab.html", lambda s: s.about_file == "ab.html"),
    ("--no-placeholders", None, lambda s: s.placeholders is False),
    ("--css", "extra.css", lambda s: s.css == "extra.css"),
    ("--image-max-width", "600", lambda s: s.image_max_width == 600),
    ("--image-quality", "70", lambda s: s.image_quality == 70),
    ("--keep-original-images", None, lambda s: s.optimize_images is False),
    ("--output", "out.epub", lambda s: s.output == "out.epub"),
]

# Flags handled by main() rather than mapped into the spec; each has a test below.
MAIN_FLAGS = {"--set-secret", "--set-secret-file", "--domain", "--admin-key", "--manifest", "--debug"}


@pytest.mark.parametrize("flag, value, check", SPEC_FLAGS, ids=[f[0] for f in SPEC_FLAGS])
def test_flag_maps_into_spec(flag, value, check):
    argv = [flag] + ([value] if value is not None else []) + SLUG
    assert check(spec_for(argv))


def test_every_cli_flag_is_covered():
    parser_flags = {
        opt for action in build_epub.build_parser()._actions
        for opt in action.option_strings if opt.startswith("--") and opt != "--help"
    }
    tested = {f[0] for f in SPEC_FLAGS} | MAIN_FLAGS
    assert parser_flags == tested, f"untested: {parser_flags - tested}, stale: {tested - parser_flags}"


def test_defaults():
    spec = spec_for(SLUG)
    assert spec.source.url == "https://flaminghydra.ghost.io"
    assert spec.source.platform == "ghost" and spec.source.processor == "flaminghydra"
    assert spec.source.slugs == SLUG
    assert (spec.title, spec.lang, spec.output) == ("Collected Posts", "en", "book.epub")
    assert (spec.optimize_images, spec.image_max_width, spec.image_quality) == (True, 1100, 85)
    assert spec.placeholders is True


def test_processor_inferred_from_url():
    assert spec_for(["--url", "https://other.ghost.io"] + SLUG).source.processor == "generic"


# ---------------------------------------------------------------- main()

@pytest.fixture
def captured_build(monkeypatch):
    calls = []
    monkeypatch.setattr(build_epub, "_run_build", lambda spec, override_secret: calls.append((spec, override_secret)))
    return calls


@pytest.fixture
def captured_secret(monkeypatch):
    calls = []

    def fake_set(self, platform, domain, secret, *, prefer_keyring=True):
        calls.append((platform, domain, secret, prefer_keyring))
        return "test-store"

    monkeypatch.setattr(SecretStore, "set", fake_set)
    monkeypatch.setenv("PHOENIX_SET_SECRET_VALUE", "id:secret")
    return calls


def test_admin_key_is_passed_to_the_build(captured_build):
    build_epub.main(["--admin-key", "k:v"] + SLUG)
    [(spec, override)] = captured_build
    assert override == "k:v" and spec.source.slugs == SLUG


def test_no_slugs_and_no_manifest_is_an_error(captured_build):
    with pytest.raises(SystemExit):
        build_epub.main([])
    assert not captured_build


def test_set_secret_uses_keyring_and_url_host(captured_secret, captured_build):
    build_epub.main(["--set-secret", "--url", "https://site.ghost.io"])
    assert captured_secret == [("ghost", "site.ghost.io", "id:secret", True)]
    assert not captured_build


def test_set_secret_file_with_domain(captured_secret, captured_build):
    build_epub.main(["--set-secret-file", "--url", "", "--domain", "other.test"])
    assert captured_secret == [("ghost", "other.test", "id:secret", False)]


def test_manifest_flag_builds_from_manifest(tmp_path, captured_build):
    path = tmp_path / "book.toml"
    path.write_text('posts = ["a", "b"]\n[book]\ntitle = "From Manifest"\n'
                    '[source]\nplatform = "ghost"\nurl = "https://flaminghydra.ghost.io"\n')
    build_epub.main(["--manifest", str(path)])
    [(spec, _)] = captured_build
    assert spec.title == "From Manifest" and spec.source.slugs == ["a", "b"]


# ---------------------------------------------------------------- manifests

FULL_MANIFEST = """
posts = ["one", "two"]

[sort_names]
"Felipe De La Hoz" = "De La Hoz, Felipe"

[book]
title = "T"
subtitle = "S"
editor = "A"
series = "Digest"
series_number = 367
issn = "0317-8471"
rights = "R"
publisher = "P"
description = "D"
pub_date = "2026-09-29"
isbn = "978"
lang = "en-US"
output = "o.epub"

[source]
platform = "ghost"
url = "https://other.ghost.io"
processor = "flaminghydra"

[content]
cover = "c.jpg"
copyright_file = "c.html"
imprint_file = "i.html"
foreword_file = "f.html"
intro_file = "in.txt"
notes_file = "n.html"
acknowledgements_file = "a.html"
about_file = "ab.html"
placeholders = false

[style]
css = "extra.css"

[images]
optimize = false
max_width = 700
quality = 60
"""


def test_full_manifest(tmp_path):
    path = tmp_path / "book.toml"
    path.write_text(FULL_MANIFEST)
    s = build_epub._spec_from_manifest(str(path))
    assert s.source.slugs == ["one", "two"] and s.source.processor == "flaminghydra"
    assert (s.subtitle, s.series, s.series_number, s.rights, s.issn) == ("S", "Digest", "367", "R", "0317-8471")
    assert s.sort_names == {"Felipe De La Hoz": "De La Hoz, Felipe"}
    assert (s.title, s.editor, s.publisher, s.description, s.pub_date, s.isbn, s.lang, s.output) == \
        ("T", "A", "P", "D", "2026-09-29", "978", "en-US", "o.epub")
    assert (s.cover, s.copyright_file, s.imprint_file, s.foreword_file, s.intro_file,
            s.notes_file, s.acknowledgements_file, s.about_file) == \
        ("c.jpg", "c.html", "i.html", "f.html", "in.txt", "n.html", "a.html", "ab.html")
    assert (s.optimize_images, s.image_max_width, s.image_quality) == (False, 700, 60)
    assert s.placeholders is False
    assert s.css == "extra.css"


def test_slugs_under_source_also_accepted(tmp_path):
    path = tmp_path / "book.toml"
    path.write_text('[source]\nplatform = "ghost"\nurl = "https://x.ghost.io"\nslugs = ["s"]\n')
    s = build_epub._spec_from_manifest(str(path))
    assert s.source.slugs == ["s"] and s.source.processor == "generic"
    assert (s.optimize_images, s.image_max_width, s.image_quality) == (True, 1100, 85)


def test_readme_manifest_example_parses(tmp_path):
    readme = (REPO / "README.md").read_text()
    example = re.search(r"```toml\n(.*?)```", readme, re.S).group(1)
    path = tmp_path / "book.toml"
    path.write_text(example)
    s = build_epub._spec_from_manifest(str(path))
    assert s.source.slugs and s.title
