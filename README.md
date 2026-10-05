# phoenix-ebook

Turn a publication's posts into a clean, ready-to-read EPUB.

phoenix-ebook fetches posts from a publishing platform, cleans up each site's HTML quirks, downloads and embeds every image, and assembles a proper ebook with cover, front matter, chapters, back matter, and a table of contents.

## Supported sites

| Site | Platform | Processor |
|---|---|---|
| [Flaming Hydra](https://flaminghydra.com) | Ghost | `flaminghydra` |
| Any other Ghost site | Ghost | `generic` |

More are on the way: more platforms, more site-specific processors and, eventually, a web UI for building books without the command line.

## Requirements

- Python 3.11+
- A Ghost **Admin API key** for the site whose posts you want to collect. In Ghost Admin, go to **Settings → Integrations → Add custom integration** and copy the *Admin API key* (it looks like `id:secret`).

## Setup

```bash
git clone https://github.com/buffystruggles/phoenix-ebook.git
cd phoenix-ebook

# with uv (recommended)
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt

# or with plain venv/pip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Store your API key

Store the key once per site; you'll be prompted for it without echo:

```bash
.venv/bin/python build_epub.py --set-secret --platform ghost --url https://flaminghydra.ghost.io
```

`--set-secret` uses your OS keyring, falling back to a file. On machines without a keyring (containers, servers), use `--set-secret-file`, which writes `~/.phoenix_secrets.json` with owner-only permissions.

phoenix-ebook looks for a key in this order:

1. `--admin-key` on the command line
2. Environment variable `PHOENIX_SECRET_<PLATFORM>_<DOMAIN>`, e.g. `PHOENIX_SECRET_GHOST_FLAMINGHYDRA_GHOST_IO`
3. `~/.phoenix_secrets.json`
4. OS keyring (service `phoenix:<platform>`, username `<domain>`)

## Usage

### Command line

Pass the site URL, book metadata, and the post slugs in reading order:

```bash
.venv/bin/python build_epub.py --url https://flaminghydra.ghost.io \
  --title "Flaming Hydra Collection" \
  --author "Flaming Hydra Editors" \
  --cover cover.jpg \
  --output flaming-hydra.epub \
  1940-for-some fender-bender vigil-vacancy
```

A post's slug is the last part of its URL: `https://flaminghydra.com/fender-bender/` → `fender-bender`.

The processor is picked from the URL automatically; override it with `--processor`. Run `--help` for every option.

### Manifest file

For books you rebuild or tweak often, describe the book in TOML and pass `--manifest`:

```toml
posts = [
  "1940-for-some",
  "fender-bender",
  "vigil-vacancy",
]

[book]
title = "Flaming Hydra Collection"
author = "Flaming Hydra Editors"
publisher = "Flaming Hydra Press"
description = "A collection of essays."
pub_date = "2026-09-29"
lang = "en"
output = "flaming-hydra.epub"

[source]
platform = "ghost"
url = "https://flaminghydra.ghost.io"
# processor = "flaminghydra"   # optional; inferred from the URL

[content]
cover = "cover.jpg"
foreword_file = "foreword.html"

[images]              # optional; these are the defaults
max_width = 1100
quality = 85
# optimize = false    # embed images exactly as downloaded
```

`posts` must come before the first `[table]` header — TOML assigns any key after a header to that table.

```bash
.venv/bin/python build_epub.py --manifest book.toml
```

### Book structure

Pages appear in this order:

| Page | Option | If omitted |
|---|---|---|
| Title page | — (from `--title`, `--author`, `--publisher`) | always generated |
| Copyright | `--copyright-file` | placeholder page |
| Imprint | `--imprint-file` | placeholder page |
| Contents | — | always generated |
| Foreword | `--foreword-file` | placeholder page |
| Introduction | `--intro-file` | left out |
| Half-title page (the title alone, as a divider) | — | left out if the book has no copyright, imprint, foreword or introduction |
| *Chapters: one per post, in the order given* | | |
| Notes | `--notes-file` | placeholder page |
| Acknowledgements | `--acknowledgements-file` | placeholder page |
| About This Book | `--about-file` | placeholder page |

Placeholder pages hold bracketed stand-in text so you can see where a section goes. For a finished book, pass `--no-placeholders` (manifest: `placeholders = false` under `[content]`) and sections without a file are left out instead.

The cover (`--cover`) is the book's cover image in reading apps and libraries; it isn't repeated as a page inside the book, which some stores (notably Kindle) warn against. If a content file or the cover doesn't exist, the build stops with an error naming the option, before fetching anything.

Each chapter opens with a header: the post's publication date (in the site's timezone), its title, a "By …" byline linking to each author's page, and the post's feature image with its caption. The contents page lists each chapter by its title; a site's processor can add more (Flaming Hydra books show "Title — Author").

Content files are HTML fragments, inserted as-is; start them with an `<h2>` heading (the title page holds the book's `<h1>`). The introduction also accepts plain text, one paragraph per line. In a manifest, use the same names with underscores (`foreword_file`, `about_file`, …) under `[content]`.

### Images

By default, post images are scaled down to at most 1100px wide and re-encoded as JPEG at quality 85, which typically shrinks a book several-fold with no visible loss. Images with real transparency stay PNG (fully transparent borders are trimmed first), animated GIFs and SVGs pass through untouched, and an image is left as-is when re-encoding wouldn't make it smaller.

| Option | Manifest `[images]` | Default |
|---|---|---|
| `--image-max-width N` | `max_width` | `1100` (`0` = never resize) |
| `--image-quality N` | `quality` | `85` |
| `--keep-original-images` | `optimize = false` | off |

The cover image is handled separately and isn't affected by these options.

If an image can't be downloaded (network error, HTTP error, or a response that isn't actually an image), it is left out of the book and the build still succeeds; each one is listed as a warning after the build. Temporary failures (timeouts, connection errors, HTTP 5xx/429) are retried once.

### Styling

Books use [Standard Ebooks](https://standardebooks.org)' `core.css` (a public-domain stylesheet tuned for reading apps) plus phoenix-ebook's own styles for its pages: a centered title page, chapter headers with the date and byline in small caps, figures kept on one screen together with their captions, and book-style indented paragraphs. No font is set, so the reader's own font and size settings always apply.

To adjust the look of a book, add your own stylesheet; its rules are applied last, so they win:

```bash
.venv/bin/python build_epub.py --css my-styles.css …
```

In a manifest: `css = "my-styles.css"` under a `[style]` table.

## Extending phoenix-ebook

```
build_epub.py            # CLI
phoenix_ebook/
├── platforms/           # post sources (Ghost today)
├── processors/          # per-site HTML cleanup
├── epub_builder.py      # EPUB assembly
├── images.py            # image fetching, validation and optimization
├── models.py            # Post, Author, BookSpec, SourceSpec, BuildResult, BuildProblem
└── secrets.py           # API key storage
```

**New platform.** Subclass `Platform` in `phoenix_ebook/platforms/`, set `name`, decorate it with `@register_platform`, and import the module in `phoenix_ebook/platforms/__init__.py`. `fetch_post()` returns a `Post`: the body HTML only, authors as `Author(name, url)`, `published_at` in the site's own timezone, and the feature image fields if the platform has them. The docstring on `Platform.fetch_post` has the full contract.

**New site processor.** Subclass `HtmlProcessor` in `phoenix_ebook/processors/`, set `name`, decorate it with `@register_processor`, and import the module in `phoenix_ebook/processors/__init__.py`. Overrides of `clean()` **must call `super().clean()` first**; that is where all the shared cleanup happens. See `flaminghydra.py` for an example.

### Using it as a library

```python
import requests
from phoenix_ebook.epub_builder import build
from phoenix_ebook.models import BookSpec
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.processors.base import get_processor
import phoenix_ebook.platforms, phoenix_ebook.processors  # register built-ins

session = requests.Session()
ghost = get_platform("ghost")
posts = [ghost.fetch_post(session, "https://flaminghydra.ghost.io", admin_key, slug)
         for slug in ["fender-bender", "vigil-vacancy"]]

result = build(BookSpec(title="My Book", output="book.epub"), posts,
               get_processor("flaminghydra"), session=session,
               image_base_url="https://flaminghydra.ghost.io")
print(result.path)
for problem in result.problems:   # e.g. images that couldn't be downloaded
    print(problem.kind, problem.post_slug, problem.url, problem.detail)
```

`build()` never fails because of an image; those come back in `result.problems`.

## Development

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt   # adds pytest
.venv/bin/python scripts/fetch_epubcheck.py                         # optional: pinned epubcheck into .tools/
.venv/bin/python -m pytest
```

The suite runs offline (no network, no API key). The epubcheck tests need Java and the downloaded validator; without them they are skipped, not failed.

## Changes

See [CHANGELOG.md](CHANGELOG.md) for changes that affect how books come out.

## License

[MIT](LICENSE)
