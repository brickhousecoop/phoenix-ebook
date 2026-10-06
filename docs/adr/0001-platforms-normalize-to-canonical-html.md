# Platforms normalize to canonical chapter HTML; processors handle site quirks

Each source platform (Ghost today, Substack next) delivers post HTML in its own dialect: Ghost's editor leaves `kg-*` card classes and members-only comments, Ghost's Markdown card emits markdown-it footnotes, Substack has its own footnote and embed markup. We decided that **a platform converts its dialect into one canonical chapter HTML inside the platform package** (in `fetch_post`, so `Post.html` and `Post.feature_image_caption` are already canonical), and that **everything downstream (site processors, the builder, images, alt text, styles) only ever sees canonical HTML**. Site processors (e.g. Flaming Hydra's) handle quirks of one site and pick on top of their platform's output; a platform names its default processor, and site processors are chosen by site *within* a platform. Platform-specific URL rules (Ghost's `/size/w…/` image variants) live behind the platform (`Platform.canonical_image_url`).

## Considered options

- **One shared cleanup in `HtmlProcessor` for all platforms** (what we had): simplest, but Ghost's rules would run on every platform's posts, and each new platform would pile its own rules into the same base class, coupling all of them.
- **Per-site processors only**: every Ghost site would need its own processor just to get Ghost's cleanup.

## Consequences

- Canonical chapter HTML is a contract (see `CONTEXT.md`): new markup features (e.g. notes, #18) are defined once in canonical form; each platform maps its dialect to it, and the builder adds EPUB semantics without knowing the platform.
- Generic web hygiene that applies to any HTML (inline styles, `loading` attributes, HTML comments, editor `<br>` spacing, nested bold) stays in `HtmlProcessor.clean()`.
- The CLI's default `--url` remains the Flaming Hydra Ghost site for back-compatibility with existing invocations; it isn't a design assumption.
