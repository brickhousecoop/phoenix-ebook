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

- Python 3.14 (the version phoenix-ebook is developed and tested on)
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

The key is stored under the URL's host name; use `--domain` to give the host explicitly instead. `--set-secret` uses your OS keyring, falling back to a file. On machines without a keyring (containers, servers), use `--set-secret-file`, which writes `~/.phoenix_secrets.json` with owner-only permissions.

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
  --title "Flaming Hydra" --subtitle "August 2026" \
  --editor "Flaming Hydra Editors" \
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

[sort_names]          # optional: fix sort names the automatic rule gets wrong
"Felipe De La Hoz" = "De La Hoz, Felipe"

[book]
title = "Flaming Hydra"
subtitle = "August 2026"
editor = "Flaming Hydra Editors"
series = "Flaming Hydra Digest"
series_number = 368
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

### Credits and metadata

| Option | Manifest `[book]` | What it does |
|---|---|---|
| `--editor` (old name: `--author`) | `editor` (or `author`) | Who compiled the book. Shown on the title page and as the book's creator in reading apps. |
| `--subtitle` | `subtitle` | Shown under the title on the title page and half-title page. |
| `--series`, `--series-number` | `series`, `series_number` | E.g. "Flaming Hydra Digest", 368. Shown on the title page; lets apps group the books. |
| `--rights` | `rights` | Rights statement in the book's metadata. Left out if not given. |
| `--description` | `description` | The book's description, shown in reading apps and catalogues. |
| `--pub-date` | `pub_date` | Publication date, e.g. `2026-09-29`. |
| `--lang` | `lang` | Language code (default `en`; e.g. `en-US`). |
| `--isbn` | `isbn` | This book's ISBN (one per book and format), stored as a standard `urn:isbn:` identifier. |
| `--issn` | `issn` | The series' ISSN (one number for all issues; needs `--series`). Shown with the series on the title page. |

ISBNs and ISSNs are checked (including the check digit) before anything is fetched, so a typo stops the build instead of going into the book.

Each post's author is credited in the book's metadata as a **contributor**, so library catalogues can find the book by any of its writers while the shelf shows the editor. Without `--editor`, the post authors are credited as the book's authors instead. Sort names ("Connor, J.D.") are derived automatically; fix any the rule gets wrong in a `[sort_names]` table in the manifest.

### Book structure

Pages appear in this order:

| Page | Option | If omitted |
|---|---|---|
| Title page | — (from `--title`, `--subtitle`, `--series`, `--editor`, `--publisher`) | always generated |
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

Footnotes in posts (Ghost's Markdown card) become notes at the end of their chapter, with a "Notes" heading; tapping a note number opens the note in a pop-up in reading apps that support it, such as Thorium.

Embedded media become **link cards**: a YouTube or Vimeo video, a Spotify episode, an audio or video file, a bookmarked page, or a post from X, TikTok or Bluesky appears as a small card with a label ("Video", "Audio", "Link", "Post on X", …), the linked title or the post's text, a source line, and a thumbnail for videos and bookmarks. A reader taps through to watch or listen online. Thumbnails are decorative, so they never need alt text. Forms and polls (Tally) can't work in a book and are left out; anything not recognised becomes a generic "Embedded content" card; both are listed after the build.

Links in posts are checked: a link to a section of the post that doesn't exist (e.g. a digest's contents pointing at a renamed section) is pointed at the section it meant, a mistyped address (stray spaces or quotes, a missing `https://`) is fixed, and a link that can't be repaired loses the link but keeps its words. Every changed link is listed after the build with its old and new address, so you can check the repairs.

Website calls-to-action are taken out of Flaming Hydra books: subscribe banners at the end of posts, and subscribe, support, share and shop buttons. Subscribe or sign-up links inside sentences keep their words but lose the (dead) link, and each such paragraph, plus any promotional image kept because it's mid-post, is listed after the build for review.

Content files are HTML fragments, inserted as-is; start them with an `<h2>` heading (the title page holds the book's `<h1>`). The introduction also accepts plain text, one paragraph per line. In a manifest, use the same names with underscores (`foreword_file`, `about_file`, …) under `[content]`.

### Images

By default, post images are scaled down to at most 1100px wide and re-encoded as JPEG at quality 85, which typically shrinks a book several-fold with no visible loss. Images with real transparency stay PNG (fully transparent borders are trimmed first), animated GIFs and SVGs pass through untouched, and an image is left as-is when re-encoding wouldn't make it smaller.

| Option | Manifest `[images]` | Default |
|---|---|---|
| `--image-max-width N` | `max_width` | `1100` (`0` = never resize) |
| `--image-quality N` | `quality` | `85` |
| `--keep-original-images` | `optimize = false` | off |

The cover image is handled separately and isn't affected by these options.

Link-card thumbnails come from the video or page's own site: YouTube (`i.ytimg.com`) and bookmark images directly; for TikTok (`www.tiktok.com`, then `*.tiktokcdn.com` / `*.tiktokcdn-us.com`), Vimeo (`vimeo.com`, then `i.vimeocdn.com`) and Spotify (`open.spotify.com`, then `*.scdn.co`), the build first asks the site for the thumbnail's address. Behind a firewall, allow these hosts; otherwise the cards simply have no thumbnail.

If an image can't be downloaded (network error, HTTP error, or a response that isn't actually an image), it is left out of the book and the build still succeeds; each one is listed as a warning after the build. Temporary failures (timeouts, connection errors, HTTP 5xx/429) are retried once.

### Alt text

Alt text is what screen readers say instead of showing an image. After each build, phoenix-ebook lists every image whose alt text is **missing** or **looks unhelpful** (a file name like `IMG_5799.jpg`, a generic word like "image", or a copy of the caption, which would be read twice), with the post, the image's position ("image 3 of 12"), its URL and caption. It then prints a commented-out block ready to paste into your manifest:

```toml
[alt_text]
"https://…/content/images/2026/08/IMG_5799.jpg" = "Klee's Angelus Novus: a birdlike angel with wide eyes and spread wings."
"https://…/content/images/2026/08/divider.png" = ""   # empty = decorative: no alt text, no warning
```

Entries here win over the alt text from the site, and match the image whatever size variant the post uses. Fixing alt text in Ghost itself is even better: the website benefits too, and the next build picks it up. An entry that matches no image is reported, so typos don't go unnoticed.

Writing good alt text: describe what the image contributes in context, briefly; don't start with "Image of". If the image contains text (a comic's speech bubbles, a screenshot of a post or document), include the text.

### Accessibility

Each book declares accessibility metadata (shown, for example, in Thorium's book-info dialog), **computed from what the book actually contains**: how it can be read (text, plus images when present), its features (table of contents, structural navigation, resizable text with the reader's own font, and *text descriptions for images* only once every image has alt text or is marked decorative), possible hazards (none, or an unknown flashing hazard if an animated GIF is included), and a one-sentence summary such as "31 of 38 images have text descriptions." Replace the summary with your own wording via `accessibility_summary` under `[book]` in a manifest.

Books make **no WCAG conformance claim**: that's a promise that someone has reviewed the book against the standard, which a build can't do. Pages also carry ARIA roles (chapters, foreword, introduction, acknowledgments) so screen readers can navigate by section.

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
├── alt_text.py          # alt text checks and overrides
├── errors.py            # PhoenixError and its subclasses (user-fixable failures)
├── models.py            # Post, Author, BookSpec, SourceSpec, BuildResult, BuildProblem
├── secrets.py           # API key storage
└── styles/              # core.css (Standard Ebooks, unmodified) + phoenix.css
```

Each platform converts its own HTML dialect into one **canonical chapter HTML**; everything after that (site processors, the builder, images, alt text, styles) only sees the canonical form. Platform quirks belong in the platform, site quirks in a site processor. See [ADR 0001](docs/adr/0001-platforms-normalize-to-canonical-html.md) and the canonical forms a platform must produce, such as notes, in [docs/canonical-html.md](docs/canonical-html.md).

**New platform.** Subclass `Platform` in `phoenix_ebook/platforms/`, set `name` (and `default_processor` if not `"generic"`), decorate it with `@register_platform`, and import the module in `phoenix_ebook/platforms/__init__.py`. `fetch_post()` returns a `Post`: the body HTML only, run through your `normalize_html()` so it's canonical (Ghost's, for example, drops editor `kg-*` classes); authors as `Author(name, url)`; `published_at` in the site's own timezone; and the feature image fields if the platform has them. Override `canonical_image_url()` if the platform serves size variants of the same image. The docstrings on `Platform` have the full contract, including the errors to raise.

**New site processor.** Subclass `HtmlProcessor` in `phoenix_ebook/processors/`, set `name`, `platform` and `sites` (domain substrings it handles), decorate it with `@register_processor`, and import the module in `phoenix_ebook/processors/__init__.py`. It's chosen automatically for matching sites on that platform (or with `--processor`). Overrides of `clean()` **must call `super().clean()` first**; that's where the shared web cleanup happens. See `flaminghydra.py` for an example.

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

`build()` never fails because of an image; those come back in `result.problems`. Failures the user can fix (a rejected key, a missing post, an unreachable site, an invalid option or manifest, an unwritable output path) raise a subclass of `phoenix_ebook.errors.PhoenixError` whose message says what to do next, e.g. `PostNotFound` lists every missing slug. Catch `PhoenixError` to show the message; anything else escaping is a bug:

```python
from phoenix_ebook.errors import PhoenixError

try:
    result = build(spec, posts, processor, session=session, image_base_url=site)
except PhoenixError as exc:
    show_to_user(str(exc))
```

## Development

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt   # adds pytest
.venv/bin/python scripts/fetch_epubcheck.py                         # optional: pinned epubcheck into .tools/
.venv/bin/python -m pytest
```

To check a built book with [DAISY Ace](https://daisy.github.io/ace/), the standard EPUB accessibility checker (needs Node.js and a headless Chrome; the desktop Ace App is the easy alternative):

```bash
npm install @daisy/ace
PUPPETEER_EXECUTABLE_PATH=/path/to/chrome npx ace-puppeteer --outdir ace-report book.epub   # opens as ace-report/report.html
```

The suite runs offline (no network, no API key). The epubcheck tests need Java and the downloaded validator; without them they are skipped, not failed.

## Changes

See [CHANGELOG.md](CHANGELOG.md) for changes that affect how books come out.

## License

[MIT](LICENSE)
