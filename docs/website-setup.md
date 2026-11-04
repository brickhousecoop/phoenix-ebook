# Setting up the website on Vercel

How the book-building website (`web/`, see [ADR 0002](adr/0002-website-on-vercel-calls-the-library.md)) runs on Vercel, written for someone new to Vercel. It records what was done for `phoenix-ebook.vercel.app` in October 2026; follow it to set the site up again, or to check how it's set up.

## Where it runs

- **Address:** <https://phoenix-ebook.vercel.app>, behind the site's own sign-in ([ADR 0003](adr/0003-website-sign-in-own-passwords.md)): only the users set up below can build books.
- **Vercel project:** `phoenix-ebook`, on **The Brick House** team (`brickhousecoop`, Pro plan).
- **Deploys:** connected to GitHub `brickhousecoop/phoenix-ebook`. Every push to `main` deploys to production; other branches get their own preview address, behind Vercel's own sign-in (team members only) as well as the site's.
- **Configuration in the repo:**
  - `pyproject.toml`: Python 3.14, the app's entrypoint (`web.main:app`) and its dependencies;
  - `vercel.json`: the 800-second time limit, and tests left out of the deployment;
  - `.vercelignore`: an allow-list of what a command-line deploy uploads.

## Who can do what

| Step | Who |
|---|---|
| Import the repository, settings, Blob store, environment variables, protection, website users | any team **Member** |
| Let Vercel's GitHub app see the repository (only if it isn't in Vercel's list) | a GitHub organisation **Owner** |
| Spend Management cap | a Vercel team **Owner** |
| The store: Neon account, import, backups | whoever runs the site, on their own computer |

## Setting it up

1. **Check the repository is visible to Vercel.** In Vercel, choose The Brick House team, then **Add New → Project**. Under *Import Git Repository*, `brickhousecoop/phoenix-ebook` should be listed (it was). If it isn't, Vercel's GitHub app doesn't have access to it: a GitHub Owner of `brickhousecoop` grants it under GitHub → Settings → GitHub Apps → Vercel → *Repository access*. Choose only this repository.

2. **Import it.** Press **Import**, and on the next page check:
   - **Team:** The Brick House, not a personal account;
   - **Project name:** `phoenix-ebook`, which gives the address;
   - **Application preset:** *Python*. It covers FastAPI; the entrypoint comes from `pyproject.toml`;
   - **Root directory:** `./`, with build and install commands left as they are;
   - **Environment variables:** none yet. Without users nobody can sign in, and without the Ghost key nobody could build anything.

   Press **Deploy**. The first deploy goes live straight away.

3. **Protect the previews.** Project **Settings → Deployment Protection → Vercel Authentication → Standard Protection** (the default). It puts preview addresses behind Vercel's sign-in, so only team members reach them; `phoenix-ebook.vercel.app` itself is left to the site's own sign-in. Until October 2026 the site had no sign-in of its own and used *All Deployments* instead.

4. **Create the Blob store** (where finished books go). In the project, **Storage → Create Database → Blob**:
   - **Name:** e.g. `phoenix-ebook-books`;
   - **Access:** **Public**. Download links work for anyone who has them, and their addresses can't be guessed. A public store can't be made private later;
   - **Connect** it to `phoenix-ebook` for **Production** and **Preview**, keeping the variable prefix `BLOB`.

   Then check **Settings → Environment Variables** for **`BLOB_READ_WRITE_TOKEN`**. The site needs this exact variable (Vercel's Python SDK reads nothing else). Without it, every build stops with "This site isn't connected to its Blob store".

5. **Add the Ghost key.** If the site has [the store](#the-store), the key goes there and this step is skipped. Without it, **Settings → Environment Variables → Add**:
   - **Name:** `PHOENIX_SECRET_GHOST_FLAMINGHYDRA_GHOST_IO`;
   - **Value:** the Flaming Hydra Ghost **Admin API key** (`id:secret`, from Ghost Admin → Settings → Integrations), the same key the command line uses;
   - **Environments:** **Production** and **Preview** (not Development);
   - **Sensitive:** on, so the value can't be read back from the dashboard.

   Each blog has its own key, added the same way: `PHOENIX_SECRET_GHOST_HELLWORLD_GHOST_IO` for Welcome to Hell World (from its own Ghost Admin). The blogs the website builds from are listed in `BLOGS` in `web/forms.py`.

6. **Set up the users**, as in [the store](#the-store) or, without one, [Users](#users) below.

7. **Redeploy**, so the deployment picks up the variables: **Deployments → the latest → ⋯ → Redeploy**. Then open the address in a private window: you should get the site's sign-in page. Sign in, build a small book, and download it.

8. **Ask a Vercel Owner to set a Spend Management cap** (team **Settings → Billing → Spend Management**). Builds are billed by usage and there's no limit on how many people run. *(Not done yet.)*

## The store

Blogs, their keys, users and their access can live in one Postgres database ([ADR 0004](adr/0004-one-store-postgres-read-only-website.md)), so changing them needs no redeploy. With `PHOENIX_STORE_URL` set, the store is the only source, and the old variables below are ignored. Without it, the site uses those variables, or on your own computer `~/.phoenix_users.json`, your stored keys and `BLOGS` in `web/forms.py`.

| Variable | Where | What |
|---|---|---|
| `PHOENIX_STORE_URL` | Vercel, Sensitive | The website's **read-only** connection |
| `PHOENIX_STORE_MASTER_KEY` | Vercel, Sensitive, and your password manager | Decrypts blog keys. If it's lost, every blog's key has to be entered again |
| `PHOENIX_STORE_ADMIN_URL` | **Only your own computer, never Vercel or the sandbox** | The full-access connection, for the scripts. Asked for if it isn't set |

### Setting it up

1. **Create the database.** Sign up at <https://neon.com> on the Free plan, which needs no card. Create a project in the region closest to the site's Vercel region (Vercel: **Settings → Functions**). Connect it by hand, not through the Vercel Marketplace, which adds a full-access variable to the project and offers a database branch per preview.

2. **Copy the full-access connection.** On Neon's project page, press **Connect**, with **Connection pooling** on (the host name has `-pooler` in it). The read-only logins will use the same route.

3. **Load it,** on your own computer, in the repository, with the website's requirements installed (`uv pip install -r requirements-web.txt`):

   ```bash
   .venv/bin/python scripts/store_admin.py import
   ```

   It asks for the connection, without showing what you type. It reads `~/.phoenix_users.json` and the keys wherever this computer keeps them; `--users-file` and `--keys-file` name other files. The blogs come from `BLOGS` in the code. On the first import every user gets every blog; after that, users keep the blogs they have and new users start with none. If anything is missing it says what, and writes nothing.

   It **shows these only once**, so keep the output until they're stored:
   - **the master key:** put it in your password manager;
   - **the website's read-only connection;**
   - **a second read-only connection,** for checking the store from the development sandbox. Store it from your own shell in the sandbox, never in a chat. It can be deleted at any time without touching the site (in Neon's SQL editor: `DROP OWNED BY phoenix_check; DROP ROLE phoenix_check;`).

4. **Try it on a preview.** Add `PHOENIX_STORE_URL` and `PHOENIX_STORE_MASTER_KEY` for **Preview only**, and redeploy a preview. Check you can sign in and build from each blog.

5. **Connect production.** Add the same two for **Production** and redeploy, leaving `PHOENIX_USERS` and the `PHOENIX_SECRET_GHOST_*` variables in place. **The way back:** remove `PHOENIX_STORE_URL` from Production and redeploy, and the site returns to the old variables.

6. **Delete the old variables** after a few days of normal use, redeploy, and build once more.

### Who builds from which blog

With the store, each user builds only from the blogs they've been given (#41). The form names only their blogs, addresses from any other blog are refused before anything is fetched (and logged as `refused`), and a user with no blogs sees a message instead of the form. Without the store (your own computer, tests), everyone may use every blog.

```bash
.venv/bin/python scripts/store_admin.py users                                # each user and their blogs
.venv/bin/python scripts/store_admin.py grant ana@example.org hellworld      # let Ana build from Welcome to Hell World
.venv/bin/python scripts/store_admin.py revoke ana@example.org hellworld     # and stop her
```

A blog can be named by its name, one of its addresses' hosts (`www.welcometohellworld.com`) or the first part of its API's host (`hellworld`, `flaminghydra`). A change counts from the user's next page, with no redeploy, and each one is exported straight away to `~/.phoenix_store_backups/`. Adding a user with `scripts/add_user.py` and running the import gives them no blogs until you grant one.

### Changing things

Until #40 adds the rest of the commands, edit your lists and run the import again, with `PHOENIX_STORE_MASTER_KEY` set in your shell. It makes the store match its inputs, **removing whatever they don't list**, and it takes effect on the next request, with no redeploy. It runs in one transaction: all of it happens, or none. `--new-login-passwords` replaces the read-only passwords, if you've lost them, and shows the new ones; the website's then has to change in Vercel.

### Backups

Neon's Free plan keeps 6 hours of history and has no scheduled backups, so export after each change:

```bash
.venv/bin/python scripts/store_admin.py export                    # a dated file in ~/.phoenix_store_backups/
.venv/bin/python scripts/store_admin.py import --from-export FILE # restores it
```

The export can use either connection. It's readable only by you, and the blog keys stay encrypted in it, but it holds emails and password hashes, so **never put it in the repository or the public Blob store**. A restore makes the store match the file exactly, and needs the same master key as when the file was made.

### When it goes wrong

- **"Try again in a minute":** the site couldn't read the store. Check Neon's project page, and the site's logs for `store: couldn't read it`. On the Free plan, using up the 100 compute hours or 5 GB of data transfer stops the database until next month or an upgrade, with no data lost. A wrong `PHOENIX_STORE_URL` looks the same.
- **"has PHOENIX_STORE_URL but no PHOENIX_STORE_MASTER_KEY":** add the master key to Vercel for that environment, and redeploy.
- **"was encrypted with master key …":** `PHOENIX_STORE_MASTER_KEY` isn't the one that encrypted the keys. Restore it from your password manager.

## Users

Without [the store](#the-store), the site's users are an email address and a password each. The passwords are generated, never chosen, and only their argon2id hashes are kept, in two environment variables (**Settings → Environment Variables**, both **Sensitive**, for **Production** and **Preview**):

- **`PHOENIX_USERS`**: the list of users, as JSON (`{"ana@example.org": "$argon2id$…"}`). Without it, or if it isn't valid JSON, nobody can sign in, and the sign-in page says so.
- **`PHOENIX_SESSION_SECRET`**: signs the cookie that keeps people signed in for 30 days. Any long random value, e.g. from `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Changing it signs everyone out. Without it on Vercel, nobody can sign in.

To add someone, or give them a new password, run this on your own computer, in the repository:

```bash
.venv/bin/python scripts/add_user.py ana@example.org
```

It prints the new password **once**: send it to the person privately. It also prints the whole new `PHOENIX_USERS` value: paste it over the old one in Vercel, then **redeploy** (variables only reach new deployments). `--remove ana@example.org` takes someone out the same way; they're signed out at their next click after the redeploy.

The script keeps its copy of the list in `~/.phoenix_users.json`, readable only by you. Vercel's copy is the real one: if two people manage users from different computers, one paste can undo the other's change, so let one person do it. `--users-file PATH` uses another file, and `--users-file -` reads the list from standard input and writes the changed list to standard output. Nobody can change or reset their own password; ask whoever manages users.

When the site runs on your own computer, it uses `~/.phoenix_users.json` if `PHOENIX_USERS` isn't set. Without `PHOENIX_SESSION_SECRET` there, restarting the site signs you out.

**Logs.** Each build is logged with who started it, the site, the number of posts and whether a book came out (`build: user=… site=… posts=… built`, or `refused` when the blog isn't theirs); each failed sign-in with the email address tried (`sign-in failed: email=…`), never the password. See [Logs](#logs) for where to read them. Nothing limits how many builds a user runs.

**Book links are still public.** Anyone a user passes a download link to can open the book, paid posts and drafts included (#37).

## Testing from a script

Scripts can sign in like a person: `POST /sign-in` with `email` and `password`, and an `Origin` header naming the site (every form sent to the site must have one), then send the `phoenix_session` cookie that comes back. Use a user made for the test, and remove it afterwards.

Preview addresses are also behind Vercel's sign-in, which stops scripts. For tests run from a sandbox or a CI job, a member can create a bypass under **Settings → Deployment Protection → Protection Bypass for Automation**. Send it as the `x-vercel-protection-bypass` header. Anyone holding it gets past the sign-in, so **delete it when the testing is done**.

## Logs

- **Dashboard:** the project's **Logs** tab lists every request with its status and duration; errors from the site (tracebacks from a failed build) are in each request's details.
- **Command line:** `vercel logs --scope brickhousecoop --project phoenix-ebook` (add `--follow` to watch, or `--level error --since 1h`). This needs a Vercel login or token. Use a token scoped to just this project, with an expiry (Account Settings → Tokens → Scope: The Brick House → phoenix-ebook), rather than a full-account login.

## What to expect

Measured on 2026-10-07, building recent Flaming Hydra posts on the live site and, for comparison, in the development sandbox:

| Book | On Vercel | In the sandbox | Book size |
|---|---|---|---|
| 5 posts | 5 s | 8 s | — |
| 100 posts (the most a book can have) | 110 s | 217 s | 81 MB |
| 100 Welcome to Hell World posts, measured 2026-11-01 (more images per post) | under 7 min | about 1,200 s | 147 MB |

- **Time limit:** 800 seconds per request. A 100-post book uses about a seventh of it on Flaming Hydra, and about half on Welcome to Hell World.
- **Upload limit:** Vercel refuses any request over 4.5 MB with "413 Request Entity Too Large" before the site sees it. The form's page totals the chosen files first and says so if they're over 4.4 MB.
- **A closed tab doesn't stop a build.** Tested on 2026-10-07: a 20-post build disconnected after a second still finished, and its complete book was saved to the Blob store. Nobody sees the link, though, so the answer is to build again. That uses another build's worth of time, which is one reason for the spend cap.
- **Storage:** books are kept in the Blob store with no expiry; nothing deletes them yet (#33). Each book's folder also holds its uploads (cover, content pages), so a rebuild can reuse them.
