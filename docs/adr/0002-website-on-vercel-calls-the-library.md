# The website is a thin FastAPI app on Vercel that calls the library

phoenix-ebook gets a website so people can build a Flaming Hydra book without the command line. We decided that **the website lives in this repo (`web/`) as a FastAPI app that turns a form into a `BookSpec` and calls the same `build()` the CLI does**, with server-rendered HTML and a little JavaScript (no JS framework or build step), deployed on Vercel (brickhousecoop team, Pro plan). The website adds no book rules of its own: anything that changes how books come out goes in the library, so the CLI and the website can't drift apart, and the existing offline tests cover both.

Version 1 is deliberately minimal ("something to play with"):

- **One site, one key.** Flaming Hydra only. The server holds the site's existing Ghost Admin key as a Vercel environment variable, the same key the CLI uses; other platforms will likely issue one key per site, so the setup stays uniform. Any post the key can read can go in a book, drafts and paid posts included, digests too.
- **No login, no saved state.** Access is limited by Vercel Authentication (brickhousecoop Vercel team only) while we try it out, plus `noindex`; turning it off makes the site public with no code change. Nothing is stored between requests except the finished EPUB.
- **Flow.** A form (post addresses in reading order, title, subtitle, editor, cover; every other manifest field under "advanced", content pages as `.html`/`.txt`/`.md`/`.docx` uploads) → checks that report every problem at once (post list, duplicates, missing posts, ISBN/ISSN, uploads), form refilled → the build with live progress → one results page: the download link, every warning and automatic change, and alt-text boxes that feed a full rebuild.
- **Limits.** At most 100 posts per book, sized from measured builds (≈2 s per post on one CPU: 50 posts took 102 s, 131 MB of memory, 44 MB of EPUB) to stay well inside Vercel Pro's 800 s function limit. No limit on how many builds run.
- **Storage.** Vercel functions can't return more than 4.5 MB, so the EPUB goes to a public Vercel Blob store under an unguessable name and the results page links to it. No expiry or cleanup yet.
- **Deploys.** From the sandbox with the Vercel CLI until a brickhousecoop GitHub Owner approves Vercel's GitHub app; then every push to `main` deploys, with previews for branches.

## Considered options

- **A separate frontend (React/Next.js) calling a Python API**: two languages and a JS build step for a form, a progress view and a results page.
- **A background job queue with a status page**: survives long builds and closed tabs, but needs storage and polling; a 100-post build fits in one request, with progress streamed.
- **Ghost's read-only Content API key**: can't fetch paid posts or drafts, which the website is meant to include.
- **Expiring or private downloads** (cleanup cron, private store with signed links): deferred for simplicity. A public store can't be turned private, so moving to signed links later means a new store; nothing in it needs to carry over.

## Consequences

- Anyone allowed through can build a book from any post the key can read, including paid posts and drafts, until real auth exists. Vercel Authentication is the stopgap; keep the form → `BookSpec` → `build()` seam so auth can sit in front of it later.
- The form and the TOML manifest both produce a `BookSpec`; adding a manifest field means adding it to the form's advanced section too.
- Library changes the website needs (plain-text content pages escaped properly, `.md`/`.docx` content files, progress reporting from `build()`) land in the library and reach the CLI as well.
- Costs are usage-based with no build limit; a Vercel Owner should set a Spend Management cap.
