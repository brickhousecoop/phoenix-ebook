# phoenix-ebook

Builds EPUB books from posts published on blogging and newsletter platforms. This glossary covers the parts of a book and where they come from.

## Sources

**Post**:
A single published piece on the source platform (e.g. a Ghost post), with its title, authors, date, body and feature image.
_Avoid_: Article, entry, story

**Canonical chapter HTML**:
The single HTML form every platform converts its posts into before anything else touches them (see ADR 0001). Defines how shared features such as notes and figures are written, independent of the platform's own dialect.
_Avoid_: Clean HTML, normalized HTML (as a loose description)

**Platform**:
A publishing service posts come from (Ghost, later Substack), with its own API and HTML dialect.
_Avoid_: Source (that's the platform plus a site URL), CMS

**Site processor**:
Per-site adjustments applied on top of a platform's canonical HTML (e.g. dropping one site's comments footer).
_Avoid_: Plugin, filter

## Credits

**Editor**:
The person or group credited with compiling the book (the CLI's `--author`). Shown first, as the book's creator.
_Avoid_: Author (for the book as a whole), compiler

**Contributor**:
A writer of one or more of the book's posts. Credited per post in chapter bylines and, in the book's metadata, as an author-role contributor; becomes the book's creator only when there is no editor.
_Avoid_: Guest author, writer

**Series**:
A named run of books the book belongs to, with its number in the run (e.g. a monthly digest).
_Avoid_: Collection (ambiguous with the book itself), volume

## Book structure

**Decorative image**:
An image that conveys no content (a divider, an ornament, a link card's thumbnail), given empty alt text on purpose so it isn't reported as missing. Link-card thumbnails are decorative automatically; other images are marked in the book's alt-text table with an empty description.
_Avoid_: Spacer, ornament image

**Link card**:
What an embedded video, audio player, social post or bookmarked page becomes in a book: a label, a link to it (or the post's text), its source and, for videos and bookmarks, a decorative thumbnail.
_Avoid_: Embed (that's the website's live player), preview, widget

**Chapter**:
A post's page in the book. A book's chapters are its posts, in the order given.
_Avoid_: Article, section (those are only HTML element names)

**Reading order**:
The fixed sequence of pages a reader moves through: title page, copyright, imprint, contents page, foreword, introduction, half-title page, chapters, notes, acknowledgements, "About This Book".
_Avoid_: Spine (the EPUB file format's name for it)

**Contents page**:
The in-book page (also the reading app's contents menu) listing the book from the foreword onward: foreword, introduction, chapters and back matter. Each chapter's entry is its title, optionally with its authors, depending on the site.
_Avoid_: TOC page, nav

**Front matter**:
The pages before the first chapter: title page, copyright, imprint, contents page, foreword, introduction, and half-title page.
_Avoid_: Preliminaries, prelims

**Note**:
Text a post attaches to a point in its body (a numbered marker in the text), shown at the end of that post's chapter and as a pop-up in reading apps that support it. Not the "Notes" back-matter page, which is book-level.
_Avoid_: Footnote (it isn't at the foot of a page), citation

**Back matter**:
The pages after the last chapter: notes, acknowledgements, and "About This Book".
_Avoid_: End matter, appendix

**Title page**:
The front page naming the book (title, subtitle, author or editor, publisher), generated from the book's metadata.

**Half-title page**:
A page showing only the title, placed between the front matter and the first chapter as a divider. Omitted when the book has no front matter besides the title page and contents page.

**Placeholder page**:
A front- or back-matter page filled with bracketed stand-in text because no content file was supplied for that section.
_Avoid_: Stub page, dummy page
