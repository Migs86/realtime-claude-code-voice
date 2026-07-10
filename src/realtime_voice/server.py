"""MCP server: voice for Claude Code over the OpenAI Realtime API.

Tools:
- converse: speak a message, listen for the spoken reply (with barge-in).
  Coordinates a single machine-wide audio slot across concurrent Claude Code
  terminals; when a session finishes waiting for the slot it focuses its
  iTerm2 tab, posts a notification, and announces the hand-off out loud.
- voice_status: who holds the audio slot, who's waiting, current config.
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import config
from .audio import AudioIO, open_audio
from .iterm import focus_terminal, iterm_session_uuid, notify
from .realtime import RealtimeError, RealtimeSession
from .slot import (
    AudioSlot,
    SlotBusy,
    clear_phase,
    holder_info,
    phase_info,
    waiter_infos,
    write_phase,
)

logging.basicConfig(
    level=os.environ.get("REALTIME_VOICE_LOG", "INFO"),
    stream=sys.stderr,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Claude Code launches stdio MCP servers from the project directory, so the
# cwd basename is a good human label for "which session is this".
PROJECT = Path.cwd().name or "claude"

mcp = FastMCP("realtime-voice")

# Persistent voice plumbing: the WebSocket to OpenAI and the PortAudio
# streams stay open across converse calls, so repeat turns skip the
# connection + device setup entirely. Torn down when idle or when another
# session is waiting for the audio slot.
_audio: AudioIO | None = None
_session: RealtimeSession | None = None
_session_lock = asyncio.Lock()
_idle_task: asyncio.Task | None = None


async def _get_session(voice: str) -> RealtimeSession:
    """Return the live session, (re)creating it if absent or misconfigured."""
    global _audio, _session
    if _audio is None:
        _audio = open_audio(asyncio.get_running_loop())
        if _session is not None:
            _session.audio = _audio
    if _session is not None and not _session.matches(
        voice=voice, model=config.MODEL, silence_ms=config.SILENCE_MS
    ):
        await _session.close()
        _session = None
    if _session is None:
        _session = RealtimeSession(
            _audio, voice=voice, model=config.MODEL, silence_ms=config.SILENCE_MS
        )
    return _session


async def _teardown() -> None:
    """Close the Realtime connection and release the audio devices."""
    global _audio, _session
    if _session is not None:
        await _session.close()
        _session = None
    if _audio is not None:
        _audio.__exit__(None, None, None)
        _audio = None


def _cancel_idle_close() -> None:
    global _idle_task
    if _idle_task is not None:
        _idle_task.cancel()
        _idle_task = None


def _schedule_idle_close() -> None:
    _cancel_idle_close()

    async def _close_after_idle() -> None:
        await asyncio.sleep(config.IDLE_S)
        async with _session_lock:
            log.info("voice idle for %ss — closing session", config.IDLE_S)
            await _teardown()

    global _idle_task
    _idle_task = asyncio.create_task(_close_after_idle())


@mcp.tool()
async def converse(
    message: str,
    listen: bool = True,
    wait_timeout: float = 120,
    barge_in: bool | None = None,
    voice: str | None = None,
    listen_timeout: float = 60,
) -> str:
    """Speak to the user out loud and hear their reply (OpenAI Realtime API).

    Speaks `message` through the speakers, then (if listen=True) listens on
    the mic and returns what the user said as text. The user can talk over
    the playback to interrupt it (barge-in).

    Usage guidance:
    - Keep spoken messages short (1-3 sentences) and conversational; end with
      a question when you expect an answer.
    - Call repeatedly to hold a multi-turn voice conversation.
    - For a final sign-off that needs no reply, pass listen=False.
    - Only one Claude session can use audio at a time. If the result starts
      with [voice busy], tell the user in text who holds it; retry with a
      bigger wait_timeout (seconds) to queue for the slot — when it frees,
      the user's terminal is focused and a hand-off is announced out loud.
    - If the user asks to stop voice mode, stop calling this tool.

    Args:
        message: Text to speak aloud, verbatim.
        listen: Wait for and return the user's spoken reply.
        wait_timeout: Max seconds to wait for the machine-wide audio slot.
        barge_in: Keep the mic hot during playback so the user can interrupt
            (default from REALTIME_VOICE_BARGE_IN, normally on).
        voice: Realtime voice name (default from config, e.g. "marin").
        listen_timeout: Max seconds to wait for the user to start speaking.
    """
    if not config.api_key():
        return (
            "[error] OPENAI_API_KEY is not set. Export it in the shell that "
            "launches Claude Code, or add a line to ~/.realtime-voice/env"
        )

    effective_barge_in = config.BARGE_IN if barge_in is None else barge_in
    slot = AudioSlot(
        PROJECT,
        {"cwd": str(Path.cwd()), "iterm": iterm_session_uuid()},
    )
    try:
        waited = await slot.acquire(wait_timeout)
    except SlotBusy as e:
        holder = e.holder or {}
        held_by = holder.get("label", "unknown")
        held_for = int(time.time() - holder.get("since", time.time()))
        return (
            f"[voice busy] The audio slot is held by session '{held_by}' "
            f"(for {held_for}s). Waited {int(wait_timeout)}s. Tell the user in "
            f"text, and retry with a larger wait_timeout if they want to queue."
        )

    notes: list[str] = []
    try:
        text = message
        if waited:
            await focus_terminal()
            await notify("Claude voice", f"Voice slot free — '{PROJECT}' is ready to talk")
            text = f"Voice is free again — now talking to {PROJECT}. {message}"
            notes.append(
                "this session waited for the voice slot; the user's iTerm2 tab "
                "was focused and the hand-off was announced out loud"
            )
        elif config.FOCUS:
            # Switch iTerm2 to the tab that's about to talk. Fire-and-forget
            # so the osascript round-trip never delays the first audio.
            asyncio.create_task(focus_terminal())
        async with _session_lock:
            _cancel_idle_close()
            session = await _get_session(voice or config.VOICE)
            turn_kwargs = dict(
                message=text,
                listen=listen,
                barge_in=effective_barge_in,
                listen_timeout=listen_timeout,
                on_phase=lambda phase: write_phase(PROJECT, phase),
            )
            try:
                result = await session.run_turn(**turn_kwargs)
            except RealtimeError:
                # The kept-alive WebSocket may have died while idle;
                # reconnect once and retry the turn.
                log.info("realtime turn failed — reconnecting once")
                await session.close()
                result = await session.run_turn(**turn_kwargs)
    except RealtimeError as e:
        return f"[error] Realtime API: {e}"
    except Exception as e:
        log.exception("converse failed")
        return f"[error] {type(e).__name__}: {e}"
    finally:
        clear_phase()
        still_waiting = slot.release()
        if still_waiting:
            # Hand off cleanly: free the mic/speakers for the next session.
            async with _session_lock:
                _cancel_idle_close()
                await _teardown()
            notes.append(
                f"session(s) {', '.join(repr(w) for w in still_waiting)} are "
                f"waiting for voice — wrap up this voice conversation soon"
            )
        else:
            _schedule_idle_close()

    parts: list[str] = []
    if result.get("barged_in"):
        parts.append("[user interrupted the playback mid-message]")
    if not listen:
        parts.append("[spoken]")
    elif result["status"] == "silence":
        parts.append(
            f"[no speech] The user didn't start speaking within "
            f"{int(listen_timeout)}s. They may have stepped away — continue in text."
        )
    elif result["status"] == "no-transcript":
        parts.append("[heard speech but no transcript arrived — ask the user to repeat]")
    elif result.get("transcript"):
        parts.append(f'User said: "{result["transcript"]}"')
    else:
        parts.append("[heard speech but the transcript was empty]")
    parts.extend(f"[note] {n}" for n in notes)
    return "\n".join(parts)


@mcp.tool()
async def voice_status() -> str:
    """Report who holds the machine-wide audio slot, who is waiting, and the
    current voice configuration. Use before long voice sessions or when
    converse reports the slot is busy."""
    lines = [f"this session: '{PROJECT}' (pid {os.getpid()})"]
    holder = holder_info()
    if holder:
        held_for = int(time.time() - holder.get("since", time.time()))
        who = "this session" if holder.get("pid") == os.getpid() else f"'{holder.get('label')}'"
        lines.append(f"audio slot: held by {who} for {held_for}s")
    else:
        lines.append("audio slot: free")
    phase = phase_info()
    if phase:
        lines.append(f"voice activity: {phase.get('phase')} ('{phase.get('label')}')")
    waiters = waiter_infos()
    if waiters:
        lines.append(
            "waiting: " + ", ".join(f"'{w.get('label', '?')}'" for w in waiters)
        )
    lines.append(
        "realtime connection: "
        + ("open (kept alive between turns)" if _session is not None and _session.connected else "closed")
    )
    lines.append(
        f"config: model={config.MODEL} voice={config.VOICE} "
        f"barge_in={'on' if config.BARGE_IN else 'off'} "
        f"silence_ms={config.SILENCE_MS} idle_close_s={config.IDLE_S} "
        f"api_key={'set' if config.api_key() else 'MISSING'}"
    )
    return "\n".join(lines)


def main() -> None:
    log.info("realtime-voice MCP server starting (project=%s)", PROJECT)
    mcp.run()


if __name__ == "__main__":
    main()
