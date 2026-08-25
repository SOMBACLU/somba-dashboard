#!/bin/zsh
#
# The auto-update worker.
#
# Your Mac's scheduler (launchd) runs this file every 30 minutes. It does the
# same three things weekly-update.command does when you double-click it:
#
#   1. read the latest social numbers   (python3 update_stats.py)
#   2. save them into the project       (git commit)
#   3. send them to the website         (git push -> Render rebuilds the site)
#
# You do not need to run this by hand. To turn the schedule on or off,
# double-click somba-autoupdate.command instead.
#
# It is written to be safe if anything goes wrong: it never uses --force, and
# it never leaves the project folder in a half-finished state. If it cannot
# finish cleanly it writes a plain-English note to logs/local-update.log and
# stops until the next run.

# --- Where we are -----------------------------------------------------------
# $0 is this file. Its folder is "scripts/", so one level up is the project.
REPO="${0:A:h:h}"
cd "$REPO" || exit 0

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"

# say() prints a timestamped line. launchd captures everything this script
# prints into logs/local-update.log, so these lines become the diary.
say() { print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

say "----- auto-update starting -----"

# --- Only one at a time -----------------------------------------------------
# A slow run (YouTube's all-time crawl can take a few minutes) must never
# overlap the next one, or two copies of git would fight each other.
# "mkdir" is the trick: on macOS it either creates the folder or fails, and
# only one process can win, so it works as a do-not-disturb sign.
LOCK="$LOG_DIR/.update.lock"
HAVE_LOCK=0

# A lock left behind by a crashed run would block us forever, so anything
# older than an hour is treated as abandoned and cleared.
if [[ -d "$LOCK" ]] && [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +60 2>/dev/null)" ]]; then
  say "Clearing a leftover lock from an earlier run that never finished."
  rmdir "$LOCK" 2>/dev/null
fi

if mkdir "$LOCK" 2>/dev/null; then
  HAVE_LOCK=1
else
  say "Another update is still running — skipping this one. Nothing is wrong."
  exit 0
fi

# Whatever happens from here on (finish, error, or being killed), drop the lock.
cleanup() { [[ "$HAVE_LOCK" = "1" ]] && rmdir "$LOCK" 2>/dev/null; }
trap cleanup EXIT INT TERM

# --- Refuse to touch a project that is mid-repair ----------------------------
# If a previous "git rebase" was interrupted (by you or by us), git leaves
# behind a marker folder. Doing anything else on top of that is how repos get
# badly tangled, so we stop and say so.
if [[ -d "$REPO/.git/rebase-merge" || -d "$REPO/.git/rebase-apply" ]]; then
  say "STOPPING: the project is in the middle of a 'rebase' (a half-finished merge)."
  say "          Ask Claude Code to sort it out, then auto-update resumes by itself."
  exit 0
fi

# --- Secrets ----------------------------------------------------------------
# .secrets.local holds lines like: export YOUTUBE_API_KEY="abc123"
# "source" runs those lines here, so the key exists for update_stats.py.
# It is never committed (see .gitignore).
if [[ -f "$REPO/.secrets.local" ]]; then
  source "$REPO/.secrets.local"
  # Say whether the key arrived, never what it is — this log is plain text.
  if [[ -n "$YOUTUBE_API_KEY" ]]; then
    say "Loaded .secrets.local — YouTube key is present."
  else
    say "Loaded .secrets.local — but no YouTube key in it."
  fi
else
  say "No .secrets.local file — YouTube will fall back to reading the public page."
fi

# --- Step 1: read the numbers -----------------------------------------------
say "Reading the latest numbers..."
python3 update_stats.py
FETCH_STATUS=$?
if [[ $FETCH_STATUS -ne 0 ]]; then
  # update_stats.py only exits non-zero when EVERY platform failed. Even then
  # it may have saved partial data, so we carry on to the save/send steps.
  say "Note: the reader reported a problem (exit code $FETCH_STATUS). Continuing anyway."
fi

# --- Step 2: save the change (only if there is one) --------------------------
git add data/stats.json
if git diff --staged --quiet; then
  say "No numbers changed this time — nothing to send. Done."
  exit 0
fi

git commit -q -m "Auto stats update (Mac) $(date -u +'%F %H:%M UTC')" || {
  say "Could not save the change. Leaving everything as it is."
  exit 0
}
say "Saved the new numbers."

# --- Safety checks before sending -------------------------------------------
# Only ever publish from the main branch.
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [[ "$BRANCH" != "main" ]]; then
  say "You are working on branch '$BRANCH', not 'main' — saved the numbers but did not publish."
  exit 0
fi

# If you have your own unsaved edits to other files, git will not let us pull
# in the robot's hourly commits. Rather than shuffle your work around behind
# your back, we stop here. The numbers are saved locally and will go out on
# the next run after you commit your edits.
if ! git diff --quiet; then
  say "You have unsaved edits in this folder, so the update was saved but not published."
  say "  Files with unsaved edits:"
  git diff --name-only | sed 's/^/    /'
  say "  Commit or undo those edits and the next run will publish automatically."
  exit 0
fi

# --- Step 3: send it to the website -----------------------------------------
# The hourly cloud robot commits too, so grab its work first. --rebase replays
# our commit on top of its commits instead of making a messy merge.
say "Fetching the hourly robot's latest commits..."
if ! git pull --rebase; then
  # Something clashed. Unwind completely so the folder is exactly as it was.
  git rebase --abort 2>/dev/null
  say "Could not combine our update with the robot's (a clash, or no internet)."
  say "  Nothing was broken and nothing was forced — the folder was put back as it was."
  say "  The saved numbers will go out on a later run, or run weekly-update.command yourself."
  exit 0
fi

say "Publishing..."
if git push; then
  say "Published. The website will rebuild in a minute or two."
else
  say "Could not publish (probably no internet, or GitHub sign-in expired)."
  say "  The numbers are saved on this Mac and will go out on a later run."
fi

say "----- auto-update finished -----"

# --- Housekeeping -----------------------------------------------------------
# Keep the diary from growing forever: once a log passes about 1 MB, park it as
# a ".old" copy. The next run starts a fresh, empty log.
for f in "$LOG_DIR/local-update.log" "$LOG_DIR/local-update.err"; do
  if [[ -f "$f" ]] && (( $(stat -f%z "$f" 2>/dev/null || echo 0) > 1048576 )); then
    mv -f "$f" "$f.old"
  fi
done

exit 0
