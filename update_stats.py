#!/usr/bin/env python3
"""SOMBA stats updater — fully automatic, no keys, no prompts.

Scrapes the public follower counts for every enabled platform in
data/stats.json and saves a dated snapshot. It never asks a question:
if a platform can't be read, it quietly reuses that platform's last
known number (flagged so the dashboard can show a small "reused" note).

Runs unattended every hour in GitHub Actions, and can also be run by
hand:  python3 update_stats.py

Uses only Python's standard library — nothing to install.
"""

import html as html_lib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(REPO_DIR, "data", "stats.json")

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# Instagram serves the full public page to known search-engine crawlers but
# often walls off everyone else — and which crawlers it trusts varies by the
# requesting network. Trying several in turn is what keeps GitHub's cloud
# runners (the flakiest network we run from) able to read the page.
CRAWLER_UAS = [
    GOOGLEBOT_UA,
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
    "bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/126.0.0.0 Safari/537.36",
    "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
]

# Optional official-API credentials, read from the environment so nothing
# secret ever lives in this file or the repo. When YOUTUBE_API_KEY is set the
# script uses YouTube's official Data API for rock-solid numbers; when it is
# absent everything still works by scraping the public pages as before.
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# Private-analytics credentials. Every one of these is optional: when a value is
# missing the matching fetch is skipped and the hand-entered numbers already in
# data/stats.json are left exactly as they are. Nothing here ever breaks a run.
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
INSTAGRAM_TOKEN = os.environ.get("INSTAGRAM_TOKEN")

# True when running inside GitHub Actions. Some platforms (Instagram) serve the
# page to a laptop but block cloud runners outright, so there is nothing to be
# gained by retrying them here — and a permanently-failing platform would jam
# the alarm channel for every other failure.
IN_CI = bool(os.environ.get("GITHUB_ACTIONS"))


# ---------------------------------------------------------------- helpers

def http_get(url, user_agent, timeout=25, headers=None):
    """Fetch a URL and return its body as text. Raises on any failure."""
    all_headers = {"User-Agent": user_agent}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(url, headers=all_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError("HTTP %s" % resp.status)
        return resp.read().decode("utf-8", errors="replace")


def http_post_json(url, payload, user_agent, timeout=25):
    """POST a JSON body and return the parsed JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": user_agent, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError("HTTP %s" % resp.status)
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def http_post_form(url, fields, timeout=25):
    """POST a form-encoded body and return the parsed JSON.

    http_post_json sends Content-Type: application/json, which Google's token
    endpoint rejects — hence this near-identical second helper.
    """
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": CHROME_UA},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def utc_today():
    """Today's date in UTC — the one clock both writers agree on.

    The Mac scheduler runs in local time and GitHub Actions runs in UTC. Using
    the local date meant that every evening between ~17:17 PDT and midnight the
    two disagreed about the date, which appended duplicate snapshots, flipped
    the "fetched" stamps back and forth (a pointless redeploy every run), and
    defeated the once-per-day guard on the expensive YouTube crawl.
    """
    return datetime.now(timezone.utc).date()


def parse_abbrev(s):
    """Turn '756', '13,800', '13.8K' or '1.2M' into a whole number."""
    s = s.strip().upper().replace(",", "")
    mult = 1
    if s.endswith("K"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("B"):
        mult, s = 1_000_000_000, s[:-1]
    return int(round(float(s) * mult))


# ---------------------------------------------------------------- fetchers
# Each fetcher returns a dict of metrics including "followers",
# or raises an exception (which triggers a silent carry-forward).

def fetch_instagram_scrape(p):
    last_err = None
    for ua in CRAWLER_UAS:
        try:
            html = http_get(p["url"], ua)
        except Exception as e:
            last_err = e
            continue
        m = re.search(
            r'([\d.,]+[KMB]?)\s+Followers,\s+([\d.,]+[KMB]?)\s+Following,\s+([\d.,]+[KMB]?)\s+Posts',
            html,
        )
        if m:
            return {
                "followers": parse_abbrev(m.group(1)),
                "following": parse_abbrev(m.group(2)),
                "posts": parse_abbrev(m.group(3)),
            }
        last_err = RuntimeError("follower count not found in the Instagram page")
    raise last_err


IG_API = "https://graph.instagram.com/v25.0"


def ig_get(path, **params):
    """One Instagram Graph API call. Raises if the token is missing/expired."""
    params["access_token"] = INSTAGRAM_TOKEN
    url = "%s/%s?%s" % (IG_API, path.lstrip("/"), urllib.parse.urlencode(params))
    return json.loads(http_get(url, CHROME_UA))


def fetch_instagram_api(p):
    """Follower/post counts from Instagram's official Graph API.

    Preferred over the scrape because it is the only method that works from
    GitHub's runners — Instagram blocks their IPs outright, and an API call
    carrying a token is not subject to that block.
    """
    me = ig_get("me", fields="followers_count,follows_count,media_count")
    return {
        "followers": int(me["followers_count"]),
        "following": int(me.get("follows_count") or 0),
        "posts": int(me.get("media_count") or 0),
    }


def fetch_instagram(p):
    """Official API when a token is configured, public page otherwise."""
    if INSTAGRAM_TOKEN:
        try:
            return fetch_instagram_api(p)
        except Exception as e:
            # A dead token should be loud — it is a 60-day chore that WILL
            # lapse — but it must not take the whole run down with it.
            print("(Instagram API failed: %s — falling back to the page) " % e, end="")
    return fetch_instagram_scrape(p)


def fetch_tiktok(p):
    html = http_get(p["url"], CHROME_UA)
    username = p["url"].rstrip("/").rsplit("@", 1)[-1]
    # Anchor to our own account so a "suggested accounts" block can't
    # feed us someone else's numbers. Fall back to first match anywhere.
    idx = html.find('"uniqueId":"%s"' % username)
    region = html[idx : idx + 3000] if idx != -1 else html
    out = {}
    for key, name in (("followerCount", "followers"), ("heartCount", "likes"), ("videoCount", "videos")):
        m = re.search(r'"%s":(\d+)' % key, region) or re.search(r'"%s":(\d+)' % key, html)
        if m:
            out[name] = int(m.group(1))
    if "followers" not in out:
        raise RuntimeError("follower count not found in the TikTok page")
    return out


def fetch_youtube(p):
    """Subscriber, video and total-view counts.

    Prefers YouTube's official Data API (needs YOUTUBE_API_KEY and the
    channel_id); falls back to scraping the public /about page when no key
    is configured or the API call fails.
    """
    if YOUTUBE_API_KEY and p.get("channel_id"):
        try:
            return fetch_youtube_api(p["channel_id"])
        except Exception:
            pass  # fall through to the public-page scrape
    return fetch_youtube_scrape(p)


def fetch_youtube_api(channel_id):
    """Channel counts straight from the YouTube Data API v3."""
    url = (
        "https://www.googleapis.com/youtube/v3/channels"
        "?part=statistics&id=%s&key=%s" % (channel_id, YOUTUBE_API_KEY)
    )
    data = json.loads(http_get(url, CHROME_UA))
    items = data.get("items") or []
    if not items:
        raise RuntimeError("YouTube API returned no channel")
    stats = items[0].get("statistics", {})
    if "subscriberCount" not in stats:
        raise RuntimeError("YouTube API did not return a subscriber count")
    out = {"followers": int(stats["subscriberCount"])}
    if "videoCount" in stats:
        out["videos"] = int(stats["videoCount"])
    if "viewCount" in stats:
        out["views"] = int(stats["viewCount"])
    return out


def fetch_youtube_scrape(p):
    # The /about page reliably carries subscriber, video and total-view counts.
    url = p["url"].rstrip("/") + "/about"
    html = http_get(url, CHROME_UA)
    subs = re.search(r'"([\d.,]+[KMB]?)\s+subscribers"', html)
    if not subs:
        raise RuntimeError("subscriber count not found in the YouTube page")
    out = {"followers": parse_abbrev(subs.group(1))}
    vids = re.search(r'"([\d.,]+[KMB]?)\s+videos"', html)
    if vids:
        out["videos"] = parse_abbrev(vids.group(1))
    # viewCountText is the channel total; anchor to it so we don't grab
    # a single video's view count that also appears on the page.
    views = re.search(r'"viewCountText":"([\d.,]+)\s+views"', html)
    if views:
        out["views"] = parse_abbrev(views.group(1))
    return out


def fetch_linkedin(p):
    html = http_get(p["url"], CHROME_UA)
    m = re.search(r'([\d.,]+[KM]?)\s+followers', html, re.IGNORECASE)
    if not m:
        raise RuntimeError("follower count not found in the LinkedIn page")
    return {"followers": parse_abbrev(m.group(1))}


def fetch_facebook(p):
    # No reliable public scrape for Facebook pages; carries forward if enabled.
    raise RuntimeError("Facebook has no automatic fetch")


def fetch_youtube_videos(channel_id):
    """Recent uploads from YouTube's official RSS feed (title, date, views, likes)."""
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id
    xml = http_get(url, CHROME_UA)
    videos = []
    for entry in xml.split("<entry>")[1:]:
        vid = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", entry)
        title = re.search(r"<media:title>([^<]*)</media:title>", entry)
        pub = re.search(r"<published>(\d{4}-\d{2}-\d{2})", entry)
        views = re.search(r'<media:statistics views="(\d+)"', entry)
        likes = re.search(r'<media:starRating[^>]*count="(\d+)"', entry)
        if not (vid and title and pub):
            continue
        videos.append({
            "id": vid.group(1),
            "title": html_lib.unescape(title.group(1)),
            "published": pub.group(1),
            "views": int(views.group(1)) if views else None,
            "likes": int(likes.group(1)) if likes else None,
        })
    if not videos:
        raise RuntimeError("no entries in the YouTube feed")
    return videos


def enrich_videos_via_api(videos):
    """Fill exact views/likes (and a publish date) from the YouTube Data API.

    Mutates the given list in place. A no-op when no key is configured, so
    the RSS/scrape numbers simply stand on their own without one.
    """
    if not YOUTUBE_API_KEY or not videos:
        return
    ids = [v["id"] for v in videos if v.get("id")]
    info = {}
    for i in range(0, len(ids), 50):  # the API takes up to 50 ids per call
        batch = ",".join(ids[i:i + 50])
        url = (
            "https://www.googleapis.com/youtube/v3/videos"
            "?part=statistics,snippet&id=%s&key=%s" % (batch, YOUTUBE_API_KEY)
        )
        data = json.loads(http_get(url, CHROME_UA))
        for item in data.get("items", []):
            info[item["id"]] = item
    for v in videos:
        item = info.get(v["id"])
        if not item:
            continue
        stats = item.get("statistics", {})
        if "viewCount" in stats:
            v["views"] = int(stats["viewCount"])
        if "likeCount" in stats:
            v["likes"] = int(stats["likeCount"])
        published = item.get("snippet", {}).get("publishedAt", "")
        if published and not v.get("published"):
            v["published"] = published[:10]


# --- all-time top videos -------------------------------------------------
# The RSS feed above only carries the ~15 newest uploads, so the all-time
# list walks the channel's Videos and Shorts tabs the same way the YouTube
# page itself does: its embedded "Innertube" browse endpoint. The API key it
# needs is public and printed inside every YouTube page — we scrape it fresh
# each run, so there is still nothing to configure and nothing secret here.

CONSENT_COOKIE = {"Cookie": "SOCS=CAI"}  # skips the EU consent interstitial
SHORTS_TAB_PARAMS = "EgZzaG9ydHPyBgUKA5oBAA=="
VIDEOS_TAB_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


def _walk_collect(obj, key, out):
    """Collect every value stored under `key` anywhere in a nested JSON blob."""
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj[key])
        for v in obj.values():
            _walk_collect(v, key, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_collect(v, key, out)
    return out


def _continuation_token(browse_response):
    items = _walk_collect(browse_response, "continuationItemRenderer", [])
    for it in items:
        token = (
            it.get("continuationEndpoint", {})
            .get("continuationCommand", {})
            .get("token")
        )
        if token:
            return token
    return None


def fetch_youtube_top_videos(channel_id):
    """All-time most-viewed uploads (Shorts + videos). Returns (videos, scanned)."""
    page = http_get(
        "https://www.youtube.com/channel/%s/videos" % channel_id,
        CHROME_UA,
        headers=CONSENT_COOKIE,
    )
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', page)
    ver = re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', page)
    if not (key and ver):
        raise RuntimeError("could not read the YouTube page config")
    browse_url = "https://www.youtube.com/youtubei/v1/browse?key=" + key.group(1)
    context = {
        "client": {
            "clientName": "WEB",
            "clientVersion": ver.group(1),
            "hl": "en",
            "gl": "US",
        }
    }

    found = {}  # id -> {title, views, kind}

    # Shorts tab: pages of shortsLockupViewModel entries, follow continuations.
    resp = http_post_json(
        browse_url,
        {"context": context, "browseId": channel_id, "params": SHORTS_TAB_PARAMS},
        CHROME_UA,
    )
    for _ in range(12):  # safety cap; ~48 Shorts per page
        for lockup in _walk_collect(resp, "shortsLockupViewModel", []):
            vid = (
                lockup.get("onTap", {})
                .get("innertubeCommand", {})
                .get("reelWatchEndpoint", {})
                .get("videoId")
            )
            title = lockup.get("overlayMetadata", {}).get("primaryText", {}).get("content")
            views = lockup.get("overlayMetadata", {}).get("secondaryText", {}).get("content", "")
            m = re.match(r"([\d.,]+[KMB]?)\s+views", views or "")
            if vid and title:
                found[vid] = {
                    "title": title,
                    "views": parse_abbrev(m.group(1)) if m else None,
                    "kind": "short",
                }
        token = _continuation_token(resp)
        if not token:
            break
        resp = http_post_json(browse_url, {"context": context, "continuation": token}, CHROME_UA)

    # Videos tab: the channel's long-form uploads (a single small page).
    resp = http_post_json(
        browse_url,
        {"context": context, "browseId": channel_id, "params": VIDEOS_TAB_PARAMS},
        CHROME_UA,
    )
    # Newer responses use lockupViewModel...
    for lv in _walk_collect(resp, "lockupViewModel", []):
        if lv.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue
        vid = lv.get("contentId")
        md = lv.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = md.get("title", {}).get("content")
        views = None
        rows = md.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        for row in rows:
            for part in row.get("metadataParts", []):
                m = re.match(r"([\d.,]+[KMB]?)\s+view", part.get("text", {}).get("content", ""))
                if m:
                    views = parse_abbrev(m.group(1))
        if vid and title:
            found[vid] = {"title": title, "views": views, "kind": "video"}
    # ...older ones use videoRenderer. Parse both so a format flip can't break us.
    for vr in _walk_collect(resp, "videoRenderer", []):
        vid = vr.get("videoId")
        runs = vr.get("title", {}).get("runs", [])
        title = runs[0].get("text") if runs else None
        views = vr.get("viewCountText", {}).get("simpleText", "")
        m = re.match(r"([\d.,]+[KMB]?)\s+view", views or "")
        if vid and title:
            found[vid] = {
                "title": title,
                "views": parse_abbrev(m.group(1)) if m else None,
                "kind": "video",
            }

    if not found:
        raise RuntimeError("no uploads found on the channel tabs")

    top = sorted(
        ({"id": vid, **info} for vid, info in found.items()),
        key=lambda v: v["views"] or 0,
        reverse=True,
    )[:12]

    # Enrich the winners from their watch pages: exact views, likes, date.
    for v in top:
        try:
            watch = http_get(
                "https://www.youtube.com/watch?v=" + v["id"],
                CHROME_UA,
                headers=CONSENT_COOKIE,
            )
            views = re.search(r'"viewCount":"(\d+)"', watch)
            likes = re.search(r'"likeCount":"(\d+)"', watch)
            pub = re.search(r'"publishDate":"(\d{4}-\d{2}-\d{2})', watch) or re.search(
                r'"uploadDate":"(\d{4}-\d{2}-\d{2})', watch
            )
            if views:
                v["views"] = int(views.group(1))
            v["likes"] = int(likes.group(1)) if likes else None
            v["published"] = pub.group(1) if pub else None
        except Exception:
            v.setdefault("likes", None)
            v.setdefault("published", None)
        time.sleep(1)

    top.sort(key=lambda v: v["views"] or 0, reverse=True)
    return top, len(found)


# ------------------------------------------------- private analytics (opt-in)
# Everything below needs a token Dan grants once. With no token these are
# skipped entirely and the hand-entered numbers in data/stats.json stand.

YTA_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
YTA_LAG_DAYS = 3       # YouTube finishes counting a day ~2-3 days later
YTA_WINDOW_DAYS = 28   # matches YouTube Studio's "Last 28 days"

AGE_BAND = {"age13-17": "13–17", "age18-24": "18–24", "age25-34": "25–34",
            "age35-44": "35–44", "age45-54": "45+", "age55-64": "45+",
            "age65-": "45+"}
AGE_ORDER = ["13–17", "18–24", "25–34", "35–44", "45+"]
GENDER_LABEL = {"female": "Women", "male": "Men",
                "user_specified": "Other / undisclosed"}


def google_access_token():
    """Trade the long-lived refresh token for a one-hour access token."""
    data = http_post_form("https://oauth2.googleapis.com/token", {
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
        "refresh_token": YOUTUBE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    if "access_token" not in data:
        raise RuntimeError("no access token returned — the grant may have been revoked")
    return data["access_token"]


def yta_window(today=None):
    """The most recent 28 days YouTube has finished counting."""
    today = today or utc_today()
    end = today - timedelta(days=YTA_LAG_DAYS)
    start = end - timedelta(days=YTA_WINDOW_DAYS - 1)
    return start.isoformat(), end.isoformat()


def yta_query(token, **params):
    url = YTA_URL + "?" + urllib.parse.urlencode(params)
    return json.loads(http_get(url, CHROME_UA,
                               headers={"Authorization": "Bearer " + token}))


def yta_rows(resp):
    """[(dimension_value, metric_value), ...] for a one-dimension report."""
    return [(r[0], r[1]) for r in (resp.get("rows") or [])]


def fetch_youtube_engagement(token, start, end):
    """Last-28-day engagement straight from YouTube Analytics."""
    resp = yta_query(token, ids="channel==MINE", startDate=start, endDate=end,
                     metrics="views,likes,comments,shares")
    rows = resp.get("rows") or []
    if not rows:
        raise RuntimeError("YouTube Analytics returned no rows")
    got = dict(zip([c["name"] for c in resp.get("columnHeaders", [])], rows[0]))
    # These are NET changes over the window, not counts, so a viewer removing a
    # like can return a negative number. Seen live: likes = -1. Clamp, because a
    # negative "count" would poison every total it feeds.
    likes = max(0, int(got.get("likes") or 0))
    comments = max(0, int(got.get("comments") or 0))
    shares = max(0, int(got.get("shares") or 0))
    views = int(got.get("views") or 0)
    # Below ~10 events a single like moves the headline rate by more than a tenth
    # of its own value, so the number carries no information — it just looks like
    # a finding. Seen live: 1,016 views and exactly one share, which would have
    # published "0.1%, far below benchmark" off the back of one person.
    MIN_INTERACTIONS = 10
    inter = likes + comments + shares
    if views <= 0 or inter < MIN_INTERACTIONS:
        raise RuntimeError(
            "too little activity to compute a rate (%d views, %d interactions; need %d) — "
            "leaving YouTube's engagement as it was" % (views, inter, MIN_INTERACTIONS))
    return {
        "reach": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        # YouTube Analytics has no "saves" metric at all. Writing 0 keeps the
        # rate honest against a benchmark measured the same way; folding in
        # playlist adds would quietly inflate it.
        "saves": 0,
        "period_days": YTA_WINDOW_DAYS,
        "source": "youtube-analytics-api",
        "window": "%s..%s" % (start, end),
    }


def fetch_youtube_demographics(token, start, end):
    """Age and gender splits of the channel's viewers."""
    ages = {}
    for key, pct in yta_rows(yta_query(
            token, ids="channel==MINE", startDate=start, endDate=end,
            metrics="viewerPercentage", dimensions="ageGroup")):
        band = AGE_BAND.get(key)
        if band:
            ages[band] = round(ages.get(band, 0) + float(pct), 1)

    genders = []
    for key, pct in yta_rows(yta_query(
            token, ids="channel==MINE", startDate=start, endDate=end,
            metrics="viewerPercentage", dimensions="gender")):
        label = GENDER_LABEL.get(key)
        if label:
            genders.append({"label": label, "pct": round(float(pct), 1)})

    # Plausibility gate. The API hands back raw percentages with no sample size,
    # so a channel with a handful of signed-in viewers reports things like
    # "100% age13-17, 100% male" — which is exactly what this channel returned
    # while YouTube Studio refused the same report as "not enough demographic
    # data". A single bucket at 100% is a sample of roughly one person, and
    # publishing it would be worse than showing nothing.
    if not ages and not genders:
        raise RuntimeError("YouTube Analytics returned no demographics")
    if len(ages) < 3 or len(genders) < 2 or max(ages.values(), default=0) >= 95:
        raise RuntimeError(
            "demographics too thin to trust (%d age bands, %d genders, top band %.0f%%) — "
            "YouTube Studio suppresses this report at these sample sizes and so do we"
            % (len(ages), len(genders), max(ages.values(), default=0)))
    return {
        "age": [{"band": b, "pct": ages[b]} for b in AGE_ORDER if b in ages],
        "gender": genders,
        "estimated": False,
        "as_of": utc_today().isoformat(),
        "source": "youtube-analytics-api",
        "note": "Real YouTube viewer demographics, refreshed automatically.",
    }


def update_private_analytics(data):
    """Fill in whatever the granted tokens allow. Never raises."""
    if not (YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN):
        return
    sys.stdout.write("  YouTube private analytics... ")
    sys.stdout.flush()
    try:
        token = google_access_token()
        start, end = yta_window()
    except Exception as e:
        print("could not sign in (%s) — keeping the numbers already on file" % e)
        print("::warning::YouTube Analytics sign-in failed (%s)" % e)
        return

    try:
        eng = fetch_youtube_engagement(token, start, end)
        block = data.setdefault("engagement", {})
        eng["updated"] = utc_today().isoformat()
        block.setdefault("platforms", {})["youtube"] = eng
        # Deliberately NOT stamping block["updated"] — that renders as "Updated
        # <date>" for the whole Engagement section, and a YouTube-only refresh
        # would vouch for hand-entered Instagram numbers that never changed.
        print("ok (%s views, %s likes)" % ("{:,}".format(eng["reach"]),
                                           "{:,}".format(eng["likes"])))
    except Exception as e:
        print("engagement unavailable (%s)" % e)

    try:
        # Held in a separate key: YouTube is a minority of the audience, so this
        # must never overwrite the cross-platform demographics block.
        data["demographics_youtube"] = fetch_youtube_demographics(token, start, end)
        print("  YouTube demographics... ok")
    except Exception as e:
        print("  YouTube demographics... unavailable (%s)" % e)


FETCHERS = {
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "youtube": fetch_youtube,
    "linkedin": fetch_linkedin,
    "facebook": fetch_facebook,
}


# ---------------------------------------------------------------- carry-forward

def last_known(prev_snap, platform_id):
    """(metrics dict, date) from the previous snapshot, or (None, None)."""
    if not prev_snap:
        return None, None
    entry = (prev_snap.get("platforms") or {}).get(platform_id)
    if not entry or entry.get("followers") is None:
        return None, None
    return entry, prev_snap["date"]


def last_known_any(data, platform_id):
    """Newest non-null numbers for a platform from ANY snapshot — including
    today's (a same-day rerun must never wipe a good number with a blank)."""
    for snap in reversed(data["snapshots"]):
        entry = (snap.get("platforms") or {}).get(platform_id)
        if entry and entry.get("followers") is not None:
            return entry, snap["date"]
    return None, None


def carry_forward(p, data):
    """Couldn't scrape — reuse the last known numbers, flagged as reused."""
    prev_entry, prev_date = last_known_any(data, p["id"])
    if prev_entry:
        out = {k: v for k, v in prev_entry.items() if k not in ("source", "carried_from")}
        out["source"] = "carried"
        out["carried_from"] = prev_entry.get("carried_from", prev_date)
        return out
    return {"followers": None, "source": "carried"}


def fetch_with_retries(fetcher, p, attempts=3, delay=6):
    """Platforms sometimes rate-limit cloud servers briefly — retry before
    falling back to carry-forward."""
    for i in range(attempts):
        try:
            return fetcher(p)
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(delay)


# ---------------------------------------------------------------- snapshots

def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)


def save_data(data):
    """Write via a temp file and rename, so a killed run cannot leave a
    half-written stats.json behind for the next job to commit and publish."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    with open(tmp) as f:          # cheap self-check before it goes live
        json.load(f)
    os.replace(tmp, DATA_FILE)


def previous_snapshot(data, today):
    """Most recent snapshot from a DIFFERENT day (so a same-day rerun still
    compares against last week, not against itself)."""
    for snap in reversed(data["snapshots"]):
        if snap["date"] != today:
            return snap
    return None


def upsert_snapshot(data, snap):
    """Same-day rerun replaces that day's snapshot instead of duplicating it.

    Searches the whole list rather than only testing the newest entry. Two
    writers with skewed clocks could otherwise append an unbounded number of
    rows for the same date: once the other writer added a later-dated snapshot,
    every later run here saw a non-matching last element and appended again.
    """
    snaps = data["snapshots"]
    for i, existing in enumerate(snaps):
        if existing["date"] == snap["date"]:
            snaps[i] = snap
            break
    else:
        snaps.append(snap)
    snaps.sort(key=lambda s: s["date"])


def dedupe_snapshots(data):
    """Collapse any duplicate dates left behind by earlier runs. Keeps the last."""
    seen, out = {}, []
    for snap in data.get("snapshots", []):
        seen[snap["date"]] = snap
    if len(seen) != len(data.get("snapshots", [])):
        out = [seen[d] for d in sorted(seen)]
        removed = len(data["snapshots"]) - len(out)
        data["snapshots"] = out
        print("  (cleaned %d duplicate snapshot row%s)" % (removed, "" if removed == 1 else "s"))


# ---------------------------------------------------------------- output

def print_summary(snap, prev_snap, platforms):
    print()
    print("=" * 56)
    print("  SOMBA stats for %s" % snap["date"])
    print("=" * 56)
    total, prev_total, have_prev = 0, 0, False
    for p in platforms:
        entry = snap["platforms"].get(p["id"])
        if not entry:
            continue
        followers = entry.get("followers")
        prev_entry, _ = last_known(prev_snap, p["id"])
        delta = ""
        if followers is not None and prev_entry:
            diff = followers - prev_entry["followers"]
            delta = "%+d" % diff if diff else "no change"
            prev_total += prev_entry["followers"]
            have_prev = True
        stale = "  (reused old number)" if entry.get("source") == "carried" else ""
        shown = "?" if followers is None else "{:,}".format(followers)
        print("  %-10s %8s followers   %s%s" % (p["name"], shown, delta, stale))
        if followers is not None:
            total += followers
    print("-" * 56)
    line = "  Total audience: {:,}".format(total)
    if have_prev:
        line += "   (%+d since last update)" % (total - prev_total)
    print(line)
    print("=" * 56)


# ---------------------------------------------------------------- main

def main():
    data = load_data()
    dedupe_snapshots(data)
    # Remember what the data looked like before this run, so we only stamp a
    # new "generated_at" time when something actually changed. An unchanged
    # file stays byte-identical -> no git commit -> no pointless redeploy.
    baseline = json.dumps(
        {k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True
    )
    platforms = [p for p in data["config"]["platforms"] if p.get("enabled")]
    today = utc_today().isoformat()
    prev_snap = previous_snapshot(data, today)

    # Today's entries as already saved by an earlier run this hour. A later
    # failing run must never downgrade a number this morning actually read —
    # doing so relabels good days as stale and inflates the "blocked" streak.
    today_prev = {}
    for snap_existing in data["snapshots"]:
        if snap_existing["date"] == today:
            today_prev = snap_existing.get("platforms") or {}
            break

    print("Fetching stats for %d platforms..." % len(platforms))
    snap = {"date": today, "platforms": {}}
    failed = []
    for p in platforms:
        sys.stdout.write("  %s... " % p["name"])
        sys.stdout.flush()
        prev_today = today_prev.get(p["id"]) or {}

        if IN_CI and not ci_can_read(p):
            metrics = prev_today if prev_today.get("source") == "scrape" else carry_forward(p, data)
            print("skipped — this platform only refreshes when run on the Mac")
            snap["platforms"][p["id"]] = metrics
            continue

        try:
            metrics = fetch_with_retries(FETCHERS[p["id"]], p)
            metrics["source"] = "scrape"
            metrics["last_ok"] = today
            print("ok (%s followers)" % "{:,}".format(metrics["followers"]))
        except Exception as e:
            if prev_today.get("source") == "scrape":
                # Already read successfully today — keep that, don't downgrade.
                metrics = prev_today
                print("could not read (%s) — keeping today's earlier reading" % e)
            else:
                metrics = carry_forward(p, data)
                note = "reused last number" if metrics.get("followers") is not None else "no data yet"
                print("could not read (%s) — %s" % (e, note))
                failed.append(p["name"])
                # Surfaces as a yellow annotation on the GitHub Actions run page.
                print("::warning::%s could not be read this run (%s)" % (p["name"], e))
        snap["platforms"][p["id"]] = metrics

    yt = next((p for p in platforms if p["id"] == "youtube" and p.get("channel_id")), None)
    if yt:
        sys.stdout.write("  Recent videos... ")
        sys.stdout.flush()
        try:
            vids = fetch_youtube_videos(yt["channel_id"])
            try:
                enrich_videos_via_api(vids)
            except Exception:
                pass  # keep the RSS numbers if the enrichment call fails
            data["recent_videos"] = {"fetched": today, "source": "youtube-rss", "videos": vids}
            print("ok (%d videos)" % len(vids))
        except Exception as e:
            print("could not read (%s) — reused last list" % e)

        # The all-time crawl walks dozens of YouTube pages, so with hourly
        # runs we only do it once per day. If today's attempt fails, the
        # "fetched" date stays on yesterday and the next hourly run retries.
        # Set FETCH_TOP_VIDEOS=1 to force a fresh crawl right now.
        already_today = data.get("top_videos", {}).get("fetched") == today
        if already_today and os.environ.get("FETCH_TOP_VIDEOS") != "1":
            print("  All-time top videos... already fetched today, skipping")
        else:
            sys.stdout.write("  All-time top videos... ")
            sys.stdout.flush()
            try:
                top, scanned = fetch_youtube_top_videos(yt["channel_id"])
                try:
                    enrich_videos_via_api(top)
                except Exception:
                    pass  # keep the watch-page numbers if the enrichment call fails
                data["top_videos"] = {
                    "fetched": today,
                    "source": "youtube-innertube",
                    "total_scanned": scanned,
                    "videos": top,
                }
                print("ok (top %d of %d uploads)" % (len(top), scanned))
            except Exception as e:
                print("could not read (%s) — reused last list" % e)

    update_private_analytics(data)

    upsert_snapshot(data, snap)
    if json.dumps(
        {k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True
    ) != baseline:
        data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_data(data)
    print_summary(snap, prev_snap, platforms)

    if failed and len(failed) == len(platforms):
        # Every platform failing at once means something is broken on our
        # side (network, blocked runner, changed pages) — fail the run so
        # the workflow's alarm step fires, instead of pretending all is well.
        print("::error::No platform could be read this run: %s" % ", ".join(failed))
        sys.exit(1)


def ci_can_read(p):
    """Can the cloud job read this platform?

    Instagram blocks GitHub's IPs for scraping, so it is marked local-only —
    but an API token lifts that restriction, because an authenticated call is
    not subject to the block. So the exemption disappears the moment a token
    exists, and the alarm starts watching Instagram again automatically.
    """
    if p["id"] == "instagram" and INSTAGRAM_TOKEN:
        return True
    return p.get("reads") not in ("manual", "local-only")


def days_since_ok(data, platform_id, today):
    """Days since this platform last handed us a genuinely fresh number.

    Counting consecutive "carried" snapshots was wrong: one good hour rewrote
    the day's entry and reset the count, so a platform blocked for a fortnight
    could still look fine. A last_ok date survives that.
    """
    for snap in reversed(data["snapshots"]):
        entry = (snap.get("platforms") or {}).get(platform_id)
        if not entry:
            continue
        stamp = entry.get("last_ok")
        if not stamp and entry.get("source") == "scrape":
            stamp = snap["date"]  # older data predates last_ok
        if stamp:
            try:
                return (date.fromisoformat(today) - date.fromisoformat(stamp)).days
            except ValueError:
                return None
    return None


def health_check():
    """`update_stats.py --check` — exits non-zero when the data has gone
    quietly stale, so the workflow can raise an alarm a human will see.

    Also prints CAUSE: lines. The workflow builds the alert issue's title from
    them, so each kind of breakage gets its own issue instead of the first one
    open muting all the rest.
    """
    data = load_data()
    platforms = [p for p in data["config"]["platforms"] if p.get("enabled")]
    today = utc_today().isoformat()
    problems = []   # (cause-slug, human sentence)

    if not data["snapshots"]:
        problems.append(("no-data", "there are no snapshots at all — the data file has been emptied"))
    else:
        newest = data["snapshots"][-1]["date"]
        age = (utc_today() - date.fromisoformat(newest)).days
        if age >= 2:
            problems.append(("updates-stopped",
                             "the newest snapshot is %d days old (%s) — updates have stopped landing"
                             % (age, newest)))

    for p in platforms:
        # A platform with no automatic reader, or one we deliberately skip in
        # the cloud, is not "blocked" — alarming on it would never clear.
        if not ci_can_read(p):
            # Exempt from the "reader is blocked" alarm, but NOT from noticing
            # that it has stopped refreshing altogether. This platform depends
            # on the Mac scheduler; if that stops, nothing else would ever say so.
            if p.get("reads") == "local-only":
                gone = days_since_ok(data, p["id"], today)
                if gone is not None and gone >= 4:
                    problems.append(("%s-not-refreshing" % p["id"],
                                     "%s has not refreshed for %d days — it only updates from the "
                                     "Mac, so the scheduled updater there is probably not running"
                                     % (p["name"], gone)))
            continue
        stale = days_since_ok(data, p["id"], today)
        if stale is None:
            problems.append(("%s-never-read" % p["id"],
                             "%s has never been read successfully" % p["name"]))
        elif stale >= 3:
            problems.append(("%s-blocked" % p["id"],
                             "%s has not returned a fresh number for %d days — its reader "
                             "is probably blocked" % (p["name"], stale)))

    if problems:
        for cause, msg in problems:
            print("::error::" + msg)
        print("CAUSE:" + "+".join(c for c, _ in problems))
        print("DETAIL:" + " | ".join(m for _, m in problems))
        return 1
    print("Data health: ok (%d platforms, newest snapshot %s)"
          % (len(platforms), data["snapshots"][-1]["date"] if data["snapshots"] else "none"))
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(health_check())
    main()
