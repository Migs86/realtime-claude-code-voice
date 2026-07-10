# realtime-voice-mcp

Voice for Claude Code over the **OpenAI Realtime API** (one persistent
WebSocket doing streamed speech, instead of separate speech-to-text and
text-to-speech calls). Claude Code stays the brain — the Realtime model is
only the mouth and ears: it speaks Claude's words verbatim and transcribes
your reply. You can **barge in** (talk over the playback) and it stops
immediately.

Built for **concurrent terminals**: only one Claude session can use your mic
and speakers at a time (an "audio slot" — a machine-wide lock file). Other
sessions queue for it; when the slot frees, the waiting session **focuses its
iTerm2 tab**, posts a macOS notification, and announces the hand-off out loud
before continuing.

## Install

```bash
./install.sh
```

That runs `uv sync` and registers the server user-wide via
`claude mcp add --scope user realtime-voice`.

Set your key (either works):

```bash
export OPENAI_API_KEY=sk-...              # in the shell that launches claude
# or
echo 'OPENAI_API_KEY=sk-...' >> ~/.realtime-voice/env
```

Smoke test without Claude:

```bash
uv run python scripts/voice_check.py "Hello! Say something back."
```

That also installs a `/voice` slash command user-wide. Then in any Claude
Code session, type `/voice` (optionally `/voice <topic>`) — or just say
*"talk to me using realtime voice"*.

## macOS permissions (one-time prompts)

1. **Microphone** — first time audio capture starts.
2. **Automation → control iTerm2** — first time a waiting session focuses
   your tab.

## How the concurrency works

- The slot is an exclusive lock on `~/.realtime-voice/audio.lock`.
- `converse` waits up to `wait_timeout` seconds (default 120) for the slot.
  On timeout it returns `[voice busy] held by '<project>'` so Claude can tell
  you in text and re-queue with a longer wait if you want.
- When a session that had to wait finally gets the slot, it:
  1. focuses the iTerm2 tab it was launched from (via `ITERM_SESSION_ID`),
  2. posts a macOS notification, and
  3. speaks *"Voice is free again — now talking to \<project\>"* before your
     message.
- When you finish a voice turn and someone else is queued, the tool result
  tells Claude another session is waiting so it can wrap up.
- `voice_status` shows the holder, the queue, and config.

## Status line indicator

During a voice turn the server writes the live phase to
`~/.realtime-voice/phase.json` (`{pid, label, phase, since}`), so a Claude
Code statusline can show **who is talking and whether it's speaking or
listening** — e.g. `🎙 listening (this tab)` or `🔊 speaking · other-project`.
`scripts/statusline-voice-segment.sh` prints that segment; call it from your
statusline script and append its output (it prints nothing when voice is
idle, and a stale file from a dead process is ignored via a pid check).

## Barge-in and echo

Barge-in keeps the mic hot while audio plays. There is **no echo
cancellation**, so on open laptop speakers the model may hear itself and
self-interrupt. Fixes, best first:

1. Use headphones (AirPods are fine).
2. macOS mic mode **Voice Isolation** (Control Center while mic is in use).
3. Disable barge-in: `REALTIME_VOICE_BARGE_IN=0` (half-duplex — mic is muted
   while Claude speaks).

## Configuration (env vars, or lines in `~/.realtime-voice/env`)

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. |
| `REALTIME_VOICE_MODEL` | `gpt-realtime-mini` | Realtime model. `gpt-realtime` sounds better, costs ~3x. |
| `REALTIME_VOICE_VOICE` | `marin` | Voice (`marin`, `cedar`, `alloy`, …). |
| `REALTIME_VOICE_TRANSCRIBE_MODEL` | `gpt-4o-mini-transcribe` | Input transcription model. |
| `REALTIME_VOICE_BARGE_IN` | `1` | `0` = mic muted during playback. |
| `REALTIME_VOICE_SILENCE_MS` | `600` | Trailing silence that ends your turn. Lower = snappier, but can clip mid-sentence pauses. |
| `REALTIME_VOICE_IDLE_S` | `300` | Close the kept-alive connection after this much idle time. |
| `REALTIME_VOICE_LOG` | `INFO` | Log level (stderr). |

## Cost

Realtime API bills per audio token, roughly per minute of speech.
`gpt-realtime-mini` is on the order of a few cents per conversational minute;
`gpt-realtime` about 3x that. The connection is kept open between turns for
latency, but idle time costs nothing (billing is per audio token, not per
connection), and every response is out-of-band (`conversation: "none"`) so
no history accumulates. The connection closes after
`REALTIME_VOICE_IDLE_S` of silence or when another session wants the slot.

## Layout

```
src/realtime_voice/
  server.py     # MCP tools: converse, voice_status; persistent session cache
  realtime.py   # RealtimeSession: speak verbatim, barge-in, transcribe
  audio.py      # mic/speaker I/O (PortAudio, 24 kHz PCM16)
  slot.py       # machine-wide audio slot lock + waiter queue
  iterm.py      # iTerm2 tab focus + macOS notifications
  config.py     # env config
scripts/voice_check.py  # standalone smoke test
```

## Known limitations

- Long pauses mid-sentence (> `SILENCE_MS`) end your turn early — raise
  `REALTIME_VOICE_SILENCE_MS` if it cuts you off.
- Queueing is unfair-ish under heavy contention (lock, not a strict FIFO).
- iTerm2 only for tab focus (falls back to doing nothing elsewhere).
