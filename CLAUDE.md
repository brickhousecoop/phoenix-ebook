# Phoenix

Ghost→EPUB builder. Currently targets `flaminghydra.ghost.io`. Trajectory:
1. Add more source platforms (Substack is named next).
2. Add more site-specific HTML quirks via per-site processors.
3. Eventually: web UI that drives the library (not just a CLI).

The code is already split for that trajectory — keep the seams.

## Layout

```
build_epub.py                   # thin CLI shim
phoenix/
├── models.py                   # Post, BookSpec, SourceSpec dataclasses
├── secrets.py                  # SecretStore keyed by (platform, domain)
├── platforms/
│   ├── base.py                 # Platform ABC + registry
│   └── ghost.py                # GhostPlatform (JWT + Admin API)
├── processors/
│   ├── base.py                 # HtmlProcessor ABC + GenericProcessor
│   └── flaminghydra.py         # site-specific rules for flaminghydra
└── epub_builder.py             # EPUB assembly, image pipeline
```

Adding a new source platform = new module in `phoenix/platforms/` that registers itself. Adding site quirks = new processor module. Don't collapse these layers back into `build_epub.py`.

## Secrets

`phoenix/secrets.py` `SecretStore` lookup order:
1. Constructor `override` (CLI `--admin-key`)
2. Env `PHOENIX_SECRET_<PLATFORM>_<DOMAIN>` (e.g. `PHOENIX_SECRET_GHOST_FLAMINGHYDRA_GHOST_IO`)
3. JSON `~/.phoenix_secrets.json` with schema `{platform: {domain: secret}}`
4. OS keyring, service `phoenix:<platform>`, username `<domain>`

Legacy `~/.ghost_epub_secrets.json` auto-migrates into the namespaced file under `"ghost"` on first access; the old file is renamed `.migrated`. If old secrets appear "lost," check for the `.migrated` backup.

## Setup on a fresh machine

```
pip install -r requirements.txt
# then store the Ghost admin key once:
python build_epub.py --set-secret --platform ghost --url https://flaminghydra.ghost.io
```

## Running

CLI (back-compatible with pre-refactor flags, plus `--platform`, `--processor`, `--manifest`):
```
python build_epub.py --url https://flaminghydra.ghost.io \
  --title "My Book" --cover cover.jpg --output book.epub \
  slug-one slug-two slug-three
```

Or via TOML manifest (`--manifest book.toml`), which is also the shape the future web UI will produce.

## State / open items

- `main` branch, no remote yet. User plans to provide a GitHub URL to push to.
- Refactor from monolithic script → `phoenix/` package is committed (`7499745`). Only imports and `--help` have been smoke-tested; **no live end-to-end rebuild has been run against the refactored code** — needs the user's admin key. First priority on resume: rebuild a known book and compare against a pre-refactor `.epub`.
- Legacy test artifacts (`a-colonoscopy*`, `issue-657*`, `test_hydra.epub`, `t.py`, `token*`, `node_modules/`) sit in the working directory but are excluded via `.gitignore`. Don't clean them up without asking.

## Conventions

- No tests yet; don't add a test framework without being asked.
- Keep CLI flags back-compatible — the user has existing invocations.
- When a new platform/processor is added, register it in its module's `__init__.py` so the registry picks it up on import.
