#!/bin/zsh
#
# Double-click this file to turn the dashboard's automatic updates ON or OFF.
#
#   * If auto-update is OFF, it turns it ON.
#   * If auto-update is ON, it offers to turn it OFF.
#
# When it is ON, your Mac quietly refreshes the dashboard numbers every 30
# minutes and publishes them, without you doing anything. This matters because
# Instagram blocks the hourly cloud robot but not your Mac.
#
# Nothing here touches the numbers themselves. To update right now instead,
# double-click weekly-update.command.

cd "$(dirname "$0")" || exit 1
REPO="$PWD"

JOB="com.somba.dashboard"                       # the job's name, as macOS knows it
SRC="$REPO/$JOB.plist"                          # the instruction sheet, kept in the project
AGENTS="$HOME/Library/LaunchAgents"             # the folder macOS watches for jobs
DEST="$AGENTS/$JOB.plist"                       # where the copy has to live

print ""
print "SOMBA dashboard — automatic updates"
print "==================================="
print ""

# --- Sanity check: is the instruction sheet there? ---------------------------
if [[ ! -f "$SRC" ]]; then
  print "Something is missing: I cannot find $JOB.plist next to this file."
  print "It should be in the same folder. Ask Claude Code to put it back."
  print ""
  read "?Press Return to close this window."
  exit 1
fi

# --- Sanity check: do the spelled-out paths still match reality? -------------
# The instruction sheet has to spell out the project's full location, so if the
# folder was moved or renamed those paths now point at nothing.
if ! grep -q "$REPO/scripts/local-update.sh" "$SRC"; then
  print "Heads up: this project folder is at"
  print "    $REPO"
  print "but $JOB.plist still points somewhere else."
  print ""
  print "Ask Claude Code to update the three paths inside $JOB.plist,"
  print "then double-click this file again."
  print ""
  read "?Press Return to close this window."
  exit 1
fi

# --- Which way are we going? -------------------------------------------------
# "launchctl list" prints every job macOS is currently running for you.
# If our job's name is in that list, auto-update is already ON.
if launchctl list | grep -q "$JOB"; then
  IS_ON="yes"
else
  IS_ON="no"
fi

# =============================================================================
# ALREADY ON -> offer to turn it off
# =============================================================================
if [[ "$IS_ON" == "yes" ]]; then
  print "Automatic updates are currently ON."
  print "Your Mac is refreshing the dashboard every 30 minutes."
  print ""
  read "ANSWER?Do you want to turn them OFF? Type y then Return (anything else leaves them on): "

  if [[ "$ANSWER" != "y" && "$ANSWER" != "Y" ]]; then
    print ""
    print "Left ON. Nothing changed."
    print ""
    read "?Press Return to close this window."
    exit 0
  fi

  launchctl unload "$DEST" 2>/dev/null
  rm -f "$DEST"

  print ""
  print "Automatic updates are now OFF."
  print ""
  print "What that means:"
  print "  * Your Mac will stop refreshing the numbers on its own."
  print "  * The hourly cloud robot keeps running, so most numbers stay fresh."
  print "  * Instagram will slowly go stale, because only your Mac can read it."
  print ""
  print "To turn it back on: double-click this same file again."
  print ""
  read "?Press Return to close this window."
  exit 0
fi

# =============================================================================
# CURRENTLY OFF -> turn it on
# =============================================================================
print "Automatic updates are currently OFF. Turning them on..."
print ""

# The diary folder has to exist before macOS will write to it.
mkdir -p "$REPO/logs"

# The job has to be in ~/Library/LaunchAgents — that is the only folder macOS
# looks in. We copy rather than move, so the original stays in the project.
mkdir -p "$AGENTS"
cp "$SRC" "$DEST"

# "unload" first, in case a half-registered copy is hanging around; the
# 2>/dev/null hides the harmless "not found" complaint when there isn't one.
launchctl unload "$DEST" 2>/dev/null
if ! launchctl load "$DEST"; then
  print "macOS refused to switch the schedule on."
  print "Ask Claude Code to look at the message just above this line."
  print ""
  read "?Press Return to close this window."
  exit 1
fi

# Confirm it really took, rather than just assuming.
if ! launchctl list | grep -q "$JOB"; then
  print "Hmm — macOS accepted the job but is not listing it."
  print "Ask Claude Code to check: launchctl list | grep $JOB"
  print ""
  read "?Press Return to close this window."
  exit 1
fi

NEXT="$(date -v+30M '+%-I:%M %p')"

print "Automatic updates are now ON."
print ""
print "What happens now:"
print "  * Your Mac refreshes the dashboard numbers every 30 minutes."
print "  * The first run is at about $NEXT — nothing runs this second."
print "  * If the Mac is asleep or shut, the run happens as soon as it wakes."
print "  * Nothing pops up. It is completely silent."
print "  * If a run has nothing new to report, it publishes nothing."
print ""
print "To see what it has been doing, open this file in TextEdit:"
print "  $REPO/logs/local-update.log"
print ""
print "To update right now instead of waiting: double-click weekly-update.command."
print ""
print "To turn this OFF later: double-click this same file again and answer y."
print ""
read "?Press Return to close this window."
exit 0
