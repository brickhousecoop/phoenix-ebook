# phoenix-ebook

Builds EPUB books from posts published on blogging and newsletter platforms. This glossary covers the parts of a book and where they come from.

## Sources

**Post**:
A single published piece on the source platform (e.g. a Ghost post), with its title, authors, date, body and feature image.
_Avoid_: Article, entry, story

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

**Chapter**:
A post's page in the book. A book's chapters are its posts, in the order given.
_Avoid_: Article (that is only the HTML element a chapter is wrapped in), section

**Reading order**:
The fixed sequence of pages a reader moves through: title page, copyright, imprint, contents page, foreword, introduction, half-title page, chapters, notes, acknowledgements, "About This Book".
_Avoid_: Spine (the EPUB file format's name for it)

**Contents page**:
The in-book page (also the reading app's contents menu) listing the book from the foreword onward: foreword, introduction, chapters and back matter. Each chapter's entry is its title, optionally with its authors, depending on the site.
_Avoid_: TOC page, nav

**Front matter**:
The pages before the first chapter: title page, copyright, imprint, contents page, foreword, introduction, and half-title page.
_Avoid_: Preliminaries, prelims

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
