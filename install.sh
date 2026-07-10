#!/usr/bin/env bash
# Install deps and register the MCP server with Claude Code (user scope,
# available in every project).
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first:  brew install uv" >&2
  exit 1
fi

echo "==> Installing dependencies (uv sync)"
uv sync

echo "==> Registering MCP server with Claude Code (scope: user)"
claude mcp remove --scope user realtime-voice >/dev/null 2>&1 || true
claude mcp add --scope user realtime-voice -- uv run --directory "$PWD" realtime-voice-mcp

echo
echo "Done. Checklist:"
echo "  1. OPENAI_API_KEY must be set in the shell that launches claude,"
echo "     or put 'OPENAI_API_KEY=sk-...' in ~/.realtime-voice/env"
echo "  2. Smoke test (no Claude needed):"
echo "       uv run python scripts/voice_check.py \"Hello! Say something back.\""
echo "  3. First run will prompt for macOS Microphone permission, and the"
echo "     first slot hand-off will prompt for Automation (control iTerm2)."
echo "  4. In any Claude Code session, try: 'talk to me using realtime voice'"
