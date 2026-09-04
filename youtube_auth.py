#!/usr/bin/env python3
"""One-time YouTube sign-in helper.

Run this ONCE to grant the dashboard read-only access to your channel's
private analytics (engagement + demographics). It prints a link, you open it,
click "Allow", and it saves a long-lived "refresh token" into .secrets.local
so the hourly updater never has to ask again.

It PRINTS the link rather than opening it, because opening the default browser
lands in whichever Chrome profile happens to be default — and approving from
the wrong Google account grants access to the wrong channel. Copy the link into
a window already signed in as the account that manages the SOMBA channel.
(Pass --open if you do want it to launch your default browser.)

Before running, put your OAuth app's id and secret into .secrets.local:

    export YOUTUBE_CLIENT_ID="....apps.googleusercontent.com"
    export YOUTUBE_CLIENT_SECRET="...."

Then:  source .secrets.local && python3 youtube_auth.py

Uses only Python's standard library — nothing to install.
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.path.join(REPO_DIR, ".secrets.local")

# Read-only access to the channel's YouTube Analytics reports.
SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")


class _CatchCode(http.server.BaseHTTPRequestHandler):
    """Tiny local web page that catches Google's redirect after you approve."""

    code = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CatchCode.code = (params.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = (
            "<h2>All set — you can close this tab and return to your Terminal.</h2>"
            if _CatchCode.code
            else "<h2>Something went wrong. Check the Terminal window.</h2>"
        )
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass  # keep the terminal quiet


def start_catcher():
    """Open a local port to catch Google's redirect after you click Allow.

    Deliberately binds 127.0.0.1 on an OS-assigned free port rather than a
    fixed "localhost:8765". Two reasons, both real:
      - "localhost" resolves to ::1 before 127.0.0.1, so any unrelated process
        holding the IPv6 port would receive the sign-in code instead of us —
        we would hang forever and hand a live credential to someone else.
      - A fixed port collides with leftover local servers.
    Desktop-app OAuth clients may use any loopback port, so this is allowed.
    """
    return http.server.HTTPServer(("127.0.0.1", 0), _CatchCode)


def save_refresh_token(token):
    """Add (or replace) the YOUTUBE_REFRESH_TOKEN line in .secrets.local."""
    lines = []
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE) as f:
            lines = [ln for ln in f if "YOUTUBE_REFRESH_TOKEN" not in ln]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append('export YOUTUBE_REFRESH_TOKEN="%s"\n' % token)
    with open(SECRETS_FILE, "w") as f:
        f.writelines(lines)


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("Missing YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET.")
        print("Add them to .secrets.local, then run:")
        print("    source .secrets.local && python3 youtube_auth.py")
        return

    # Bind first: the redirect URI has to name the port we actually got.
    server = start_catcher()
    redirect_uri = "http://127.0.0.1:%d/" % server.server_address[1]

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # ask for a refresh token
        "prompt": "consent",        # force it to hand one back
    })

    print("\n" + "=" * 64)
    print("  Open this link in a browser window that is ALREADY signed in")
    print("  as the Google account that manages the SOMBA YouTube channel:")
    print("=" * 64 + "\n")
    print(auth_url + "\n")
    print("Then click Allow. Waiting here until you do (Ctrl+C to cancel)...\n")
    if "--open" in sys.argv:
        webbrowser.open(auth_url)
    server.handle_request()  # waits for the single redirect, then returns

    if not _CatchCode.code:
        print("Did not receive an approval code. Please try again.")
        return

    # Trade the one-time code for a long-lived refresh token.
    resp = urllib.request.urlopen(urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "code": _CatchCode.code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode(),
    ))
    tokens = json.loads(resp.read().decode())
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("No refresh token returned. Response was:")
        print(json.dumps({k: v for k, v in tokens.items() if k != "access_token"}, indent=2))
        return

    save_refresh_token(refresh)
    print("\n✅ Success — your permission slip is saved in .secrets.local.")
    print("   You never need to run this again. Tell Claude you're done.")


if __name__ == "__main__":
    main()
