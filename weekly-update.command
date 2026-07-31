#!/bin/zsh
# Optional: force an update right now from your Mac.
# (Not required — the dashboard also updates itself every hour.)
cd "$(dirname "$0")"
python3 update_stats.py
git add data/stats.json
git diff --staged --quiet || git commit -m "Manual stats update"
# The hourly robot commits too, so grab its latest commits before pushing.
git pull --rebase
git push
