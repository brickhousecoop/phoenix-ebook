# phoenix-ebook

Ghost/WordPress→EPUB builder. Name it "phoenix-ebook" in prose — it is one module of a larger Phoenix project, not "Phoenix" itself. Currently targets Flaming Hydra and Welcome to Hell World (Ghost) and Defector (WordPress full-text feed). Trajectory:
1. Add more source platforms.
2. Add more site-specific HTML quirks via per-site processors.
3. A web UI that drives the library (not just a CLI): live at ebookmaker.phoenixbook.services (phoenix-ebook.vercel.app redirects there), see `docs/website-setup.md`.

The code is already split for that trajectory — keep the seams.

## Layout

```
build_epub.py                   # CLI: flags/manifest → BookSpec, runs the build, reports problems
phoenix_ebook/
├── models.py                   # Post, Author, BookSpec, SourceSpec, BuildResult/BuildProblem
├── secrets.py                  # SecretStore keyed by (platform, domain)
├── platforms/
│   ├── base.py                 # Platform ABC (+ normalize_html, canonical_image_url, default_processor) + registry
│   ├── ghost.py                # GhostPlatform (JWT + Admin API)
│   └── wordpress_feed.py       # WordPressFeedPlatform (private full-text RSS feed; the feed address is the secret)
├── processors/
│   ├── base.py                 # HtmlProcessor (generic web cleanup in clean(), then apply_rules()) + select_processor
│   ├── rules.py                # shared cleanup rules, and reading the files in sites/
│   └── flaminghydra.py         # flaminghydra's unusual logic (its lists are in sites/)
├── sites/                      # what each blog removes, as data; _defaults.toml applies to every blog; a blog can be only a file
├── images.py                   # fetch_image() / validate_image() / optimize_image()
├── alt_text.py                 # alt text checks (missing/suspicious) and URL-keyed overrides
├── errors.py                   # PhoenixError + subclasses; explain_response() for HTTP bodies
├── canonical.py                # shared canonical-HTML markers (call-to-action, link cards)
├── embeds.py                   # third-party embeds (YouTube, X, TikTok, …) -> link cards; provider rules
├── dates.py                    # format_date(): dates as books show them
├── links.py                    # shared link repair (broken #sections, malformed addresses); marks changes
├── content.py                  # content files (.html/.txt/.md/.docx) -> their page's HTML
├── styles/
│   ├── core.css                # Standard Ebooks snapshot (CC0) — never edit; see its header
│   └── phoenix.css             # our styles for our markup; all overrides go here
└── epub_builder.py             # EPUB assembly
web/                            # the website (FastAPI on Vercel; ADRs 0002-0004); calls the library, never the reverse
├── main.py                     # routes, sign-in middleware, the old-books cron job (/jobs/delete-expired-books)
├── forms.py                    # form -> BookSpec, every pre-build check; BLOGS for a site without the store
├── runs.py                     # one submission: check, build, save
├── auth.py                     # users, passwords, sign-in cookies
├── store.py                    # read-only Postgres store: blogs, encrypted keys, users, access
├── store_admin.py              # writing the store (used only by scripts/store_admin.py)
├── storage.py                  # Vercel Blob or a local folder; books deleted after 3 hours on Blob
├── problems.py                 # warning meanings, from docs/troubleshooting.md
└── templates/
tests/                          # offline pytest suite (fixtures in conftest.py); store tests use Docker Postgres if present
scripts/fetch_epubcheck.py      # pinned epubcheck into .tools/ for the epubcheck tests
scripts/store_admin.py          # manage the website's store, on the maintainer's computer only
scripts/add_user.py             # website users for a site without the store
```

Adding a new source platform = new module in `phoenix_ebook/platforms/` that registers itself and converts its HTML dialect to **canonical chapter HTML** in `normalize_html` (ADR 0001, `docs/adr/0001-…`; the canonical forms, e.g. notes, are in `docs/canonical-html.md`). Adding a site's lists = a file in `phoenix_ebook/sites/`; adding unusual logic = a processor module with `platform` + `sites`. Platform-specific rules never go in `HtmlProcessor.clean()` or the builder; site-specific rules never go in a platform. Don't collapse these layers back into `build_epub.py`.

## Setup, usage, secrets

See `README.md` — it's the source of truth for setup, CLI/manifest usage, and the secret lookup order. Keep it current when flags or behavior change. Agent-specific notes:

- Run everything via `.venv/bin/python`. Create the venv with `uv` — on Debian/Ubuntu system Python, `pip install` is blocked (PEP 668) and `python3 -m venv` may lack `ensurepip`.
- In a container/sandbox there's usually no OS keyring: the user stores the key with `--set-secret-file` from their own shell. Never ask for the key in chat or read `~/.phoenix_secrets.json` contents.
- Secret naming (`~/.phoenix_secrets.json`, `PHOENIX_SECRET_*`, keyring service `phoenix:<platform>`) is deliberately **shared across all Phoenix modules** — don't rename it to `phoenix_ebook`. Only the Python package is `phoenix_ebook`.
- Legacy `~/.ghost_epub_secrets.json` auto-migrates into `~/.phoenix_secrets.json` under `"ghost"` on first access; the old file is renamed `.migrated`. If old secrets appear "lost," check for the `.migrated` backup.
- The TOML manifest is also the shape the future web UI will produce — keep it and `BookSpec` in sync.

## Conventions

- Run the test suite before committing (`.venv/bin/python -m pytest`); changes that add or change behavior come with tests. Tests are offline: use the `FakeSession` / `build_book` fixtures in `tests/conftest.py`, never the network.
- Keep CLI flags back-compatible — the user has existing invocations.
- User-fixable failures raise a `PhoenixError` subclass (`phoenix_ebook/errors.py`) whose message says what to do next; the CLI prints `error: …` (exit 1, `--debug` for the traceback). Anything else escaping is treated as a bug and shows a traceback.
- `phoenix_ebook/styles/core.css` is a pinned, unmodified Standard Ebooks snapshot (a checksum test enforces it). Put style changes in `phoenix.css`; never set `font-family` (readers' fonts win). Style changes need a visual check: render pages (e.g. headless Chromium) and give the user a sample book to review.
- When a new platform/processor is added, register it in its module's `__init__.py` so the registry picks it up on import. Processor `clean()` overrides must call `super().clean()`; a processor's own logic usually belongs in `apply_rules()`.
- Document contracts, not mechanics: public API and extension points get docstrings; trivial helpers don't. Changes that alter how books come out get a CHANGELOG.md entry.

## Agent skills

### Issue tracker

GitHub Issues on `brickhousecoop/phoenix-ebook`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (created lazily). See `docs/agents/domain.md`.
