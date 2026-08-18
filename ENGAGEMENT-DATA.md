# Updating the Engagement numbers (~5 min/week)

The Engagement section measures **real** engagement — likes, comments, shares, saves ÷
**reach** — not just followers. Those numbers aren't public/scrapable, so they're entered
by hand from each platform's own analytics. Edit `data/stats.json` → the `"engagement"`
block, then set `"is_sample": false` and update `"updated"`.

For each platform, use the **last 30 days** and enter these fields:
`reach`, `posts`, `likes`, `comments`, `shares`, `saves`.
(`reach` = reach / views / impressions depending on platform. YouTube & LinkedIn have no
`saves` — leave 0.)

### Where to read each number

**Instagram** — app → *Professional dashboard* → *Insights* → set range to **Last 30 days**.
- `reach` = Accounts reached · `posts` = number of posts in the period
- `likes` / `comments` / `shares` / `saves` = the "Interactions" breakdown (Content
  interactions → Likes, Comments, Shares, Saves)

**TikTok** — app → *Profile* → ☰ → *Creator tools* → *Analytics* → **Last 28 days**.
- `reach` = Video views · `posts` = videos posted
- `likes` / `comments` / `shares` = Overview totals · `saves` = sum of "Saved" (Content tab)

**YouTube** — *YouTube Studio* → *Analytics* → **Last 28 days**.
- `reach` = Views · `likes` / `comments` = Overview
- `shares` = *Analytics → Interactions* (Shares) · `saves` = 0 (n/a)

**LinkedIn** — Page → *Analytics → Content/Updates* → **Last 30 days**.
- `reach` = Impressions · `likes` = Reactions · `comments` = Comments · `shares` = Reposts
- `saves` = 0 (n/a)

### The math (for reference)
- **True engagement rate** = (likes + comments + shares + saves) ÷ reach × 100
- Shown per platform + an overall rate, compared against the published benchmarks below.
  **Saves + shares** are highlighted — they carry the most algorithmic weight in 2026.

## Benchmarks

The dashboard compares SOMBA's numbers to **published industry benchmarks** (third-party
averages that say what "normal" looks like, mostly for the higher-education sector).
They live in `data/stats.json` → the `"benchmarks"` block. The hourly update script never
touches that block, so edits to it are safe.

**The one rule that matters — `basis`.** Different publishers measure engagement rate
differently: per **follower**, per **reach**, per **view**, per **impression**. Comparing a
per-follower benchmark to a per-reach rate is apples-to-oranges. Every benchmark entry has
a `basis` field, and the dashboard only ever compares it to a SOMBA rate computed the same
way — automatically. So when updating a number, make sure `basis` matches how the source
measured it.

- For `"basis": "followers"` benchmarks the dashboard computes:
  **per-follower rate** = ((likes + comments + shares + saves) ÷ posts) ÷ followers × 100
  (i.e., average engagement per post, relative to follower count — Rival IQ's method).
- `reach` / `views` / `impressions` benchmarks compare against the True engagement rate
  above (TikTok's `reach` field *is* views; LinkedIn's *is* impressions).

**To update a benchmark** (e.g., when Rosie shares her Hootsuite/Sprout numbers): edit only
`value`, `source`, `source_url`, `data_period`, and `retrieved` on the matching entry.
Don't change `basis` unless the new source measures differently. Every entry is shown with
its citation under "Benchmark sources" at the bottom of the Engagement section.

**`"youtube": {}` is intentional** — no publisher we could cite states a higher-ed YouTube
benchmark. Leave it empty rather than inventing one; the dashboard handles it gracefully.

> Optional future automation: if the Instagram account is a Business/Creator account, the
> Meta Graph API can pull IG (and Facebook) engagement automatically. TikTok/LinkedIn stay
> manual. Ask when you want that wired up.
