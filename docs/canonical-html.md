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

## Buttons

A button is a single link styled as a button on the website:

```html
<p class="button"><a href="https://example.org/book">READ THE BOOK</a></p>
```

Site processors decide which buttons a book keeps (Flaming Hydra removes subscribe, share and shop buttons). Ghost: button cards (`kg-button-card`).

## Call-to-action markers

When a layer removes a website call-to-action **link** but keeps its words, it marks them so the builder can report the paragraph to the editor; the builder then removes the marker:

```html
<p>Why not <span data-call-to-action="https://example.org/subscribe">subscribe or donate</span>?</p>
<figure data-call-to-action-banner="">…a promotional image kept because it isn't at the end of the post…</figure>
```

Use `phoenix_ebook.canonical.mark_call_to_action`. Ghost marks dead `#/portal/…` links; Flaming Hydra marks its `/subscribe` links and mid-post banners.

## Not platform-specific

These are produced from the `Post` fields by the builder, not by platforms: the chapter header (date, title, byline, feature image figure; from `published_at`, `title`, `authors`, `feature_image*`).
