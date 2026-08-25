#!/bin/zsh
# Optional: force an update right now from your Mac.
# (Not required — the dashboard also updates itself every hour.)
cd "$(dirname "$0")"
# Load the API keys/tokens. Without this a local run has no credentials at
# all — not even the YouTube key — so it quietly collects worse numbers than
# the hourly cloud job does.
[ -f .secrets.local ] && source .secrets.local
python3 update_stats.py
git add data/stats.json
git diff --staged --quiet || git commit -m "Manual stats update"
# The hourly robot commits too, so grab its latest commits before pushing.
git pull --rebase
git push
