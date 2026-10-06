# Canonical chapter HTML

What a platform's `normalize_html()` must produce (see [ADR 0001](adr/0001-platforms-normalize-to-canonical-html.md)). Everything after the platform (site processors, the builder, images, alt text, styles) relies on these forms and nothing else. Ordinary HTML (paragraphs, headings, lists, links, `<figure>`/`<img>`/`<figcaption>`, emphasis, blockquotes) passes through as is; this document lists the forms that carry extra meaning.

## Notes

A post's notes are a numbered list at the end of the post, with markers in the text linking to them (see **Note** in `CONTEXT.md`).

```html
… text<a href="#fn1" id="fnref1" epub:type="noteref" role="doc-noteref">1</a> more text …

<section epub:type="endnotes" role="doc-endnotes">
  <h3>Notes</h3>
  <ol>
    <li id="fn1" epub:type="endnote">
      <p>The note's text. <a href="#fnref1" role="doc-backlink">↩︎</a></p>
    </li>
  </ol>
</section>
```

- The marker is a bare link (no `<sup>`; the stylesheet sets it as a superscript) whose text is the note's number, without brackets.
- Note items carry no ARIA role: `doc-endnote` is deprecated in DPUB-ARIA (items of a `doc-endnotes` section need none; epubcheck warns, Ace doesn't require it).
- Ids only need to be unique within the post: each post becomes its own chapter file.
- Reading apps that support it (e.g. Thorium) show the note in a pop-up when the marker is tapped; others jump to the list.

**Platform mappings:**
- Ghost: Markdown-card (markdown-it) footnotes, converted in `GhostPlatform.normalize_html`.
- Substack: to do with the Substack platform.

## Not platform-specific

These are produced from the `Post` fields by the builder, not by platforms: the chapter header (date, title, byline, feature image figure; from `published_at`, `title`, `authors`, `feature_image*`).
