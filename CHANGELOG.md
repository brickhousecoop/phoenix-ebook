# Changelog

Changes that affect how books come out or how phoenix-ebook is used. Existing command lines keep working; where the output changed, the entry says how to get the old behavior if there is one.

## Unreleased

### Changed
- **New look** (#7): books now use Standard Ebooks' `core.css` plus phoenix-ebook's own stylesheet: centered title page, small-caps dates and bylines, book-style paragraphs, figures kept on one screen with their captions, and a "Contents" page without numbering. The previous stylesheet forced the Georgia font; now no font is set, so readers' own font settings apply. Tall images are capped to the screen height instead of overflowing.
- **Book structure** (#6): a generated **title page** now opens the book, followed by copyright, imprint, the **contents page** (moved here from page 2), foreword and introduction; a **half-title page** divides front matter from the chapters. The contents page starts at the foreword: it no longer lists the title page, copyright, imprint, the half-title page or itself.
- **No cover page** (#6): the cover is declared as the book's cover image only, as Kindle's guidelines recommend; reading apps show it in the library. The book opens on the title page.
- **Chapter titles are `<h2>`** (#6), under the title page's `<h1>`; generated placeholder headings are `<h2>` too. If your content files start with `<h1>`, change them to `<h2>`.
- **Chapter headers** (#4): each chapter now opens with the publication date (in the site's timezone), the title, and a "By …" byline linking to each author's page. Contents entries are plain titles by default; Flaming Hydra books keep "Title — Author" (the site's processor decides).
- **Feature images** (#2): each post's lead image, with its alt text and caption, now appears at the top of its chapter. Previously it was missing.
- **Cleaner chapter markup** (#3): Ghost editor markup (`kg-*` classes, inline styles, `<!--members-only-->` comments, stray `<br>`, nested bold) is removed. Visible text is unchanged.
- **Image optimization on by default**: post images are scaled to at most 1100px wide and re-encoded as JPEG (quality 85); a 10-post test book went from 17.6 MB to 4.6 MB. Old behavior: `--keep-original-images`. Tune with `--image-max-width` and `--image-quality`, or an `[images]` table in the manifest.
- **The Python package is now `phoenix_ebook`** (was `phoenix`), since phoenix-ebook is one module of the larger Phoenix project. Secret storage names are unchanged and shared with other Phoenix modules.

### Fixed
- **A content-file path that doesn't exist** (e.g. a typo in `--foreword-file`) now stops the build with a clear error before anything is fetched; it used to produce a placeholder page silently (or crash, for the cover and intro).
- **Images that can't be downloaded** (#1) are left out and listed as warnings after the build, instead of leaving a remote link in the book (which made the EPUB invalid). HTTP error pages are no longer embedded as images. Temporary failures are retried once.
- **Cover reference** was placed in the wrong part of the package file, which failed epubcheck; books now validate cleanly.
- **Titles containing `&` or `<`** no longer produce an invalid EPUB.

### Added
- `--css` (manifest: `[style] css`): add your own stylesheet, applied after the built-in ones.
- `--no-placeholders` (manifest: `[content] placeholders = false`): leave out sections that have no file instead of adding placeholder pages.
- EPUB landmarks for the title page, contents page and start of the text, which some reading apps use to decide where a book opens.
- `--image-max-width`, `--image-quality`, `--keep-original-images` and the manifest `[images]` table.
- For library use, `build()` now returns `BuildResult(path, problems)` instead of a bare path.
- An offline test suite (`.venv/bin/python -m pytest`), including epubcheck validation.
