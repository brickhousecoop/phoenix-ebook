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

5. **Add the Ghost key.** **Settings → Environment Variables → Add**:
   - **Name:** `PHOENIX_SECRET_GHOST_FLAMINGHYDRA_GHOST_IO`;
   - **Value:** the Flaming Hydra Ghost **Admin API key** (`id:secret`, from Ghost Admin → Settings → Integrations), the same key the command line uses;
   - **Environments:** **Production** and **Preview** (not Development);
   - **Sensitive:** on, so the value can't be read back from the dashboard.

6. **Set up the users**, as in [Users](#users) below.

7. **Redeploy**, so the deployment picks up the variables: **Deployments → the latest → ⋯ → Redeploy**. Then open the address in a private window: you should get the site's sign-in page. Sign in, build a small book, and download it.

8. **Ask a Vercel Owner to set a Spend Management cap** (team **Settings → Billing → Spend Management**). Builds are billed by usage and there's no limit on how many people run. *(Not done yet.)*

## Users

The site's users are an email address and a password each. The passwords are generated, never chosen, and only their argon2id hashes are kept, in two environment variables (**Settings → Environment Variables**, both **Sensitive**, for **Production** and **Preview**):

- **`PHOENIX_USERS`**: the list of users, as JSON (`{"ana@example.org": "$argon2id$…"}`). Without it, or if it isn't valid JSON, nobody can sign in, and the sign-in page says so.
- **`PHOENIX_SESSION_SECRET`**: signs the cookie that keeps people signed in for 30 days. Any long random value, e.g. from `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Changing it signs everyone out. Without it on Vercel, nobody can sign in.

To add someone, or give them a new password, run this on your own computer, in the repository:

```bash
.venv/bin/python scripts/add_user.py ana@example.org
```

It prints the new password **once**: send it to the person privately. It also prints the whole new `PHOENIX_USERS` value: paste it over the old one in Vercel, then **redeploy** (variables only reach new deployments). `--remove ana@example.org` takes someone out the same way; they're signed out at their next click after the redeploy.

The script keeps its copy of the list in `~/.phoenix_users.json`, readable only by you. Vercel's copy is the real one: if two people manage users from different computers, one paste can undo the other's change, so let one person do it. `--users-file PATH` uses another file, and `--users-file -` reads the list from standard input and writes the changed list to standard output. Nobody can change or reset their own password; ask whoever manages users.

When the site runs on your own computer, it uses `~/.phoenix_users.json` if `PHOENIX_USERS` isn't set. Without `PHOENIX_SESSION_SECRET` there, restarting the site signs you out.

**Logs.** Each build is logged with who started it, the site, the number of posts and whether a book came out (`build: user=… site=… posts=… built`); each failed sign-in with the email address tried (`sign-in failed: email=…`), never the password. See [Logs](#logs) for where to read them. Nothing limits how many builds a user runs.

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

- **Time limit:** 800 seconds per request, so a 100-post book uses about a seventh of it.
- **Upload limit:** Vercel refuses any request over 4.5 MB with "413 Request Entity Too Large" before the site sees it. The form's page totals the chosen files first and says so if they're over 4.4 MB.
- **A closed tab doesn't stop a build.** Tested on 2026-10-07: a 20-post build disconnected after a second still finished, and its complete book was saved to the Blob store. Nobody sees the link, though, so the answer is to build again. That uses another build's worth of time, which is one reason for the spend cap.
- **Storage:** books are kept in the Blob store with no expiry; nothing deletes them yet (#33). Each book's folder also holds its uploads (cover, content pages), so a rebuild can reuse them.
