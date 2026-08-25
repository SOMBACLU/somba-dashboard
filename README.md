# SOMBA Social Media Dashboard

A one-page website showing follower growth for the Cal Lutheran School of
Management Brand Ambassador program (SOMBA) across Instagram, TikTok,
YouTube, and LinkedIn (Facebook slot ready for later).

**It runs itself.** Every hour a free automation reads the latest follower
counts and publishes any changes. No logins, no numbers to type, nothing to
run. You never have to touch it.

---

## How it works (so you know, not because you need to do anything)

1. A scheduled job on GitHub (called a "GitHub Action") wakes up every hour.
2. It reads the public follower counts from each platform's page (YouTube
   uses Google's official API).
3. If any number changed, it saves them into `data/stats.json` and commits
   that change. Quiet hours change nothing and publish nothing.
4. Render sees the change and republishes the live site automatically.

If a platform can't be read one hour (they occasionally block automated
visitors — Instagram most often), that platform simply keeps its last known
number and the card shows a small "reused" note. If that goes on for 3+ days
the note turns red and a GitHub issue is opened automatically so someone
actually finds out.

---

## The only one-time setup: connect it to Render

This is the single human step, and it's a few clicks:

1. Go to <https://dashboard.render.com>
2. **New → Static Site**
3. Connect the **somba-dashboard** GitHub repository
4. **Publish directory**: type a single dot — `.`
5. Leave **Build Command** blank
6. Click **Create Static Site**

Render gives you the public web address (like `somba-dashboard.onrender.com`).
From then on it updates on its own.

---

## Want to refresh it right now instead of waiting for the next hour?

Two easy ways, both optional:

- **On GitHub**: open the repo → **Actions** tab → **Update SOMBA stats** →
  **Run workflow**. It fetches fresh numbers and republishes in about a minute.
- **On your Mac**: double-click **`weekly-update.command`** in this folder.

## What the dashboard shows

- **Total audience** — everyone following SOMBA anywhere, added together
- **Platform cards** — followers per platform with change since the previous day
- **Growth trend charts** — YouTube views, average views per video, content
  published, and TikTok likes over time, plus a mini growth line on every
  platform card (these appear once there are at least two days of data)
- **Growth pace** — new followers per week, fastest-growing platform, and a
  milestone projection ("Instagram passes 1,000 followers by ~…")
- **Engagement** — how much the content actually moves people, not just how
  many follow. Each platform gets a **meter row**: a coloured bar for SOMBA and a
  gold line for the **published industry benchmark** (a third-party average for the
  higher-education sector, e.g. from Rival IQ or Hootsuite). The gold line sits at
  the same spot in every row, so a bar past the line means "above benchmark" at a
  glance. Posting pace gets its own rows. Every benchmark is cited under
  "Benchmark sources" on the page; to update the values (e.g. from a
  Hootsuite/Sprout export), see the Benchmarks section in `ENGAGEMENT-DATA.md`.
- **Recent content** — the latest YouTube uploads ranked by views (pulled from
  YouTube's official public feed)
- **Audience mix** — a donut chart of which platform holds how much of the
  audience
- **Who we reach** — age, gender, and location breakdown. **Right now these
  are illustrative estimates** (the section's note line says so) because
  platforms only show real demographics to the account owner inside their
  apps. See below for swapping in the real numbers.
- **The growth playbook** — an interactive checklist: switch between platforms,
  tap actions as they go live, and watch the progress bars fill. Progress is
  saved in each viewer's own browser (your checkmarks won't appear on someone
  else's computer).

## Replacing the estimated demographics with real numbers

The age/gender/location numbers are placeholders until someone with access to
the SOMBA accounts copies the real ones from the apps (about 10 minutes):

1. In the Instagram app: **Professional dashboard → Total followers** shows
   age ranges, gender, and top cities. TikTok: **Profile → TikTok Studio →
   Analytics → Followers** shows the same.
2. Open `data/stats.json` in any text editor and find `"demographics"`.
3. Overtype the `pct` numbers with the real ones (each group should add up
   to about 100).
4. Change `"estimated": true` to `"estimated": false` and update `"as_of"`
   to today's date.
5. Save, then publish: double-click `weekly-update.command`, or ask
   Claude Code to push. The gold "Estimated" badge disappears on its own.

## Adding Facebook later

Open `data/stats.json`, find the Facebook entry, set its `"url"` to the page
address and `"enabled": true`. Facebook doesn't allow automatic reading, so it
will just show whatever number was last recorded — but the card appears and
you can update `data/stats.json` by hand whenever you like.

## If a number ever looks stuck

A platform showing a "reused" note for several days means its page stopped
letting the automation read it. Ask Claude Code to update that platform's
reader — it's a small fix in `update_stats.py`.

## Files in this folder

| File | What it is |
|---|---|
| `index.html` | The dashboard page people see |
| `data/stats.json` | The numbers — one snapshot per day lives here |
| `update_stats.py` | The scraper that collects the numbers |
| `.github/workflows/update-stats.yml` | The hourly automation |
| `weekly-update.command` | Optional double-click "refresh now" button |
