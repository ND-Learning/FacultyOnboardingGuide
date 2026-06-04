#!/bin/zsh
# install_schedule.sh -- schedule the daily refresh on this Mac via launchd.
#   ./install_schedule.sh            install/update (daily at 07:30)
#   ./install_schedule.sh uninstall  remove
# Logs land in refresh_launchd.log next to this script. Note: the job only
# runs while this Mac is awake; launchd runs missed jobs at next wake.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.odl.estimator.refresh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$1" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "uninstalled $LABEL"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
/usr/bin/python3 - "$HERE" "$PLIST" "$LABEL" <<'PY'
import plistlib, sys
here, plist_path, label = sys.argv[1], sys.argv[2], sys.argv[3]
plistlib.dump({
    "Label": label,
    "ProgramArguments": ["/usr/bin/python3", f"{here}/refresh.py"],
    "WorkingDirectory": here,
    "StartCalendarInterval": {"Hour": 7, "Minute": 30},
    "StandardOutPath": f"{here}/refresh_launchd.log",
    "StandardErrorPath": f"{here}/refresh_launchd.log",
}, open(plist_path, "wb"))
print(f"wrote {plist_path}")
PY

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "scheduled: $LABEL daily at 07:30 (logs: refresh_launchd.log)"
echo "test now with:  launchctl kickstart gui/$(id -u)/$LABEL"
