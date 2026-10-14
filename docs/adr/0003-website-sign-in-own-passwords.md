# Website sign-in: our own email-and-password sign-in, users in an environment variable

The website needs its own sign-in before it can go public (#34), because the Ghost key behind it reads paid posts and drafts. For version 1, only the Flaming Hydra team are **users**, and every user may build from any site the website has a key for. We decided that **the website checks an email address and password itself (argon2id hashes, a signed 30-day cookie), with the user list kept as JSON in a Sensitive Vercel environment variable (`PHOENIX_USERS`)**. On your own machine the list falls back to `~/.phoenix_users.json`, which `scripts/add_user.py` maintains. If neither exists, nobody can sign in. Sign-in lives only in `web/`, in front of the form → `BookSpec` → `build()` seam, so the library knows nothing about it.

Later, anyone may sign in and build from their own sites with their own keys. The design keeps that open: users are identified by email address, not by Ghost, and a single check decides which sites a user may build from.

## Considered options

- **Google (or GitHub) sign-in**: less for us to secure, but needs a Google Cloud OAuth app and assumes the whole team uses Google.
- **Email magic links**: suit "anyone may sign in" well, but need an email provider, a domain whose DNS we control, and a store for used links. Likely to be added at that stage, next to passwords.
- **A hosted service (e.g. Clerk)**: handles sign-up and recovery, but adds a vendor for a handful of users.
- **Ghost's own staff or member login**: ties sign-in to one platform, which rules out other sites later.
- **Users in Edge Config or Upstash Redis**: users could change without a redeploy, but that needs a store. The environment variable is enough for a few users, and users are read in one place, so moving later is small.

## Consequences

- Adding or removing a user, or resetting a password, means editing `PHOENIX_USERS` and redeploying. Users can't change their own passwords. The admin hands out generated passwords.
- With no store there are no failed-attempt limits. Generated passwords and argon2id's slow hashing make guessing impractical. Failed sign-ins are logged.
- `~/.phoenix_users.json` is a copy, and Vercel holds the real list. Two people managing users from different machines can overwrite each other's changes.
- Book links stay public Blob links in version 1. A user who shares a link shares the book. Private books (Vercel's private Blob with signed links) are a separate issue, needed before people outside the team can sign in.
- Vercel Authentication moves to preview deployments only once the sign-in is checked on production.
