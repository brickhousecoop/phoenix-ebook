"""Platform credentials, keyed by (platform, domain).

Storage names (``~/.phoenix_secrets.json``, ``PHOENIX_SECRET_*``, keyring service
``phoenix:<platform>``) are shared with other Phoenix modules on purpose.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from phoenix_ebook.errors import MissingSecret

try:
    import keyring
except ImportError:
    keyring = None


SECRET_FILE = Path.home() / ".phoenix_secrets.json"
LEGACY_GHOST_SECRET_FILE = Path.home() / ".ghost_epub_secrets.json"
KEYRING_SERVICE_PREFIX = "phoenix"


def extract_domain(url: str) -> str:
    """'https://www.Example.com/x' -> 'example.com' (the key secrets are stored under)."""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def env_var_name(platform: str, domain: str) -> str:
    """('ghost', 'site.ghost.io') -> 'PHOENIX_SECRET_GHOST_SITE_GHOST_IO'."""
    slug = re.sub(r"[^a-z0-9]", "_", f"{platform}_{domain}").upper()
    return f"PHOENIX_SECRET_{slug}"


def _keyring_service(platform: str) -> str:
    return f"{KEYRING_SERVICE_PREFIX}:{platform}"


def _load_file() -> dict[str, dict[str, str]]:
    _migrate_legacy()
    if not SECRET_FILE.exists():
        return {}
    data = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _save_file(data: dict[str, dict[str, str]]) -> None:
    SECRET_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    SECRET_FILE.chmod(0o600)


def _migrate_legacy() -> None:
    if not LEGACY_GHOST_SECRET_FILE.exists():
        return
    try:
        legacy = json.loads(LEGACY_GHOST_SECRET_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(legacy, dict) or not legacy:
        return

    merged: dict[str, dict[str, str]] = {}
    if SECRET_FILE.exists():
        try:
            existing = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                merged = existing
        except json.JSONDecodeError:
            pass

    ghost_bucket = merged.setdefault("ghost", {})
    for domain, secret in legacy.items():
        ghost_bucket.setdefault(domain, secret)

    SECRET_FILE.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    SECRET_FILE.chmod(0o600)
    backup = LEGACY_GHOST_SECRET_FILE.with_suffix(".json.migrated")
    LEGACY_GHOST_SECRET_FILE.rename(backup)
    print(
        f"Migrated secrets from {LEGACY_GHOST_SECRET_FILE} → {SECRET_FILE} "
        f"(backup: {backup})",
        file=sys.stderr,
    )


class SecretStore:
    """Looks up and stores secrets. A legacy ``~/.ghost_epub_secrets.json`` is
    migrated into the shared file on first access (the old file is kept as
    ``.json.migrated``)."""

    def __init__(self, override: str | None = None) -> None:
        self._override = override

    def get(self, platform: str, domain: str) -> str:
        """Return the secret, checking in order: constructor override, environment
        variable, ``~/.phoenix_secrets.json``, OS keyring. Raises RuntimeError with
        setup instructions if none has it."""
        if self._override:
            return self._override

        env_val = os.environ.get(env_var_name(platform, domain))
        if env_val:
            return env_val

        bucket = _load_file().get(platform, {})
        if domain in bucket:
            return bucket[domain]

        if keyring is not None:
            try:
                val = keyring.get_password(_keyring_service(platform), domain)
                if val:
                    return val
            except Exception as e:
                print(f"keyring read failed: {e}", file=sys.stderr)

        raise MissingSecret(
            f"No secret found for platform={platform!r} domain={domain!r}.\n"
            f"Options:\n"
            f"  1. Pass the secret directly (CLI --admin-key or SecretStore(override=...))\n"
            f"  2. Set env {env_var_name(platform, domain)}\n"
            f"  3. Run build_epub.py --set-secret --platform {platform} --url <site>"
        )

    def set(self, platform: str, domain: str, secret: str, *, prefer_keyring: bool = True) -> str:
        """Store a secret in the OS keyring (verified by reading it back), or in
        ``~/.phoenix_secrets.json`` (mode 600) if that fails or ``prefer_keyring``
        is False. Returns where it was stored."""
        if prefer_keyring and keyring is not None:
            try:
                keyring.set_password(_keyring_service(platform), domain, secret)
                verified = keyring.get_password(_keyring_service(platform), domain)
                if verified == secret:
                    return "keyring"
                print(
                    "keyring store succeeded but read-back differed; falling back to file.",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"keyring store failed: {e}; falling back to file.", file=sys.stderr)

        data = _load_file()
        data.setdefault(platform, {})[domain] = secret
        _save_file(data)
        return str(SECRET_FILE)
