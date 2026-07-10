"""Manual smoke test, no Claude Code needed.

    uv run python scripts/voice_check.py "Hello! Say something back."

Speaks the message, listens for your reply, prints the transcript.
Try talking over the playback to test barge-in.
"""

import asyncio
import sys

from realtime_voice import config
from realtime_voice.audio import AudioIO
from realtime_voice.realtime import run_turn


async def main() -> None:
    message = " ".join(sys.argv[1:]) or (
        "Hi! This is a voice check for the realtime voice server. "
        "Say something after I finish talking — or interrupt me right now."
    )
    if not config.api_key():
        sys.exit("OPENAI_API_KEY is not set (env or ~/.realtime-voice/env)")
    print(f"model={config.MODEL} voice={config.VOICE} barge_in={config.BARGE_IN}")
    print("speaking… (talk over it to test barge-in)")
    with AudioIO(asyncio.get_running_loop()) as audio:
        result = await run_turn(
            audio,
            message=message,
            listen=True,
            barge_in=config.BARGE_IN,
            listen_timeout=30,
        )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
