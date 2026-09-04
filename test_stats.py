#!/usr/bin/env python3
"""Tests for the stats readers. Run:  python3 test_stats.py

Why this file exists: the Instagram and TikTok addresses in internal_sources.py
are guessed, so they will need editing when those sites reshuffle. These tests
let you change an address, or the shape a reader expects, and find out in one
second whether you broke the rest.

Nothing here touches the network. Every test feeds a canned answer through the
real parsing code, so the tests pass whether or not you have cookies set.

Uses only Python's standard library — nothing to install.
"""

import contextlib
import copy
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import internal_sources as I
import update_stats as U

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "stats.json")


def load_stats():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


@contextlib.contextmanager
def quiet():
    """The readers narrate what they found; that is noise inside a test run."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# --------------------------------------------------------------- YouTube

class YouTubeReports(unittest.TestCase):
    """The reports added on top of YouTube Analytics."""

    def setUp(self):
        self.canned = {}
        # yta_query is the single point where these reports touch the network.
        U.yta_query = lambda token, **kw: {
            "rows": self.canned[kw.get("dimensions", "") + "|" + kw.get("filters", "")]}

    def test_subscriber_split_is_a_percentage_of_the_total(self):
        self.canned["subscribedStatus|"] = [["SUBSCRIBED", 700], ["UNSUBSCRIBED", 2143]]
        self.assertEqual(
            U.fetch_youtube_audience_split("t", "a", "b"),
            {"views_from_followers_pct": 24.6, "views_from_non_followers_pct": 75.4})

    def test_locations_drop_to_state_when_one_country_dominates(self):
        # A list reading "US 90%" is one bar and says nothing useful.
        self.canned["country|"] = [["US", 900], ["CA", 60], ["GB", 40]]
        self.canned["province|country==US"] = [["US-CA", 700], ["US-TX", 150], ["US-NY", 50]]
        r = U.fetch_youtube_locations("t", "a", "b")
        self.assertEqual(r["level"], "province")
        self.assertEqual([x["pct"] for x in r["locations"]], [77.8, 16.7, 5.6])

    def test_locations_stay_at_country_when_the_audience_is_spread(self):
        self.canned["country|"] = [["US", 400], ["GB", 350], ["CA", 250]]
        self.assertEqual(U.fetch_youtube_locations("t", "a", "b")["level"], "country")

    def test_daily_views_fold_into_weekdays(self):
        self.canned["day|"] = [["2026-08-31", 50], ["2026-09-01", 30], ["2026-09-02", 20]]
        r = U.fetch_youtube_view_days("t", "a", "b")
        self.assertEqual({k: v for k, v in r["days"].items() if v},
                         {"Mon": 50.0, "Tue": 30.0, "Wed": 20.0})
        self.assertEqual(list(r["days"]), U.WEEKDAYS,
                         "all seven days must be present, even the empty ones")

    def test_an_empty_window_is_refused_rather_than_reported_as_zero(self):
        for dims, fn in (("subscribedStatus|", U.fetch_youtube_audience_split),
                         ("country|", U.fetch_youtube_locations),
                         ("day|", U.fetch_youtube_view_days)):
            self.canned[dims] = []
            with self.assertRaises(RuntimeError):
                fn("t", "a", "b")


class DemographicsGate(unittest.TestCase):
    """The gate decides when a viewer split is too thin to publish."""

    def setUp(self):
        self.canned = {}
        U.yta_query = lambda token, **kw: {"rows": self.canned[kw["dimensions"] + "|"]}

    def demo(self, ages, genders):
        self.canned["ageGroup|"] = ages
        self.canned["gender|"] = genders
        return U.fetch_youtube_demographics("t", "a", "b")

    def test_a_single_bucket_at_100_percent_is_still_refused(self):
        # That is a sample of roughly one person.
        with self.assertRaises(RuntimeError):
            self.demo([["age18-24", "100.0"]], [["male", "100.0"]])

    def test_a_thin_but_plural_split_publishes_marked_low_confidence(self):
        r = self.demo([["age18-24", "60.0"], ["age25-34", "40.0"]], [["male", "100.0"]])
        self.assertEqual(r["confidence"], "low")
        self.assertFalse(r["estimated"])

    def test_a_full_split_publishes_normally(self):
        r = self.demo([["age18-24", "40.0"], ["age25-34", "35.0"], ["age35-44", "25.0"]],
                      [["male", "55.0"], ["female", "45.0"]])
        self.assertEqual(r["confidence"], "normal")


# ------------------------------------------------------- internal sources

class NoCookies(unittest.TestCase):
    def test_every_reader_skips_quietly_when_no_cookie_is_set(self):
        I.IG_SESSIONID = I.IG_DS_USER_ID = I.TT_SESSIONID = None
        for fn in (lambda: I.fetch_instagram_core("x", {}),
                   lambda: I.fetch_instagram_insights("x", {}),
                   lambda: I.fetch_instagram_audience("x", {}),
                   lambda: I.fetch_tiktok_overview({}),
                   lambda: I.fetch_tiktok_videos({}),
                   lambda: I.fetch_tiktok_followers({})):
            self.assertIsNone(fn())


class Parsers(unittest.TestCase):
    """Each parser reads one canned answer, and refuses anything it cannot read."""

    def test_instagram_profile_and_recent_posts(self):
        out = I._parse_web_profile({"data": {"user": {
            "edge_followed_by": {"count": 766},
            "edge_follow": {"count": 285},
            "edge_owner_to_timeline_media": {"count": 529, "edges": [{"node": {
                "shortcode": "AB1",
                "edge_liked_by": {"count": 42},
                "edge_media_to_comment": {"count": 3},
                "video_view_count": 900,
                "taken_at_timestamp": 1756900000,
                "product_type": "clips",
                "edge_media_to_caption": {"edges": [{"node": {"text": "Welcome week!"}}]},
            }}]}}}})
        self.assertEqual(out["followers"], 766)
        self.assertEqual(out["recent_posts"][0]["title"], "Welcome week!")
        self.assertEqual(out["recent_posts"][0]["likes"], 42)

    def test_instagram_insights_found_at_any_depth(self):
        # Instagram has moved these numbers between response shapes repeatedly,
        # so the parser searches rather than assuming a path.
        self.assertEqual(
            I._parse_account_insights({"data": {
                "metrics": [{"reach": 11020}, {"impressions": 19802}],
                "extra": {"accounts_engaged": 634, "profile_views": 210,
                          "profile_links_taps": 2, "total_interactions": 1320}}}),
            {"reach": 11020, "views": 19802, "interactions": 1320,
             "accounts_engaged": 634, "profile_visits": 210, "link_taps": 2})

    def test_instagram_audience_splits(self):
        self.assertEqual(
            I._parse_audience({"body": {"follower_age": {"18-24": 44.0},
                                        "gender": {"female": 61.0}}}),
            {"age": {"18-24": 44.0}, "gender": {"female": 61.0}, "city": {}})

    def test_tiktok_overview_unwraps_value_objects(self):
        self.assertEqual(
            I._parse_tt_overview({"body": {"metrics": {
                "VideoViews": {"Value": 22400}, "Like": {"Value": 1850}}}}),
            {"views": 22400, "likes": 1850})

    def test_tiktok_video_list(self):
        self.assertEqual(
            I._parse_tt_videos({"body": {"videos": [{
                "id": "77", "desc": "Campus tour", "create_time": 1756000000,
                "stats": {"playCount": 1200, "diggCount": 88,
                          "commentCount": 4, "shareCount": 9}}]}}),
            [{"id": "77", "title": "Campus tour", "views": 1200, "likes": 88,
              "comments": 4, "shares": 9, "published": 1756000000}])

    def test_junk_is_rejected_rather_than_read_as_zeros(self):
        # A parser that turns an unrecognised answer into 0 would publish a
        # confident wrong number. Every parser must raise instead.
        for fn, junk in ((I._parse_account_insights, {"nothing": "here"}),
                         (I._parse_audience, {"nope": 1}),
                         (I._parse_tt_overview, {"body": {"x": "y"}}),
                         (I._parse_tt_videos, {"body": {}}),
                         (I._parse_web_profile, {"data": {"user": {}}})):
            with self.assertRaises((ValueError, KeyError, AttributeError)):
                fn(junk)


class EndpointWalker(unittest.TestCase):
    """try_endpoints is what makes a guessed address cheap to maintain."""

    def setUp(self):
        self.attempts = [
            I.candidate("first", "https://example.invalid/a", {}, "ua", lambda d: d),
            I.candidate("second", "https://example.invalid/b", {}, "ua", lambda d: {"n": 1}),
        ]

    def test_it_walks_past_a_dead_address_and_names_the_winner(self):
        I._fetch = lambda url, ua, h, t: (None, "HTTP 404") if url.endswith("a") else ({}, None)
        log = {}
        self.assertEqual(I.try_endpoints(self.attempts, log, "demo"), {"n": 1})
        self.assertEqual(log["demo"]["used"], "second")
        self.assertEqual(log["demo"]["tried"], ["first: HTTP 404"])

    def test_when_everything_dies_it_records_why(self):
        I._fetch = lambda url, ua, h, t: (None, "HTTP 401 — the cookie has expired, paste a fresh one")
        log = {}
        self.assertIsNone(I.try_endpoints(self.attempts, log, "demo"))
        self.assertIsNone(log["demo"]["used"])
        self.assertIn("cookie has expired", log["demo"]["tried"][0])


# ------------------------------------------------------------ merge layer

def _node(code, likes, comments, views, ts, fmt, caption):
    return {"node": {
        "shortcode": code, "edge_liked_by": {"count": likes},
        "edge_media_to_comment": {"count": comments}, "video_view_count": views,
        "taken_at_timestamp": ts, "product_type": fmt,
        "edge_media_to_caption": {"edges": [{"node": {"text": caption}}]}}}


ROUTES = {
    "web_profile_info": {"data": {"user": {
        "edge_followed_by": {"count": 766}, "edge_follow": {"count": 285},
        "edge_owner_to_timeline_media": {"count": 529, "edges": [
            _node("A", 90, 10, 3000, 1756000000, "clips", "Career fair"),
            _node("B", 20, 2, 500, 1756400000, "feed", "Dean welcome")]}}}},
    "account_summary": {"data": {
        "reach": 12000, "impressions": 21000, "total_interactions": 1500,
        "accounts_engaged": 700, "profile_views": 240, "profile_links_taps": 5}},
    "insights/audience": {"body": {
        "follower_age": {"18-24": 44.0, "25-34": 31.0},
        "gender": {"Women": 61.0, "Men": 39.0},
        "cities": {"Thousand Oaks": 30.0, "Los Angeles": 22.0}}},
    "analytics/overview": {"body": {"m": {
        "VideoViews": {"Value": 25000}, "Like": {"Value": 2000},
        "Comment": {"Value": 80}, "Share": {"Value": 260}}}},
    "analytics/video_list": {"body": {"videos": [
        {"id": "1", "desc": "Tour",
         "stats": {"playCount": 9000, "diggCount": 300, "commentCount": 9, "shareCount": 40}},
        {"id": "2", "desc": "Tips",
         "stats": {"playCount": 1200, "diggCount": 50, "commentCount": 1, "shareCount": 3}}]}},
    "analytics/follower": {"body": {"gender": {"Women": 58.0}, "territory": {"US": 91.0}}},
}


class MergeIntoStatsFile(unittest.TestCase):
    """What the readers return has to land in the right place in stats.json."""

    @classmethod
    def setUpClass(cls):
        I.IG_SESSIONID = I.IG_DS_USER_ID = I.IG_CSRFTOKEN = "fake"
        I.TT_SESSIONID = "fake"

        def fake_fetch(url, ua, headers, timeout):
            for frag, payload in ROUTES.items():
                if frag in url:
                    return payload, None
            return None, "HTTP 404"   # earlier candidates miss, exercising the walk

        I._fetch = fake_fetch
        cls.before = load_stats()
        cls.data = copy.deepcopy(cls.before)
        U.IN_CI = False
        with quiet():
            U.update_internal_sources(cls.data)

    def test_top_posts_are_sorted_by_performance_not_by_date(self):
        # The page paints row one with a gold "best" rail. On a date-sorted
        # list that rail would be a lie.
        posts = self.data["top_posts"]["posts"]
        self.assertEqual([p["title"] for p in posts], ["Career fair", "Dean welcome"])
        self.assertEqual(posts[0]["interactions"], 100, "likes + comments")
        self.assertEqual(posts[0]["date"], "2025-08-24", "epoch converted to a date")

    def test_instagram_insights_land_without_disturbing_other_keys(self):
        ig = self.data["engagement"]["platforms"]["instagram"]
        self.assertEqual((ig["reach"], ig["views"], ig["external_link_taps"]),
                         (12000, 21000, 5))
        self.assertEqual(ig["source"], "instagram-internal")
        self.assertEqual(ig["views_by_format"],
                         self.before["engagement"]["platforms"]["instagram"]["views_by_format"],
                         "keys this reader does not own must survive untouched")

    def test_placeholder_demographics_are_replaced_by_real_ones(self):
        dm = self.data["demographics"]
        self.assertFalse(dm["estimated"])
        self.assertEqual([l["name"] for l in dm["locations"]],
                         ["Thousand Oaks", "Los Angeles"])

    def test_tiktok_sample_block_is_replaced_wholesale(self):
        tt = self.data["engagement"]["platforms"]["tiktok"]
        self.assertEqual(tt["source"], "tiktok-internal")
        self.assertEqual(tt["reach"], 25000)
        self.assertNotIn("saves", tt, "leftover sample keys must not sit beside real ones")
        self.assertEqual(self.data["engagement"]["sample_platforms"], [])

    def test_the_endpoint_log_records_every_reader(self):
        log = self.data["_endpoint_log"]["results"]
        self.assertEqual(sorted(log),
                         ["instagram.audience", "instagram.core", "instagram.insights",
                          "tiktok.followers", "tiktok.overview", "tiktok.videos"])
        self.assertEqual(log["instagram.insights"]["used"], "insights/account_summary")
        self.assertEqual(log["instagram.insights"]["tried"],
                         ["account_organic_insights: HTTP 404"])

    def test_the_result_is_still_valid_json(self):
        json.loads(json.dumps(self.data))


class CloudIsSkipped(unittest.TestCase):
    def test_nothing_runs_in_ci(self):
        # Instagram blocks GitHub's runners by IP, and a cookie cannot argue
        # with an IP block, so the cloud job must not even try.
        data = load_stats()
        U.IN_CI = True
        with quiet():
            U.update_internal_sources(data)
        U.IN_CI = False
        self.assertEqual(data, load_stats())


if __name__ == "__main__":
    unittest.main(verbosity=2)
