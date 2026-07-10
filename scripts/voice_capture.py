"""Capture one spoken utterance and print its transcript (for the hotkey).

Speak-first flow: the mic is hot from the first instant — frames buffer
locally while the Realtime connection is still being set up, so you can
start talking the moment you hear the beep (or even slightly before).
Server VAD ends the utterance; the transcript goes to stdout.

Exit codes: 0 = transcript printed, 1 = nothing captured / mic busy.
"""

import asyncio
import logging
import math
import os
import sys

from realtime_voice import config

logging.basicConfig(
    level=os.environ.get("REALTIME_VOICE_LOG", "WARNING"),
    stream=sys.stderr,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
from realtime_voice.audio import open_audio
from realtime_voice.iterm import notify
from realtime_voice.realtime import RealtimeSession
from realtime_voice.slot import AudioSlot, SlotBusy, clear_phase, write_phase


def _beep(audio) -> None:
    """A short 880 Hz blip: 'mic is hot, go ahead.'"""
    sr = config.SAMPLE_RATE
    n = int(sr * 0.09)
    pcm = bytearray()
    for i in range(n):
        fade = min(1.0, (n - i) / n * 4)
        v = int(9000 * fade * math.sin(2 * math.pi * 880 * i / sr))
        pcm += v.to_bytes(2, "little", signed=True)
    audio.enqueue_playback(bytes(pcm))


async def main() -> int:
    slot = AudioSlot("voice-hotkey", {})
    try:
        await slot.acquire(2)
    except SlotBusy as e:
        holder = (e.holder or {}).get("label", "another session")
        await notify("Claude voice", f"Mic busy — '{holder}' is talking")
        return 1

    loop = asyncio.get_running_loop()
    result: dict = {}
    try:
        audio = open_audio(loop)
        try:
            audio.set_mic(True)  # capture immediately; frames buffer locally
            _beep(audio)
            write_phase("voice-hotkey", "listening")
            session = RealtimeSession(audio)
            try:
                result = await session.run_turn(
                    message="", listen=True, listen_timeout=15
                )
            finally:
                await session.close()
        finally:
            clear_phase()
            audio.__exit__(None, None, None)
    finally:
        slot.release()

    transcript = (result.get("transcript") or "").strip()
    if not transcript:
        await notify("Claude voice", "Didn't catch anything — try again")
        return 1
    print(transcript)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
