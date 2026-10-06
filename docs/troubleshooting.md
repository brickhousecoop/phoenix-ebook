# Troubleshooting

What each message from `build_epub.py` means and what to do about it. Search this page for the start of the message on your screen.

- [The build stopped](#the-build-stopped): `error: …`, no book written
- [Warnings after the build](#warnings-after-the-build): the book was written; something in it may need a look
- [Exit codes and `--debug`](#exit-codes-and---debug)
- [Firewalls and proxies](#firewalls-and-proxies)

## The build stopped

The build checks your settings before fetching anything, so most of these appear within a second. An existing book at the output path is never damaged: books are written to a temporary file and renamed into place only when complete.

### Your API key

**`error: The Ghost Admin API key must look like id:secret`**
The stored key (or `--admin-key`) isn't an Admin API key. In Ghost Admin, open Settings → Integrations → your custom integration and copy the **Admin API key** (two parts separated by a colon; not the Content API key). Store it again with `--set-secret`.

**`error: Ghost rejected the Admin API key for <site> (HTTP 401)`**
The key is wrong, was regenerated, or its integration was deleted. Copy the current Admin API key from Ghost Admin and store it again:
`build_epub.py --set-secret --platform ghost --url <site>`.

**`error: <site> redirected to <other address>, and the key isn't sent across redirects`**
The `--url` you gave forwards somewhere else (usually the public website, e.g. `https://flaminghydra.com`, instead of the Ghost address). Use the address the message suggests, normally `https://<site>.ghost.io` as shown in Ghost Admin.

**`error: No secret found for platform='ghost' domain='…'`**
No key is stored for this site. The message lists the options: pass `--admin-key`, set the environment variable it names, or store the key once with `--set-secret`.
- **No keyring** (containers, servers, some Linux desktops): `--set-secret` falls back to a file, but if it fails, use `--set-secret-file`, which writes `~/.phoenix_secrets.json` readable only by you.
- **Your key used to work and now seems lost:** older versions kept keys in `~/.ghost_epub_secrets.json`. They move into `~/.phoenix_secrets.json` automatically the first time they're needed, and the old file is renamed `~/.ghost_epub_secrets.json.migrated`. If you copied your home folder from another machine, copy `~/.phoenix_secrets.json` too.
- The key is stored per host name: `https://flaminghydra.ghost.io` and `https://flaminghydra.com` are different sites to the key store.

**`error: --set-secret needs --url (the site) or --domain`**
Say which site the key is for: add `--url https://<site>.ghost.io`.

### Reaching the site

**`error: can't reach <site>: ConnectionError`** (or `Timeout`)
No connection: check the `--url` spelling and your network. Behind a firewall, see [Firewalls and proxies](#firewalls-and-proxies).

**`error: can't reach <site> (HTTP …)`**
The site answered with an error. If the message continues with an explanation (e.g. a firewall's "Approval required for …"), it comes from whatever answered; follow it. Otherwise the address probably isn't a Ghost site's Admin API: check `--url`.

**`error: <site> didn't answer like a Ghost Admin API`**
Something answered, but not Ghost. Usually `--url` is the public website rather than the Ghost address (`https://<site>.ghost.io`, shown in Ghost Admin), or a captive portal or proxy page intercepted the request.

### Posts

**`error: 1 post not found on <site>: "…"`** (or `N posts not found`)
These slugs don't exist (all missing ones are listed at once). A slug is the last part of a post's URL: `https://flaminghydra.com/fender-bender/` → `fender-bender`. Drafts and scheduled posts can't be fetched until published.

### Manifest file

**`error: manifest not found: …`**: the path after `--manifest` doesn't exist.
**`error: … isn't UTF-8 text; save it as UTF-8`**: re-save the file as UTF-8 (most editors offer this under "Save As" or "Encoding").
**`error: … isn't valid TOML: …`**: a syntax error; the rest of the message gives the line and column. Common causes: an unquoted text value, or a URL key in `[alt_text]` without quotes.
**`error: …: a [source] table with platform and url is required`**: add the `[source]` table shown in the message (see the README's *Manifest file* section).

### Book settings

**`error: --isbn / [book] isbn: '…' is not a valid ISBN-10 or ISBN-13`**: the length or check digit is wrong; copy the ISBN again (hyphens and spaces are fine).
**`error: --issn / [book] issn: '…' is not a valid ISSN`**: as above, for the 8-character ISSN.
**`error: --issn / [book] issn identifies a series, so it needs --series`**: an ISSN belongs to a series (all its issues), so give the series name too.
**`error: --series-number / [book] series_number needs --series`**: give the series the number belongs to.
**`error: --editor and --author disagree`**: they are the same setting (`--author` is the old name); give just one.
**`error: unknown platform '…'; available: …`** / **`unknown processor '…'`**: check the spelling against the list in the message.

### Content files and the cover

**`error: --foreword-file: file not found: …`** (or another content option, or `--cover`): the path doesn't exist. Relative paths are taken from the folder you run the command in, also when they're in a manifest.
**`error: --foreword-file: … isn't UTF-8 text`**: re-save the file as UTF-8.

### Output file

**`error: can't write …: the folder … doesn't exist`**: create the folder, or fix `--output`.
**`error: can't write …: no permission to write in …`**: write somewhere you're allowed to, e.g. your home folder.
**`error: can't write …: it's a folder`**: `--output` must be a file name ending in `.epub`.
**`error: can't write …: …`** (another reason, e.g. a full disk): the system's reason follows the colon.

## Warnings after the build

The book was written and is usable; the build still exits with code 0. Each problem is printed as a `warning:` line with the post's slug and the URL involved, followed by summary lines like the ones below. None of these warnings need fixing for the book to work; they tell you what the build changed or couldn't do, so you can check.

### `image-download-failed`

**`N images could not be downloaded and were left out; see warnings above`**
Each warning gives the image's URL and the reason: an HTTP error, a timeout, or "response is not a valid image" (e.g. a login page instead of a picture). Temporary failures were already retried once.
- **Try the build again**; network blips are the usual cause.
- **"HTTP 403" with a firewall message** (e.g. "Approval required for …"): the host is blocked; see [Firewalls and proxies](#firewalls-and-proxies).
- **"HTTP 404"**: the image is gone from where the post points. Fix or remove it in the post in Ghost.
- **"thumbnail lookup failed"**: a link card's thumbnail (TikTok, Vimeo, Spotify) couldn't be looked up. The card is still in the book with its title and link; only the picture is missing. "HTTP 404" here usually means the video or episode was deleted.

### `image-missing-alt`

**`N images have no alt text`**
Screen readers can't describe these images, and the book can't claim "text descriptions for images" in its accessibility metadata. The build prints a ready-made `[alt_text]` block after the warnings: paste it into your manifest and write a short description for each image (or leave `""` for an image that's purely decorative). Better still, add the alt text in Ghost, so the website benefits too. See the README's *Alt text* section for how to write it.

### `image-suspicious-alt`

**`N images have alt text that looks unhelpful`**
The alt text is a file name (`IMG_5799.jpg`), a generic word ("image"), or a copy of the caption (read twice by screen readers). These are included in the `[alt_text]` block; replace them with a description.

### `alt-override-unused`

**`N [alt_text] entries match no image in the book`**
An `[alt_text]` entry in your manifest names an image URL that isn't in this book: a typo, an image removed from the post, or a post no longer in the slug list. Copy the URL again from the build's `[alt_text]` block, or delete the entry.

### `call-to-action`

**`N website calls-to-action (subscribe/support) were unlinked or kept; review the warnings above`**
Website calls-to-action were taken out of the book (Flaming Hydra books). Listed are paragraphs whose subscribe or sign-up link was removed (the words stay, e.g. "why not subscribe?"), and promotional images kept because they're in the middle of a post. If a sentence now reads oddly, edit the post or leave the post out.

### `embed-removed`

**`N embedded forms or polls were left out; see warnings above`**
A form or poll (Tally) only works on the website, so it isn't in the book. Posts built around one (games, reader polls) usually don't belong in a book: consider removing the post from your slug list.

### `embed-unknown`

**`N unrecognised embeds became link cards; see warnings above`**
Embedded content phoenix-ebook doesn't recognise (e.g. a Dailymotion or PBS player) became a generic "Embedded content" card linking to it. Check that the card reads sensibly in the chapter.

### `link-repaired`

**`N links in posts were repaired; check the warnings above`**
A link in a post was broken and the build fixed it. Each warning gives the original address, the new one and why: a link to a section of the post that didn't exist was pointed at the section it most likely meant (matched by name or heading), or a mistyped address was corrected (spaces, quotes, a missing `https://`). **Check the section matches**: a repair can pick the wrong section. To fix one for good, correct the link in the post in Ghost.

### `link-unlinked`

**`N links in posts pointed nowhere and were unlinked (words kept); see warnings above`**
A link's target couldn't be found (e.g. a digest's contents pointing at a section that isn't in the post) or its "address" wasn't an address at all (e.g. "Washington Post, January 29th, 2018"). The words are still in the book, without the link.

### `link-empty-removed`

**`N empty links were removed; see warnings above`**
A link with no text (invisible on the website too) was removed; nothing visible changed. If the post meant to link some words, fix it in Ghost.

### The `[alt_text]` block

At the very end of the output, a commented-out block starting `# Add to your manifest and fill in …` lists every image with missing or unhelpful alt text. Remove the `#` signs when you paste it into your manifest under `[alt_text]`.

## Exit codes and `--debug`

| Code | Meaning |
|---|---|
| 0 | The book was written (there may be warnings) |
| 1 | The build stopped with an `error:` message (see above), or a bug (see below) |
| 2 | The command line itself is wrong (e.g. no slugs and no `--manifest`); `--help` lists every option |
| 130 | Interrupted with Ctrl-C; no book was written and an existing one is untouched |

Add `--debug` to see the full traceback behind an `error:` message.

If the build ends with **`error: unexpected failure, which is a bug in phoenix-ebook`** and a traceback, that's not something you did wrong. Please [open an issue](https://github.com/buffystruggles/phoenix-ebook/issues) with the traceback and the command you ran (leave out your API key).

## Firewalls and proxies

On a network that blocks or must approve outbound connections, a block shows up as:
- `error: can't reach <site> …` before anything is fetched, when the Ghost site itself is blocked;
- `image-download-failed` warnings with **HTTP 403** (often with the firewall's own message), when an image or thumbnail host is blocked: the book is written without those pictures.

A build may contact:

| Host | For |
|---|---|
| The Ghost site from `--url` (e.g. `flaminghydra.ghost.io`) | Posts and the site's timezone (Admin API) |
| `storage.ghost.io` | Images on Ghost(Pro) sites; self-hosted sites serve images from their own address |
| `i.ytimg.com` | YouTube thumbnails |
| `www.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokcdn-us.com` | TikTok thumbnail lookup, then the thumbnail |
| `vimeo.com`, `i.vimeocdn.com` | Vimeo thumbnail lookup, then the thumbnail |
| `open.spotify.com`, `*.scdn.co` | Spotify thumbnail lookup, then the artwork |
| Other image hosts | Images that posts embed from other sites, and bookmark-card thumbnails (the linked site's image server, e.g. `i.guim.co.uk` for The Guardian) |

Allow the hosts your books need; everything except the Ghost site is optional (missing pictures are reported, never fatal).
