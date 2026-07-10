#!/bin/bash
# Global push-to-talk: focus the last-active iTerm2 window and ask the
# Claude Code session in it to start a realtime voice conversation.
#
# Works mid-task: the text is typed into the session and submitted, and
# Claude Code queues messages that arrive while it's working, so voice
# starts at the next opportunity.
#
# Bind this to a key combo with the macOS Shortcuts app:
#   Shortcuts -> new shortcut -> "Run Shell Script" action -> this script
#   -> shortcut Details -> Add Keyboard Shortcut (e.g. ctrl-option-V).
#
# Optional argument: the message to send (defaults to starting voice).
# Plain text is used instead of the /voice command on purpose — typed
# slash commands open an autocomplete menu in the TUI, which can swallow
# the Enter keypress; plain text always submits cleanly.

MSG="${1:-talk to me using realtime voice}"

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
