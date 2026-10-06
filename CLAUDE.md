# phoenix-ebook

Ghost→EPUB builder. Name it "phoenix-ebook" in prose — it is one module of a larger Phoenix project, not "Phoenix" itself. Currently targets `flaminghydra.ghost.io`. Trajectory:
1. Add more source platforms.
2. Add more site-specific HTML quirks via per-site processors.
3. Eventually: web UI that drives the library (not just a CLI).

The code is already split for that trajectory — keep the seams.

## Layout

```
build_epub.py                   # CLI: flags/manifest → BookSpec, runs the build, reports problems
phoenix_ebook/
├── models.py                   # Post, Author, BookSpec, SourceSpec, BuildResult/BuildProblem
├── secrets.py                  # SecretStore keyed by (platform, domain)
├── platforms/
│   ├── base.py                 # Platform ABC + registry
│   └── ghost.py                # GhostPlatform (JWT + Admin API)
├── processors/
│   ├── base.py                 # HtmlProcessor (shared cleanup in clean()) + GenericProcessor
│   └── flaminghydra.py         # site-specific rules for flaminghydra
├── images.py                   # fetch_image() / validate_image() / optimize_image()
├── styles/
│   ├── core.css                # Standard Ebooks snapshot (CC0) — never edit; see its header
│   └── phoenix.css             # our styles for our markup; all overrides go here
└── epub_builder.py             # EPUB assembly
tests/                          # offline pytest suite (fixtures in conftest.py)
scripts/fetch_epubcheck.py      # pinned epubcheck into .tools/ for the epubcheck tests
```

Adding a new source platform = new module in `phoenix_ebook/platforms/` that registers itself. Adding site quirks = new processor module. Don't collapse these layers back into `build_epub.py`.

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
- When a new platform/processor is added, register it in its module's `__init__.py` so the registry picks it up on import. Processor `clean()` overrides must call `super().clean()`.
- Document contracts, not mechanics: public API and extension points get docstrings; trivial helpers don't. Changes that alter how books come out get a CHANGELOG.md entry.

## Agent skills

### Issue tracker

GitHub Issues on `buffystruggles/phoenix-ebook`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (created lazily). See `docs/agents/domain.md`.
