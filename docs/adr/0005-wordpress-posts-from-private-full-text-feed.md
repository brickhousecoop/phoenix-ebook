# WordPress posts come from the blog's private full-text feed

Our first WordPress blog, Defector, runs headless WordPress (Lede, on WordPress VIP) with its paywall in the front end (#49). We decided that **a WordPress blog's posts come from its private full-text RSS feed, one post per request by adding WordPress's own `name=<slug>` to the feed address, and that the whole feed address is the blog's secret**. The platform is `wordpress-feed`. The blog's address is its public site (`https://defector.com`), and the feed must be on the same site.

The feed gives each post's full text (`content:encoded`), every co-author (`dc:creator`), its categories and tags, its date (UTC) and its lead image (`media:content`). It doesn't give author links, the site's time zone, or the lead image's alt text or caption, so bylines are names only, the time zone is a setting in the blog's rules file, and lead images are reported as missing alt text.

## Considered options

- **The REST API with an Editor account and an Application Password:** full access, structured blocks and bylines, but it needs an editor login from the publisher and reaches drafts and everything else an editor can, far more than a book needs.
- **The public REST API or the public feed:** excerpts only for paid posts.
- **Paging the feed (`?paged=N`), or one day's feed per post (`/YYYY/MM/DD/feed/…`):** paging stops after about 100 pages (1,000 posts), and a day's feed needs the post's date first (one more request, to the public REST API, which on Defector is on another host). `name=` fetches any post, back to 2020, in one request, and answers 404 for one that doesn't exist.
- **Keeping the key apart from the address:** the feed id in the path is just as secret as the `key` parameter, and other plugins put their tokens elsewhere, so the address as given is the secret.

## Consequences

- The feed address is stored like an API key (`SecretStore`, or encrypted in the website's store with `blog set-key`) and never logged, printed, committed, put in an error message or kept in the store's `url` column.
- Dates come in UTC (`Platform.dates_in_utc`); a blog's `timezone` setting converts them, and without one the build says so.
- What a blog leaves out by category (`skip_categories`: podcasts, crosswords) is data in its rules file, and each skipped post is reported.
- Block HTML is converted to canonical chapter HTML in the platform (ADR 0001); a block it doesn't know keeps its contents and is reported once per book, so new markup shows up.
- Other WordPress blogs work if their feed has the full text and accepts `name=`. A plugin that puts its token somewhere else still works, since the address is kept as given.
