# Changelog

Changes that affect how books come out or how phoenix-ebook is used. Existing command lines keep working; where the output changed, the entry says how to get the old behavior if there is one.

## Unreleased

### Changed
- **Website calls-to-action removed from books** (#20): trailing subscribe banners (with their heading and caption), and subscribe/support/share-on-Bluesky/shop buttons, are removed; subscribe and Ghost "portal" links inside sentences keep their words but lose the link; each such paragraph and any mid-post promotional image (kept, since it may be the post's content) is reported after the build. Removed banners are never downloaded, so books no longer declare a flashing hazard just because of the animated logo, and image and alt-text counts change accordingly.
- **Friendly errors** (#14): common mistakes print one actionable line instead of a Python traceback, with exit code 1: a wrong or malformed key, no stored key, mistyped slugs (all listed at once), the public site URL instead of the Ghost address (it redirects, which drops the key), an unreachable or firewall-blocked host (with the firewall's explanation), a broken or incomplete manifest, an unknown platform or processor, a missing output folder, and a content file that isn't UTF-8. `--debug` shows the traceback; real bugs always do. Ctrl-C prints `interrupted` (exit 130).
- **No half-written books** (#14): the EPUB is written to a temporary file and renamed into place when complete, so a failed or interrupted build never leaves a broken book or damages an existing one.
- **Credits** (#5): `--author` is now `--editor` (the old flag still works). The editor is credited as the book's creator, and each post's author as a contributor with a sort name ("Connor, J.D."), so reading apps don't shelve an anthology under one writer. Without an editor, post authors are the creators. For library use, `BookSpec.author` is renamed `BookSpec.editor`.
- **No default rights statement** (#5): the hard-coded "© All rights reserved." is gone; pass `--rights` to include one.
- **Identifier format** (#5): ISBNs are stored as `urn:isbn:…` (typed as ISBN-13 or ISBN-10); books without one get `urn:uuid:…`.
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
- **Bogus page list** (regression from #9): every chapter was listed as a printed "page" (labelled "article-2", "article-3", …), which Thorium uses for page numbers and "go to page". Books no longer include a page list (they have no print page numbers). Found by DAISY Ace.
- **A content-file path that doesn't exist** (e.g. a typo in `--foreword-file`) now stops the build with a clear error before anything is fetched; it used to produce a placeholder page silently (or crash, for the cover and intro).
- **Images that can't be downloaded** (#1) are left out and listed as warnings after the build, instead of leaving a remote link in the book (which made the EPUB invalid). HTTP error pages are no longer embedded as images. Temporary failures are retried once.
- **Cover reference** was placed in the wrong part of the package file, which failed epubcheck; books now validate cleanly.
- **Titles containing `&` or `<`** no longer produce an invalid EPUB.

### Added
- **Footnotes become proper notes** (#18): Ghost Markdown-card footnotes ("[1]" markers and a list at the end of the post) are now marked as notes: markers show as plain superscript numbers with a larger tap area, the list gets a "Notes" heading, and Thorium opens a note in a pop-up when its number is tapped. Notes stay at the end of their chapter, with "↩︎" links back.
- For library use, `build()` accepts an optional `platform`, used for platform-specific image-URL rules when matching `[alt_text]` overrides (#19). Internally, each platform now converts its HTML to one canonical form before processing (ADR 0001); books are unchanged.
- **Accessibility metadata** (#8): access modes, features, hazards and a summary, computed from each book's content (e.g. "text descriptions for images" is claimed only when every image has alt text), plus ARIA roles for chapters, foreword, introduction and acknowledgments, and the book language on the package. No WCAG conformance claim. DAISY Ace now passes on the test book (it reported 29 findings before). Chapters are wrapped in `<section>` instead of `<article>` (required for the chapter ARIA role).
- **Section roles and better line breaks** (#9): every page now declares its part of the book (front matter, body matter, back matter) and, where it fits, what it is (copyright page, imprint, foreword, introduction, chapter, acknowledgments), for reading apps' navigation. (Screen readers mostly rely on matching ARIA roles, which aren't added yet; see #8.) An invisible no-break character before em dashes and ellipses stops lines from starting with "—" or "…" (on a 360px-wide screen, 6 of 106 did before, none now). Your content files are wrapped, never changed.
- **Alt text report and overrides** (#10): after each build, images with missing or unhelpful alt text are listed (post, position, URL, caption) with a ready-to-paste manifest block. A manifest `[alt_text]` table supplies or replaces alt text by image URL; an empty value marks an image decorative.
- `--subtitle`, `--series`, `--series-number`, `--issn`, `--rights` and the manifest's `[sort_names]` table (#5). The ISSN identifies the series and is shown with it on the title page.
- ISBN and ISSN validation (#5): an invalid number (wrong length or check digit) stops the build before anything is fetched. Subtitle and series appear on the title page.
- `--css` (manifest: `[style] css`): add your own stylesheet, applied after the built-in ones.
- `--no-placeholders` (manifest: `[content] placeholders = false`): leave out sections that have no file instead of adding placeholder pages.
- EPUB landmarks for the title page, contents page and start of the text, which some reading apps use to decide where a book opens.
- `--image-max-width`, `--image-quality`, `--keep-original-images` and the manifest `[images]` table.
- For library use, `build()` now returns `BuildResult(path, problems)` instead of a bare path.
- An offline test suite (`.venv/bin/python -m pytest`), including epubcheck validation.
