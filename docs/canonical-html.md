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

## Link cards

Embedded media (videos, audio, social posts, bookmarked pages) become a link card: a reader can't play an embed in a book, but can follow a link.

```html
<figure class="card" data-card="video">
  <img class="thumbnail" src="https://i.ytimg.com/vi/ID/maxresdefault.jpg" alt="" data-decorative=""
       data-fallback-src="https://i.ytimg.com/vi/ID/mqdefault.jpg"/>
  <p class="label">Video</p>
  <p class="title"><a href="https://www.youtube.com/watch?v=ID">The video's title</a></p>
  <p class="description">…</p>                  <!-- bookmarks -->
  <blockquote><p>The post's text…</p></blockquote>   <!-- social posts, instead of a title -->
  <p class="source">YouTube</p>
  <figcaption>…the post's own caption, if any…</figcaption>
</figure>
```

- `data-card`: `video`, `audio`, `link`, `post` or `embed` (unrecognised). The label is text, not an icon.
- Only the label is required; the source line's parts are joined with " · ".
- The thumbnail is **decorative** (`data-decorative`): empty alt text by design, not reported. `data-fallback-src` is tried if `src` fails. When the thumbnail's address must be looked up, the `<img>` has no `src` but `data-thumbnail-lookup` (an oEmbed URL); text with `data-oembed-text="{author_name} (@handle)"` is filled from the same answer, or keeps its text. The builder resolves and removes all of these.
- An embed left out of the book (a form) is replaced by `<div data-embed-removed="Tally form" data-embed-src="…"></div>`; a card made from an unrecognised embed carries `data-embed-unknown="<its URL>"`. The builder reports both and removes the markers.

`phoenix_ebook/embeds.py` builds these from an embed's `<iframe>` or a social post's quote (provider rules, not platform rules). Ghost: embed, audio, video and bookmark cards, plus iframes pasted in HTML cards.

## Call-to-action markers

When a layer removes a website call-to-action **link** but keeps its words, it marks them so the builder can report the paragraph to the editor; the builder then removes the marker:

```html
<p>Why not <span data-call-to-action="https://example.org/subscribe">subscribe or donate</span>?</p>
<figure data-call-to-action-banner="">…a promotional image kept because it isn't at the end of the post…</figure>
```

Use `phoenix_ebook.canonical.mark_call_to_action`. Ghost marks dead `#/portal/…` links; Flaming Hydra marks its `/subscribe` links and mid-post banners.

## Changed links

The shared cleanup (`phoenix_ebook/links.py`, run by every processor) marks each link it changes, so the builder can report it; the builder then removes the markers:

```html
<a href="#polar-impressions" data-link-repaired="#polar-impresions" data-link-reason="matched the heading …">…</a>
<span data-link-unlinked="#gone" data-link-reason="points to a section that isn't in this post">words kept</span>
<span data-link-removed="https://example.org/x"></span>   <!-- where a link with no text was -->
```

Section ids are plain ASCII (`día-de-los-muertos` → `dia-de-los-muertos`); links to a renamed id follow it and are reported as repaired.

## Not platform-specific

These are produced from the `Post` fields by the builder, not by platforms: the chapter header (date, title, byline, feature image figure; from `published_at`, `title`, `authors`, `feature_image*`).
