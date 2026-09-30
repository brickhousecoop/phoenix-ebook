#!/usr/bin/env python3
import argparse
import base64
import getpass
import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import keyring
import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image


# --- secret management ---

SECRET_FILE = Path.home() / ".ghost_epub_secrets.json"


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def env_key_name(domain: str) -> str:
    return f"GHOST_ADMIN_KEY_{re.sub(r'[^a-z0-9]', '_', domain).upper()}"


def get_admin_key(args) -> str:
    domain = extract_domain(args.url)

    if args.admin_key:
        return args.admin_key

    env_name = env_key_name(domain)
    env_key = os.environ.get(env_name)
    if env_key:
        return env_key

    if SECRET_FILE.exists():
        secrets = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
        if domain in secrets:
            return secrets[domain]

    try:
        key = keyring.get_password("ghost", domain)
        if key:
            return key
    except Exception as e:
        print(f"keyring read failed: {e}", file=sys.stderr)

    raise RuntimeError(
        f"No admin key found for {domain}.\n"
        f"Options (in precedence order):\n"
        f"  1. --admin-key 'id:secret'\n"
        f"  2. env {env_name}='id:secret'\n"
        f"  3. python3 build_epub.py --set-secret --url {args.url}\n"
        f"  4. python3 build_epub.py --set-secret-file --url {args.url}"
    )


def store_secret(args):
    domain = extract_domain(args.url) if args.url else args.domain
    if not domain:
        raise RuntimeError("Provide --url or --domain")

    key = getpass.getpass(f"Enter admin key for {domain}: ").strip()
    backend = keyring.get_keyring()
    print(f"Using keyring backend: {backend}")

    try:
        keyring.set_password("ghost", domain, key)
        verified = keyring.get_password("ghost", domain)
        if verified == key:
            print(f"Stored and verified secret for {domain} in keyring.")
            return
        else:
            print("Keyring store succeeded but read-back returned different data; falling back to file.")
    except Exception as e:
        print(f"Keyring store failed: {e}; falling back to file.")

    secrets = json.loads(SECRET_FILE.read_text(encoding="utf-8")) if SECRET_FILE.exists() else {}
    secrets[domain] = key
    SECRET_FILE.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    SECRET_FILE.chmod(0o600)
    print(f"Stored secret for {domain} in {SECRET_FILE}")


def store_secret_file(args):
    domain = extract_domain(args.url) if args.url else args.domain
    if not domain:
        raise RuntimeError("Provide --url or --domain")
    key = os.environ.get("GHOST_ADMIN_KEY") or getpass.getpass("Enter admin key: ").strip()
    secrets = json.loads(SECRET_FILE.read_text(encoding="utf-8")) if SECRET_FILE.exists() else {}
    secrets[domain] = key
    SECRET_FILE.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    SECRET_FILE.chmod(0o600)
    print(f"Stored secret for {domain} in {SECRET_FILE}")


# --- Ghost API helpers ---

def make_token(admin_key: str) -> str:
    key_id, key_secret = admin_key.strip().split(":")

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}).encode()
    payload = json.dumps({"iat": int(time.time()), "exp": int(time.time()) + 300, "aud": "/admin/"}).encode()
    signing_input = f"{b64url(header)}.{b64url(payload)}"
    signature = hmac.new(bytes.fromhex(key_secret), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def fetch_post(session, ghost_url, token, slug):
    r = session.get(
        f"{ghost_url}/ghost/api/admin/posts/slug/{slug}/",
        params={"formats": "html"},
        headers={"Authorization": f"Ghost {token}"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["posts"][0]


# --- EPUB helpers ---

def media_type_for_ext(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(ext.lower(), "image/jpeg")


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name).strip("_") or "item"


def wrap_text_as_html(title: str, text: str) -> str:
    if text.strip().startswith("<"):
        return text
    paragraphs = "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip())
    return f"<h1>{title}</h1>\n{paragraphs}"


def add_page(book, css, title, file_name, content, lang):
    if not content or not content.strip():
        content = f"<p>({title} — content placeholder)</p>"
    page = epub.EpubHtml(title=title, file_name=file_name, lang=lang)
    page.content = content
    page.add_item(css)
    book.add_item(page)
    return page


def load_or_placeholder(path, title, placeholder_text):
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip()
    return f"<h1>{title}</h1>\n<p>{placeholder_text}</p>"


# --- main build ---

def build_epub(args):
    ghost_url = args.url.rstrip("/")
    admin_key = get_admin_key(args)
    session = requests.Session()

    book = epub.EpubBook()
    book.set_identifier(args.isbn or str(uuid.uuid4()))
    book.set_title(args.title)
    book.set_language(args.lang)

    # Rich metadata for Thorium's "i" / publication info panel
    if args.author:
        book.add_metadata("DC", "creator", args.author)
    if args.publisher:
        book.add_metadata("DC", "publisher", args.publisher)
    if args.description:
        book.add_metadata("DC", "description", args.description)
    if args.pub_date:
        book.add_metadata("DC", "date", args.pub_date)
    book.add_metadata("DC", "rights", "© All rights reserved.")

    css = epub.EpubItem(
        uid="style",
        file_name="style/style.css",
        media_type="text/css",
        content="""
body { font-family: Georgia, serif; line-height: 1.6; margin: 1em; }
img { max-width: 100%; height: auto; display: block; margin: 1em 0; }
h1, h2, h3 { font-family: sans-serif; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; height: auto; }
""",
    )
    book.add_item(css)

    pages = []
    image_items = {}

    # ---- Cover ----
    if args.cover:
        cover_path = Path(args.cover)
        cover_bytes = cover_path.read_bytes()

        try:
            img = Image.open(io.BytesIO(cover_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            cover_bytes = buf.getvalue()
            cover_ext = ".jpg"
        except Exception:
            cover_ext = cover_path.suffix.lower() or ".jpg"
            if cover_ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                cover_ext = ".jpg"

        cover_fname = f"cover{cover_ext}"

        cover_item = epub.EpubItem(
            uid="cover-image",
            file_name=f"images/{cover_fname}",
            media_type=media_type_for_ext(cover_ext),
            content=cover_bytes,
        )
        cover_item.properties = {"cover-image"}
        book.add_item(cover_item)

        book.add_metadata(None, "meta", "", {"name": "cover", "content": "cover-image"})

        cover_page = add_page(
            book,
            css,
            "Cover",
            "cover.xhtml",
            f'<div class="cover"><img src="images/{cover_fname}" alt="Cover"/></div>',
            args.lang,
        )
        cover_page.id = "cover"
        pages.append(cover_page)

        book.add_metadata(
            None,
            "reference",
            "",
            {"type": "cover", "title": "Cover", "href": "cover.xhtml"},
        )

    # ---- Front matter ----
    copyright_html = load_or_placeholder(
        args.copyright_file,
        "Copyright",
        "[Copyright placeholder — replace with publication copyright notice.]",
    )
    copyright_page = add_page(book, css, "Copyright", "copyright.xhtml", copyright_html, args.lang)
    pages.append(copyright_page)

    imprint_html = load_or_placeholder(
        args.imprint_file,
        "Imprint",
        "[Imprint placeholder — publisher, edition, printing history, etc.]",
    )
    imprint_page = add_page(book, css, "Imprint", "imprint.xhtml", imprint_html, args.lang)
    pages.append(imprint_page)

    foreword_html = load_or_placeholder(
        args.foreword_file,
        "Foreword",
        "[Foreword placeholder — introductory remarks by a guest writer.]",
    )
    foreword_page = add_page(book, css, "Foreword", "foreword.xhtml", foreword_html, args.lang)
    pages.append(foreword_page)

    # ---- Introduction (optional) ----
    if args.intro_file:
        intro_text = Path(args.intro_file).read_text(encoding="utf-8").strip()
        if intro_text:
            intro_page = add_page(
                book, css, "Introduction", "intro.xhtml",
                wrap_text_as_html("Introduction", intro_text),
                args.lang,
            )
            pages.append(intro_page)

    # ---- Chapters from Ghost ----
    token = make_token(admin_key)
    for slug in args.slugs:
        post = fetch_post(session, ghost_url, token, slug)
        token = make_token(admin_key)

        title = post["title"] or slug
        chapter_slug = sanitize_filename(post.get("slug") or slug)

        authors = post.get("authors", [])
        author_name = authors[0].get("name") if authors else None

        html = post["html"] or ""
        soup = BeautifulSoup(html, "html.parser")

        # strip comments paragraph
        for hr in soup.find_all("hr"):
            next_sib = hr.find_next_sibling()
            if next_sib and next_sib.name == "p":
                link = next_sib.find("a", href=lambda h: h and "#comments" in h)
                if link:
                    next_sib.decompose()
                    hr.decompose()
                    break

        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()

        if not author_name:
            author_el = soup.find(class_="gh-post-authors") or soup.find("a", href=lambda h: h and "/author/" in h)
            if author_el:
                author_name = author_el.get_text(strip=True)

        display_title = f"{title} — {author_name}" if author_name else title

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue
            src = urljoin(ghost_url, src)
            parsed = urlparse(src)
            ext = os.path.splitext(parsed.path.split("?")[0])[1].lower() or ".jpg"
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                ext = ".jpg"

            uid = hashlib.sha1(src.encode()).hexdigest()[:12]
            fname = f"{chapter_slug}_{uid}{ext}"

            if src not in image_items:
                try:
                    data = session.get(src, timeout=15).content
                    image_items[src] = epub.EpubItem(
                        uid=f"img_{uid}",
                        file_name=f"images/{fname}",
                        media_type=media_type_for_ext(ext),
                        content=data,
                    )
                    book.add_item(image_items[src])
                except Exception:
                    continue

            img["src"] = image_items[src].file_name
            if "srcset" in img.attrs:
                del img["srcset"]
            if "sizes" in img.attrs:
                del img["sizes"]

        body_content = soup.body.decode_contents() if soup.body else str(soup)
        chapter = add_page(
            book, css, display_title, f"{chapter_slug}.xhtml",
            f"<h1>{title}</h1>\n{body_content}",
            args.lang,
        )
        pages.append(chapter)

    # ---- Back matter ----
    notes_html = load_or_placeholder(
        args.notes_file,
        "Notes",
        "[Notes placeholder — endnotes, references, etc.]",
    )
    notes_page = add_page(book, css, "Notes", "notes.xhtml", notes_html, args.lang)
    pages.append(notes_page)

    ack_html = load_or_placeholder(
        args.acknowledgements_file,
        "Acknowledgements",
        "[Acknowledgements placeholder — thank-yous and credits.]",
    )
    ack_page = add_page(book, css, "Acknowledgements", "acknowledgements.xhtml", ack_html, args.lang)
    pages.append(ack_page)

    # ---- About This Book / Colophon ----
    about_html = load_or_placeholder(
        args.about_file,
        "About This Book",
        "[About this publication placeholder — production credits, source, rights, etc.]",
    )
    about_page = add_page(book, css, "About This Book", "about.xhtml", about_html, args.lang)
    pages.append(about_page)

    # ---- finalize ----
    book.toc = tuple(pages)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    if args.cover:
        cover_page = next(p for p in pages if p.id == "cover")
        rest = [p for p in pages if p.id != "cover"]
        book.spine = [cover_page, "nav"] + rest
    else:
        book.spine = ["nav"] + pages

    epub.write_epub(args.output, book)
    print(f"wrote {args.output}")


def main():
    parser = argparse.ArgumentParser(description="Build an EPUB from Ghost members-only posts.")
    parser.add_argument("--url", default="https://flaminghydra.ghost.io", help="Ghost site URL")
    parser.add_argument("--admin-key", help="Ghost Admin API key (id:secret)")
    parser.add_argument("--set-secret", action="store_true", help="Store a key in keyring or local file")
    parser.add_argument("--set-secret-file", action="store_true", help="Store a key in local JSON file")
    parser.add_argument("--domain", help="Domain for --set-secret (defaults to --url host)")

    # metadata
    parser.add_argument("--title", default="Collected Posts", help="Book title")
    parser.add_argument("--author", help="Book author / editor (shown in Thorium info panel)")
    parser.add_argument("--publisher", help="Publisher name (shown in Thorium info panel)")
    parser.add_argument("--description", help="Book description (shown in Thorium info panel)")
    parser.add_argument("--pub-date", help="Publication date, e.g. 2026-09-29")
    parser.add_argument("--isbn", help="ISBN / book identifier")
    parser.add_argument("--lang", default="en", help="Language code")

    # content files
    parser.add_argument("--cover", help="Path to cover image (optional)")
    parser.add_argument("--copyright-file", help="Path to copyright HTML/text file")
    parser.add_argument("--imprint-file", help="Path to imprint HTML/text file")
    parser.add_argument("--foreword-file", help="Path to foreword HTML/text file")
    parser.add_argument("--intro-file", help="Path to intro HTML/text file")
    parser.add_argument("--notes-file", help="Path to notes HTML/text file")
    parser.add_argument("--acknowledgements-file", help="Path to acknowledgements HTML/text file")
    parser.add_argument("--about-file", help="Path to About This Book HTML/text file")

    parser.add_argument("--output", default="book.epub", help="Output EPUB path")
    parser.add_argument("slugs", nargs="*", help="Post slugs to include")
    args = parser.parse_args()

    if args.set_secret_file:
        store_secret_file(args)
    elif args.set_secret:
        store_secret(args)
    else:
        build_epub(args)


if __name__ == "__main__":
    main()

