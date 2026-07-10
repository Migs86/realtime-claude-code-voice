"""Configuration via environment variables, with an optional env file.

Precedence: real environment variables win; ~/.realtime-voice/env fills gaps.
The env file is plain KEY=VALUE lines (no quoting, # comments allowed).
"""

import os
from pathlib import Path

STATE_DIR = Path.home() / ".realtime-voice"
ENV_FILE = STATE_DIR / "env"


def _load_env_file() -> None:
    try:
        text = ENV_FILE.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


def api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


# gpt-realtime-mini is ~1/3 the audio cost of gpt-realtime and is plenty for
# verbatim speech + transcription. Override with REALTIME_VOICE_MODEL.
MODEL = os.environ.get("REALTIME_VOICE_MODEL", "gpt-realtime-mini")
VOICE = os.environ.get("REALTIME_VOICE_VOICE", "marin")
TRANSCRIBE_MODEL = os.environ.get("REALTIME_VOICE_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")

# Full-duplex barge-in keeps the mic hot while audio plays. On open laptop
# speakers the model can hear itself (no echo cancellation) and self-interrupt;
# use headphones or set REALTIME_VOICE_BARGE_IN=0 for half-duplex.
BARGE_IN = _flag("REALTIME_VOICE_BARGE_IN", True)

# How much trailing silence ends the user's turn. Lower feels snappier but
# can clip natural mid-sentence pauses; raise it if it cuts you off.
SILENCE_MS = int(os.environ.get("REALTIME_VOICE_SILENCE_MS", "600"))

# Close the kept-alive Realtime connection and audio devices after this many
# seconds without a converse call. Idle time costs nothing (the API bills per
# audio token), but OpenAI caps session length, so don't hold it forever.
IDLE_S = int(os.environ.get("REALTIME_VOICE_IDLE_S", "300"))

SAMPLE_RATE = 24000
