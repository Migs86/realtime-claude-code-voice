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

if [ $# -gt 0 ]; then
  MSG="$*"
else
  # Bring the target terminal forward while the mic warms up.
  osascript -e 'tell application "iTerm2" to activate' >/dev/null 2>&1 &
  MSG=$("$PY" "$REPO/scripts/voice_capture.py") || exit 0
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
