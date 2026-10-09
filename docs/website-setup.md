# Setting up the website on Vercel

How the book-building website (`web/`, see [ADR 0002](adr/0002-website-on-vercel-calls-the-library.md)) runs on Vercel, written for someone new to Vercel. It records what was done for `phoenix-ebook.vercel.app` in October 2026; follow it to set the site up again, or to check how it's set up.

## Where it runs

- **Address:** <https://phoenix-ebook.vercel.app>, behind Vercel's sign-in (team members only).
- **Vercel project:** `phoenix-ebook`, on **The Brick House** team (`brickhousecoop`, Pro plan).
- **Deploys:** connected to GitHub `brickhousecoop/phoenix-ebook`. Every push to `main` deploys to production; other branches get their own preview address, also behind the sign-in.
- **Configuration in the repo:**
  - `pyproject.toml`: Python 3.14, the app's entrypoint (`web.main:app`) and its dependencies;
  - `vercel.json`: the 800-second time limit, and tests left out of the deployment;
  - `.vercelignore`: an allow-list of what a command-line deploy uploads.

## Who can do what

| Step | Who |
|---|---|
| Import the repository, settings, Blob store, environment variables, protection | any team **Member** |
| Let Vercel's GitHub app see the repository (only if it isn't in Vercel's list) | a GitHub organisation **Owner** |
| Spend Management cap | a Vercel team **Owner** |

## Setting it up

1. **Check the repository is visible to Vercel.** In Vercel, choose The Brick House team, then **Add New → Project**. Under *Import Git Repository*, `brickhousecoop/phoenix-ebook` should be listed (it was). If it isn't, Vercel's GitHub app doesn't have access to it: a GitHub Owner of `brickhousecoop` grants it under GitHub → Settings → GitHub Apps → Vercel → *Repository access*. Choose only this repository.

2. **Import it.** Press **Import**, and on the next page check:
   - **Team:** The Brick House, not a personal account;
   - **Project name:** `phoenix-ebook`, which gives the address;
   - **Application preset:** *Python*. It covers FastAPI; the entrypoint comes from `pyproject.toml`;
   - **Root directory:** `./`, with build and install commands left as they are;
   - **Environment variables:** none yet. Until protection is on, the site is public, and without the Ghost key nobody can do anything with it.

   Press **Deploy**. The first deploy goes live straight away.

3. **Turn on the sign-in.** Project **Settings → Deployment Protection → Vercel Authentication → All Deployments**. The default, *Standard Protection*, covers only preview addresses, **not** `phoenix-ebook.vercel.app`. To check, open the address in a private window: you should get Vercel's sign-in page, not the form.

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

6. **Redeploy**, so the deployment picks up the variables: **Deployments → the latest → ⋯ → Redeploy**. Then open the address (signed in), build a small book, and download it.

7. **Ask a Vercel Owner to set a Spend Management cap** (team **Settings → Billing → Spend Management**). Builds are billed by usage and there's no limit on how many people run. *(Not done yet.)*

## Testing from a script

Vercel's sign-in also stops scripts. For tests run from a sandbox or a CI job, a member can create a bypass under **Settings → Deployment Protection → Protection Bypass for Automation**. Send it as the `x-vercel-protection-bypass` header. Anyone holding it gets past the sign-in, so **delete it when the testing is done**.

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
- **Storage:** books are kept in the Blob store with no expiry; nothing deletes them yet. Each book's folder also holds its uploads (cover, content pages), so a rebuild can reuse them.
