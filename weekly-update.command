#!/bin/zsh
# Optional: force an update right now from your Mac.
# (Not required — the dashboard also updates itself every hour.)
cd "$(dirname "$0")"
# Load the API keys/tokens. Without this a local run has no credentials at
# all — not even the YouTube key — so it quietly collects worse numbers than
# the hourly cloud job does.
[ -f .secrets.local ] && source .secrets.local

# Share the scheduler's lock. Double-clicking this while the every-30-minute
# background job is mid-run means two gits in one folder, which is how a repo
# ends up stuck half-way through a merge.
LOCK="logs/.update.lock"
mkdir -p logs
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "The automatic updater is running right now — try again in a minute."
  echo "(Nothing is wrong. It runs every 30 minutes.)"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

python3 update_stats.py
git add data/stats.json
git diff --staged --quiet || git commit -o data/stats.json -m "Manual stats update"
# The hourly robot commits too, so grab its latest commits before pushing.
# On a clash, throw our commit away rather than leave the folder mid-rebase —
# the numbers are re-read from scratch next time, so nothing is lost.
if ! git pull --rebase; then
  git rebase --abort 2>/dev/null
  git reset -q --hard origin/main 2>/dev/null
  echo "The robot published at the same moment, so this run was skipped."
  echo "Nothing was broken. Try again in a minute."
  exit 0
fi
git push
