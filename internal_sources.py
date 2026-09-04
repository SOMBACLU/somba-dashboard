#!/usr/bin/env python3
"""Cookie-backed reads from Instagram's and TikTok's own internal endpoints.

These are the undocumented addresses each site's own pages call. Nobody
publishes instructions for them, so three things are true and this whole file
is shaped around them:

  1. They need a logged-in session. Not a password — a *cookie*, copied out of
     a browser you signed into yourself. See README for how to get one.
  2. They only work from a home internet connection. Instagram blocks GitHub's
     servers by IP address, and a cookie cannot fix an IP block, so everything
     here runs from the Mac scheduler and is skipped in the cloud.
  3. They get renamed without warning. So no single address is trusted: each
     reader is given a *list* of addresses to try in order, and writes down
     which one answered. When a reader stops working you can see whether every
     address died (the shape changed) or just the first one (it moved).

Nothing here ever raises into the caller. A missing cookie, a dead address or a
reshuffled response all end the same way: that figure is skipped and whatever
was already on file stands. A stale number is a small problem; a crashed hourly
job that stops updating everything else is a big one.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# The Instagram web app identifies itself with this fixed id on every internal
# call. It is not a secret and not per-user — it is the same for everyone, and
# the endpoints reject requests that omit it.
IG_APP_ID = "936619743392459"

CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# Some Instagram addresses only answer the phone app, not the website, so a few
# candidates below are tried with this instead.
IG_APP_UA = ("Instagram 302.0.0.23.114 Android (33/13; 420dpi; 1080x2201; "
             "samsung; SM-G991B; o1s; exynos2100; en_US; 517172397)")

# Cookies come from the environment, which scripts/local-update.sh fills from
# .secrets.local. That file is gitignored; none of these values may be logged.
IG_SESSIONID = os.environ.get("IG_SESSIONID")
IG_DS_USER_ID = os.environ.get("IG_DS_USER_ID")
IG_CSRFTOKEN = os.environ.get("IG_CSRFTOKEN")
TT_SESSIONID = os.environ.get("TT_SESSIONID")
TT_MSTOKEN = os.environ.get("TT_MSTOKEN")


def have_instagram():
    return bool(IG_SESSIONID and IG_DS_USER_ID)


def have_tiktok():
    return bool(TT_SESSIONID)


# --------------------------------------------------------------- the walker

class Attempt(dict):
    """One candidate address: how to call it and how to read the answer."""


def candidate(name, url, headers=None, ua=CHROME_UA, parse=None):
    return Attempt(name=name, url=url, headers=headers or {}, ua=ua, parse=parse)


def _fetch(url, ua, headers, timeout):
    """Fetch and decode JSON. Returns (data, None) or (None, 'why it failed')."""
    all_headers = {"User-Agent": ua, "Accept": "*/*"}
    all_headers.update(headers)
    req = urllib.request.Request(url, headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 401/403 almost always means the cookie died. Say so in words, because
        # that is the one failure with an obvious fix.
        if e.code in (401, 403):
            return None, "HTTP %d — the cookie has expired, paste a fresh one" % e.code
        return None, "HTTP %d" % e.code
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    if not body.strip():
        return None, "empty body (usually a request that needed signing)"
    try:
        return json.loads(body), None
    except ValueError:
        return None, "not JSON (probably a login page)"


def try_endpoints(attempts, log, label, timeout=25):
    """Walk candidates in order; return the first usable answer, or None.

    Every outcome is recorded into `log` under `label`, so a run leaves behind
    a note saying which address answered and how the others failed. That note
    is what makes a guessed address cheap to maintain.
    """
    trail = []
    for a in attempts:
        data, err = _fetch(a["url"], a["ua"], a["headers"], timeout)
        if err:
            trail.append("%s: %s" % (a["name"], err))
            continue
        try:
            out = a["parse"](data)
        except Exception as e:
            trail.append("%s: answered but shape changed (%s)" % (a["name"], e))
            continue
        if out:
            log[label] = {"used": a["name"], "tried": trail}
            return out
        trail.append("%s: answered but held nothing usable" % a["name"])
    log[label] = {"used": None, "tried": trail}
    return None


# ------------------------------------------------------------------ Instagram

def _ig_headers(username):
    return {
        "x-ig-app-id": IG_APP_ID,
        "x-csrftoken": IG_CSRFTOKEN or "",
        "x-requested-with": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/%s/" % username,
        "Cookie": "sessionid=%s; ds_user_id=%s; csrftoken=%s"
                  % (IG_SESSIONID, IG_DS_USER_ID, IG_CSRFTOKEN or ""),
    }


def _parse_web_profile(d):
    u = (d.get("data") or {}).get("user") or {}
    if not u.get("edge_followed_by"):
        raise ValueError("no follower block")
    media = u.get("edge_owner_to_timeline_media") or {}
    posts = []
    for edge in (media.get("edges") or []):
        n = edge.get("node") or {}
        cap = ((n.get("edge_media_to_caption") or {}).get("edges") or [{}])
        title = ((cap[0].get("node") or {}).get("text") or "").strip() if cap else ""
        posts.append({
            "id": n.get("shortcode"),
            "title": title[:120] or "(no caption)",
            "likes": (n.get("edge_liked_by") or {}).get("count"),
            "comments": (n.get("edge_media_to_comment") or {}).get("count"),
            "views": n.get("video_view_count"),
            "published": n.get("taken_at_timestamp"),
            "format": n.get("product_type") or n.get("__typename"),
        })
    return {
        "followers": u["edge_followed_by"]["count"],
        "following": (u.get("edge_follow") or {}).get("count"),
        "posts": media.get("count"),
        "recent_posts": posts,
    }


def _parse_user_info(d):
    u = d.get("user") or {}
    if u.get("follower_count") is None:
        raise ValueError("no follower_count")
    return {
        "followers": u["follower_count"],
        "following": u.get("following_count"),
        "posts": u.get("media_count"),
        "recent_posts": [],
    }


def fetch_instagram_core(username, log):
    """Followers, following, post count and the most recent posts.

    One call covers five of the dashboard's metrics, which is why it is tried
    first and why its failure is the one worth reading closely.
    """
    if not have_instagram():
        return None
    h = _ig_headers(username)
    return try_endpoints([
        candidate("web_profile_info",
                  "https://www.instagram.com/api/v1/users/web_profile_info/?username=%s"
                  % urllib.parse.quote(username),
                  h, CHROME_UA, _parse_web_profile),
        candidate("web_profile_info (app UA)",
                  "https://i.instagram.com/api/v1/users/web_profile_info/?username=%s"
                  % urllib.parse.quote(username),
                  h, IG_APP_UA, _parse_web_profile),
        candidate("users/{id}/info",
                  "https://i.instagram.com/api/v1/users/%s/info/" % IG_DS_USER_ID,
                  h, IG_APP_UA, _parse_user_info),
    ], log, "instagram.core")


def _parse_account_insights(d):
    """Pull whatever of the insights block this response shape exposes.

    Instagram has moved these numbers between three different response shapes
    in as many years, so this reads defensively rather than assuming one.
    """
    flat = {}

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    flat.setdefault(k, v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(d)
    wanted = {
        "reach": ("reach", "reach_count", "accounts_reached"),
        "views": ("views", "impressions", "impression_count"),
        "interactions": ("total_interactions", "engagement", "interaction_count"),
        "accounts_engaged": ("accounts_engaged", "accounts_engaged_count"),
        "profile_visits": ("profile_visits", "profile_views", "profile_view_count"),
        "link_taps": ("profile_links_taps", "website_clicks", "external_link_taps"),
    }
    out = {}
    for name, keys in wanted.items():
        for k in keys:
            if k in flat:
                out[name] = int(flat[k])
                break
    if not out:
        raise ValueError("no recognised insight numbers in the response")
    return out


def fetch_instagram_insights(username, log):
    """Reach, views, interactions, profile visits and link taps."""
    if not have_instagram():
        return None
    h = _ig_headers(username)
    return try_endpoints([
        candidate("account_organic_insights",
                  "https://i.instagram.com/api/v1/insights/account_organic_insights/"
                  "?show_promotions_in_landing_page=true&first=30",
                  h, IG_APP_UA, _parse_account_insights),
        candidate("insights/account_summary",
                  "https://www.instagram.com/api/v1/insights/account_summary/",
                  h, CHROME_UA, _parse_account_insights),
        candidate("creator_tools/insights",
                  "https://www.instagram.com/api/v1/creator_tools/insights/account/",
                  h, CHROME_UA, _parse_account_insights),
    ], log, "instagram.insights")


def _parse_audience(d):
    """Age, gender and city splits, wherever this shape happens to keep them."""
    found = {"age": {}, "gender": {}, "city": {}}
    KEYS = {
        "age": ("age", "age_ranges", "audience_age", "follower_age"),
        "gender": ("gender", "genders", "audience_gender", "follower_gender"),
        "city": ("city", "cities", "audience_city", "follower_city", "top_cities"),
    }

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = k.lower()
                for bucket, names in KEYS.items():
                    if lk in names and isinstance(v, dict):
                        for label, pct in v.items():
                            if isinstance(pct, (int, float)):
                                found[bucket][label] = float(pct)
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(d)
    if not any(found.values()):
        raise ValueError("no age/gender/city splits found")
    return found


def fetch_instagram_audience(username, log):
    """Who the followers are: age bands, gender and top cities."""
    if not have_instagram():
        return None
    h = _ig_headers(username)
    return try_endpoints([
        candidate("account_audience_insights",
                  "https://i.instagram.com/api/v1/insights/account_audience_insights/",
                  h, IG_APP_UA, _parse_audience),
        candidate("insights/audience",
                  "https://www.instagram.com/api/v1/insights/audience/",
                  h, CHROME_UA, _parse_audience),
        candidate("creator_tools/audience",
                  "https://www.instagram.com/api/v1/creator_tools/insights/audience/",
                  h, CHROME_UA, _parse_audience),
    ], log, "instagram.audience")


# -------------------------------------------------------------------- TikTok

def _tt_headers():
    return {
        "Referer": "https://www.tiktok.com/",
        "Cookie": "sessionid=%s; sessionid_ss=%s%s"
                  % (TT_SESSIONID, TT_SESSIONID,
                     ("; msToken=%s" % TT_MSTOKEN) if TT_MSTOKEN else ""),
    }


def _tt_url(path, **params):
    base = {"aid": "1988", "app_language": "en", "app_name": "tiktok_web",
            "channel": "tiktok_web", "device_platform": "web_pc"}
    if TT_MSTOKEN:
        base["msToken"] = TT_MSTOKEN
    base.update(params)
    return "https://www.tiktok.com" + path + "?" + urllib.parse.urlencode(base)


def _parse_tt_overview(d):
    body = d.get("body") or d.get("data") or d
    wanted = {"views": ("VideoViews", "video_views", "play_count"),
              "likes": ("Like", "likes", "digg_count"),
              "comments": ("Comment", "comments", "comment_count"),
              "shares": ("Share", "shares", "share_count"),
              "profile_visits": ("ProfileView", "profile_views"),
              "followers": ("Follower", "follower_count")}
    flat = {}

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    flat.setdefault(k, v)
                elif isinstance(v, dict) and "Value" in v:
                    flat.setdefault(k, v["Value"])
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(body)
    out = {}
    for name, keys in wanted.items():
        for k in keys:
            if k in flat and isinstance(flat[k], (int, float)):
                out[name] = int(flat[k])
                break
    if not out:
        raise ValueError("no recognised numbers in the overview")
    return out


def fetch_tiktok_overview(log, days=28):
    """TikTok's own analytics overview: views, likes, comments, shares."""
    if not have_tiktok():
        return None
    h = _tt_headers()
    return try_endpoints([
        candidate("creator/analytics/overview",
                  _tt_url("/api/creator/analytics/overview/", range=str(days)),
                  h, CHROME_UA, _parse_tt_overview),
        candidate("creator_center/overview",
                  _tt_url("/creator_center/api/analytics/overview/", range=str(days)),
                  h, CHROME_UA, _parse_tt_overview),
        candidate("business_suite/overview",
                  _tt_url("/business_suite/api/analytics/overview/", range=str(days)),
                  h, CHROME_UA, _parse_tt_overview),
    ], log, "tiktok.overview")


def _parse_tt_videos(d):
    body = d.get("body") or d.get("data") or d
    items = None
    for key in ("videos", "video_list", "itemList", "list"):
        if isinstance(body.get(key), list):
            items = body[key]
            break
    if not items:
        raise ValueError("no video list in the response")
    out = []
    for v in items:
        stats = v.get("stats") or v
        out.append({
            "id": v.get("id") or v.get("item_id"),
            "title": (v.get("title") or v.get("desc") or "")[:120] or "(no caption)",
            "views": stats.get("VideoViews") or stats.get("playCount") or stats.get("play_count"),
            "likes": stats.get("Like") or stats.get("diggCount") or stats.get("like_count"),
            "comments": stats.get("Comment") or stats.get("commentCount") or stats.get("comment_count"),
            "shares": stats.get("Share") or stats.get("shareCount") or stats.get("share_count"),
            "published": v.get("create_time") or v.get("createTime"),
        })
    return out


def fetch_tiktok_videos(log, days=28):
    """Per-video views, likes, comments and shares."""
    if not have_tiktok():
        return None
    h = _tt_headers()
    return try_endpoints([
        candidate("creator/analytics/video_list",
                  _tt_url("/api/creator/analytics/video_list/", range=str(days), page="1"),
                  h, CHROME_UA, _parse_tt_videos),
        candidate("creator_center/video_list",
                  _tt_url("/creator_center/api/analytics/video/", range=str(days)),
                  h, CHROME_UA, _parse_tt_videos),
    ], log, "tiktok.videos")


def _parse_tt_followers(d):
    body = d.get("body") or d.get("data") or d
    out = {}
    for key, name in (("gender", "gender"), ("genders", "gender"),
                      ("territory", "locations"), ("territories", "locations"),
                      ("age", "age"), ("ages", "age"),
                      ("activity", "hours"), ("active_hours", "hours")):
        v = body.get(key)
        if isinstance(v, (dict, list)) and v:
            out.setdefault(name, v)
    if not out:
        raise ValueError("no follower breakdown found")
    return out


def fetch_tiktok_followers(log, days=28):
    """Follower gender, territory and active-hours breakdowns."""
    if not have_tiktok():
        return None
    h = _tt_headers()
    return try_endpoints([
        candidate("creator/analytics/follower",
                  _tt_url("/api/creator/analytics/follower/", range=str(days)),
                  h, CHROME_UA, _parse_tt_followers),
        candidate("creator_center/follower",
                  _tt_url("/creator_center/api/analytics/follower/", range=str(days)),
                  h, CHROME_UA, _parse_tt_followers),
    ], log, "tiktok.followers")
