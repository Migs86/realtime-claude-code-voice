"""Bring this session's iTerm2 tab to the front, and post macOS notifications.

Claude Code inherits ITERM_SESSION_ID ("w0t2p0:<UUID>") from the terminal and
passes it down to this MCP server, so we know exactly which iTerm2 session
spawned us. The first run triggers a one-time macOS Automation permission
prompt (allow controlling iTerm2).
"""

import asyncio
import json
import logging
import os
import subprocess

log = logging.getLogger(__name__)

FOCUS_SCRIPT = """
on run argv
    set targetId to item 1 of argv
    tell application "iTerm2"
        activate
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if (id of s) contains targetId then
                        select s
                        tell t to select
                        tell w to select
                        return "ok"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return "not-found"
end run
"""


def iterm_session_uuid() -> str | None:
    sid = os.environ.get("ITERM_SESSION_ID", "")
    return sid.split(":")[-1] if sid else None


def _run_osascript(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["osascript", *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("osascript failed: %s", e)
        return None


async def focus_terminal() -> bool:
    """Focus the iTerm2 tab this server was launched from. Best-effort."""
    uuid = iterm_session_uuid()
    if not uuid:
        return False
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: _run_osascript("-e", FOCUS_SCRIPT, uuid)
    )
    ok = bool(result) and result.returncode == 0 and "ok" in (result.stdout or "")
    if not ok and result:
        log.warning("iTerm2 focus failed: %s %s", result.stdout, result.stderr)
    return ok


async def notify(title: str, message: str) -> None:
    """Post a macOS notification. Best-effort."""
    script = (
        f"display notification {json.dumps(message)} "
        f"with title {json.dumps(title)}"
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: _run_osascript("-e", script))
