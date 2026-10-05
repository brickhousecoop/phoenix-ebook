# Changelog

Changes that affect how books come out or how phoenix-ebook is used. Existing command lines keep working; where the output changed, the entry says how to get the old behavior if there is one.

## Unreleased

### Changed
- **Chapter headers** (#4): each chapter now opens with the publication date (in the site's timezone), the title, and a "By …" byline linking to each author's page. The table of contents shows plain titles; the Flaming Hydra "Title — Author" form is gone.
- **Feature images** (#2): each post's lead image, with its alt text and caption, now appears at the top of its chapter. Previously it was missing.
- **Cleaner chapter markup** (#3): Ghost editor markup (`kg-*` classes, inline styles, `<!--members-only-->` comments, stray `<br>`, nested bold) is removed. Visible text is unchanged.
- **Image optimization on by default**: post images are scaled to at most 1100px wide and re-encoded as JPEG (quality 85); a 10-post test book went from 17.6 MB to 4.6 MB. Old behavior: `--keep-original-images`. Tune with `--image-max-width` and `--image-quality`, or an `[images]` table in the manifest.
- **The Python package is now `phoenix_ebook`** (was `phoenix`), since phoenix-ebook is one module of the larger Phoenix project. Secret storage names are unchanged and shared with other Phoenix modules.

### Fixed
- **Images that can't be downloaded** (#1) are left out and listed as warnings after the build, instead of leaving a remote link in the book (which made the EPUB invalid). HTTP error pages are no longer embedded as images. Temporary failures are retried once.
- **Cover reference** was placed in the wrong part of the package file, which failed epubcheck; books now validate cleanly.
- **Titles containing `&` or `<`** no longer produce an invalid EPUB.

### Added
- `--image-max-width`, `--image-quality`, `--keep-original-images` and the manifest `[images]` table.
- For library use, `build()` now returns `BuildResult(path, problems)` instead of a bare path.
- An offline test suite (`.venv/bin/python -m pytest`), including epubcheck validation.
