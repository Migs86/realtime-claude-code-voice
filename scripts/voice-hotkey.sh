#!/bin/bash
# Global push-to-talk, speak-first: press the hotkey, hear a beep, and just
# talk. Your words are transcribed and typed into the Claude Code session
# in the last-active iTerm2 window, submitted as your message.
#
# Works mid-task: Claude Code queues messages that arrive while it's
# working, so your spoken instruction lands as steering.
#
# Bind this to a key combo with the macOS Shortcuts app or a Quick Action
# (install.sh sets up "Claude Voice Push-to-Talk" on ctrl-option-V).
#
# With an argument, skips the mic and types that text instead.

set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
LOG="$HOME/.realtime-voice/hotkey.log"
mkdir -p "$HOME/.realtime-voice"

if [ $# -gt 0 ]; then
  MSG="$*"
else
  # Bring the target terminal forward while the mic warms up.
  osascript -e 'tell application "iTerm2" to activate' >/dev/null 2>&1 &
  echo "--- $(date) capture start (parent: $(ps -o comm= -p $PPID 2>/dev/null))" >> "$LOG"
  MSG=$("$PY" "$REPO/scripts/voice_capture.py" 2>>"$LOG")
  rc=$?
  if [ $rc -ne 0 ]; then
    # rc 1 = handled (busy/silence — voice_capture already notified).
    # Anything else = it died (e.g. mic permission denied for the invoking
    # app); that would otherwise be invisible, so say so.
    if [ $rc -ge 2 ]; then
      osascript -e 'display notification "Voice capture failed — see ~/.realtime-voice/hotkey.log" with title "Claude voice"' >/dev/null 2>&1
    fi
    exit 0
  fi
  MSG="🎙 $MSG (spoken aloud via the voice hotkey — reply with realtime voice)"
fi

exec osascript - "$MSG" <<'EOF'
on run argv
    set msg to item 1 of argv
    tell application "iTerm2"
        activate
        tell current session of current window
            write text msg
        end tell
    end tell
end run
EOF
