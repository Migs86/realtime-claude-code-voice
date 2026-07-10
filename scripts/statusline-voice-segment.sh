#!/bin/bash
# Voice-activity segment for a Claude Code statusline script.
#
# The MCP server advertises the live turn phase in ~/.realtime-voice/phase.json
# ({pid, label, phase, since}); this prints one colored segment from it:
#   🔊 speaking (this tab)               — when this session holds the mic
#   🎙 listening · <other-project>       — when another session does
# Prints nothing when no voice turn is active (the pid check drops stale files).
#
# Usage from your statusline script (dir_name = basename of the session cwd):
#   voice_seg=$(statusline-voice-segment.sh "$dir_name")
#   [ -n "$voice_seg" ] && line="$line │ $voice_seg"

dir_name="${1:-}"
phase_file="$HOME/.realtime-voice/phase.json"
[ -f "$phase_file" ] || exit 0

v_json=$(cat "$phase_file" 2>/dev/null)
v_pid=$(echo "$v_json" | jq -r '.pid // empty' 2>/dev/null)
[ -n "$v_pid" ] && kill -0 "$v_pid" 2>/dev/null || exit 0

v_phase=$(echo "$v_json" | jq -r '.phase // empty')
v_label=$(echo "$v_json" | jq -r '.label // empty')
case "$v_phase" in
  speaking)  v_icon='🔊'; v_color='\033[95m' ;;  # magenta
  listening) v_icon='🎙'; v_color='\033[92m' ;;  # green
  *) exit 0 ;;
esac

if [ "$v_label" = "$dir_name" ]; then
  printf '%b%s %s (this tab)\033[0m' "$v_color" "$v_icon" "$v_phase"
else
  printf '%b%s %s · %s\033[0m' "$v_color" "$v_icon" "$v_phase" "$v_label"
fi
