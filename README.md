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

| Section | Option | If omitted |
|---|---|---|
| Cover | `--cover` | no cover |
| Copyright | `--copyright-file` | placeholder page |
| Imprint | `--imprint-file` | placeholder page |
| Foreword | `--foreword-file` | placeholder page |
| Introduction | `--intro-file` | skipped |
| *Posts, in the order given* | | |
| Notes | `--notes-file` | placeholder page |
| Acknowledgements | `--acknowledgements-file` | placeholder page |
| About This Book | `--about-file` | placeholder page |

Content files are HTML fragments, inserted as-is (include your own `<h1>`). The introduction also accepts plain text, one paragraph per line. In a manifest, use the same names with underscores (`foreword_file`, `about_file`, …) under `[content]`.

### Images

By default, post images are scaled down to at most 1100px wide and re-encoded as JPEG at quality 85, which typically shrinks a book several-fold with no visible loss. Images with real transparency stay PNG (fully transparent borders are trimmed first), animated GIFs and SVGs pass through untouched, and an image is left as-is when re-encoding wouldn't make it smaller.

| Option | Manifest `[images]` | Default |
|---|---|---|
| `--image-max-width N` | `max_width` | `1100` (`0` = never resize) |
| `--image-quality N` | `quality` | `85` |
| `--keep-original-images` | `optimize = false` | off |

The cover image is handled separately and isn't affected by these options.

## Extending phoenix-ebook

```
build_epub.py            # CLI
phoenix_ebook/
├── platforms/           # post sources (Ghost today)
├── processors/          # per-site HTML cleanup
├── epub_builder.py      # EPUB assembly
├── images.py            # image resizing and re-encoding
├── models.py            # Post, BookSpec, SourceSpec
└── secrets.py           # API key storage
```

- **New platform:** subclass `Platform` in `phoenix_ebook/platforms/`, decorate it with `@register_platform`, and import the module in `phoenix_ebook/platforms/__init__.py`.
- **New site processor:** subclass `HtmlProcessor` in `phoenix_ebook/processors/`, decorate it with `@register_processor`, and import the module in `phoenix_ebook/processors/__init__.py`. See `flaminghydra.py` for an example.

## License

[MIT](LICENSE)
