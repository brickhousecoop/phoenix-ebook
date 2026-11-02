# One store for blogs, keys, users and access: Postgres, read only by the website

Every blog added a Vercel variable, and adding a user or changing a key meant a paste and a redeploy (#31). We decided that **the website reads blogs, blog keys, users and access from one Postgres database (Neon's free plan, connected by hand), with each blog key encrypted under one master key held in a Vercel variable**. With `PHOENIX_STORE_URL` set, the store is the only source. Without it, the site uses what it always did, so the local site, the tests and the command line need no database. This changes ADR 0003's "users in an environment variable", but not its sign-in: argon2id hashes and a signed cookie stay.

The website's login is read-only. Only scripts on the maintainer's own machine, holding the full-access connection and the master key, can write. The library and `SecretStore` know nothing about the store: the website decrypts a key and passes it through `SecretStore`'s existing override.

## Considered options

- **A secrets service (1Password, Doppler, Infisical):** fits the keys well, but users, blogs and access would still need a database, so two stores.
- **Inside `SecretStore`, shared with the command line:** puts a database driver and the master key into the library and every tool that uses it, and most of the store is only about the website's users.
- **Neon Auth instead of our sign-in:** would bring password resets, but it's in beta, shows no Python guide, and ties us to Neon.
- **The Vercel Marketplace install:** bills through Vercel, but adds a full-access `DATABASE_URL` to the project and offers a database branch per preview, which can linger and use up the 10 free branches.
- **Store first, old variables as backup:** a removed user could still sign in from the old list, and nothing shows which source answered.
- **A short-lived copy to ride out outages:** only covers an instance that happens to hold one, delays removals, and leaves decrypted secrets in memory.
- **Separate databases for previews:** the preview's copy would still need real keys to build anything, so it adds work without removing the main exposure.

## Consequences

- Blogs, users and keys change with no redeploy, and a removed user or replaced key takes effect on the next request, because every page reads the store.
- An outage shows a "try again in a minute" page, never a wrong-password message or a sign-out. A build reads what it needs before it starts, so one already running finishes.
- If the free plan's compute hours or data transfer run out, the database stops until next month or an upgrade. No data is lost, but the site shows "try again" meanwhile.
- A leak of the database alone doesn't expose blog keys. Someone with the database and the Vercel variables can read them, which is about today's exposure. They can't change anything.
- Previews share production's database. Table changes only add things until merged.
- Neon's free plan has no scheduled backups, so we keep our own: an export file with keys still encrypted, restored with the import script. The store only changes through scripts, so #40 exports after each change. The master key is backed up separately: lose it, and every blog's key has to be entered again.
- A key added to the store doesn't reach the command line, so someone using both keeps two copies.
- `BLOGS` stays in the code for the site without a store, so there are two lists of blogs. Once blogs are added only to the store, the code's list is only for local use.
- Nothing records who changed a key or user.
- A blog's cleanup rules stay in `phoenix_ebook/sites/`, found by the blog's address as before, until #44.
