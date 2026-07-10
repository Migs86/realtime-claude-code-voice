"""Persistent speak-and-listen sessions over the OpenAI Realtime API (GA).

Claude Code stays the brain. The Realtime model is only the mouth and ears:
- Speak: a response.create instructed to say Claude's message verbatim,
  streamed to the speakers as it generates (low latency).
- Barge-in: the mic stays hot during playback; when server VAD reports
  speech_started we cancel the response and flush local playback instantly.
- Listen: server VAD detects end of the user's utterance; we return the
  input transcription. turn_detection.create_response is false, so the
  Realtime model never answers on its own.

A RealtimeSession keeps one WebSocket open across turns, so repeat turns
skip the TLS + WebSocket handshake (~300-500 ms). Every response is
out-of-band (conversation: "none"), so assistant output never accumulates
in the server-side conversation and each turn stays a pure verbatim relay.
"""

import asyncio
import base64
import json
import logging

import websockets

from . import config
from .audio import AudioIO

log = logging.getLogger(__name__)

VERBATIM_INSTRUCTIONS = (
    "You are the voice of a coding assistant. You are a pure text-to-speech "
    "relay: when asked to say something, say it exactly as written, with "
    "natural pacing and intonation. Never add, omit, or change words. Never "
    "answer questions yourself."
)


class RealtimeError(Exception):
    pass


class _State:
    def __init__(self) -> None:
        self.speech_started = asyncio.Event()
        self.speech_stopped = asyncio.Event()
        self.response_done = asyncio.Event()
        self.transcript_done = asyncio.Event()
        self.errored = asyncio.Event()
        self.transcript = ""
        self.error: dict | None = None
        # Gate for playback: deltas that trickle in after a barge-in cancel
        # (or after the turn ends) must not reach the speakers, because the
        # output stream now outlives the turn.
        self.speaking = False


async def _wait(st: _State, event: asyncio.Event, timeout: float | None) -> bool:
    """Wait for an event, racing against a fatal API error. False on timeout."""
    ev_task = asyncio.ensure_future(event.wait())
    err_task = asyncio.ensure_future(st.errored.wait())
    done, pending = await asyncio.wait(
        {ev_task, err_task}, timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    if err_task in done:
        raise RealtimeError(str(st.error))
    return bool(done)


def _session_update(voice: str, silence_ms: int) -> dict:
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": VERBATIM_INSTRUCTIONS,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": config.SAMPLE_RATE},
                    "transcription": {"model": config.TRANSCRIBE_MODEL},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "silence_duration_ms": silence_ms,
                        "create_response": False,
                        "interrupt_response": False,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": config.SAMPLE_RATE},
                    "voice": voice,
                },
            },
        },
    }


class RealtimeSession:
    """One WebSocket to the Realtime API, reused across converse turns."""

    def __init__(
        self,
        audio: AudioIO,
        *,
        voice: str = config.VOICE,
        model: str = config.MODEL,
        silence_ms: int = config.SILENCE_MS,
    ) -> None:
        self.audio = audio
        self.voice = voice
        self.model = model
        self.silence_ms = silence_ms
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._st: _State | None = None

    @property
    def connected(self) -> bool:
        return (
            self._ws is not None
            and self._recv_task is not None
            and not self._recv_task.done()
        )

    def matches(self, *, voice: str, model: str, silence_ms: int) -> bool:
        # Voice can't change once a session has produced audio, so a
        # different voice (or model/VAD config) needs a fresh session.
        return (
            self.voice == voice
            and self.model == model
            and self.silence_ms == silence_ms
        )

    async def connect(self) -> None:
        key = config.api_key()
        if not key:
            raise RealtimeError("OPENAI_API_KEY is not set")
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {key}"},
            max_size=1 << 24,
        )
        # Don't block on the session.updated ack. The API applies events in
        # order, so a response.create / mic append sent right after is still
        # processed against the updated session — this saves a full
        # round-trip before the first audio. A bad session config still
        # surfaces as an error event, which every _wait races against.
        await self._ws.send(json.dumps(_session_update(self.voice, self.silence_ms)))
        self._recv_task = asyncio.create_task(self._receiver())
        self._send_task = asyncio.create_task(self._mic_sender())

    async def close(self) -> None:
        for t in (self._recv_task, self._send_task):
            if t is not None:
                t.cancel()
        self._recv_task = self._send_task = None
        self._st = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def run_turn(
        self,
        *,
        message: str,
        listen: bool = True,
        barge_in: bool = True,
        listen_timeout: float = 60.0,
        utterance_timeout: float = 120.0,
    ) -> dict:
        """Speak `message`, optionally listen for a reply.

        Returns {"status": "ok"|"silence"|"no-transcript",
                 "transcript": str|None, "barged_in": bool}.
        """
        if not self.connected:
            await self.close()
            await self.connect()
        st = self._st = _State()
        barged = False
        try:
            # Start from a clean input buffer so uncommitted audio from a
            # previous turn can't leak into this one.
            await self._send({"type": "input_audio_buffer.clear"})

            if message:
                self.audio.clear_playback()
                # Hot mic during playback only when barge-in is wanted.
                self.audio.set_mic(bool(listen and barge_in))
                st.speaking = True
                await self._send({
                    "type": "response.create",
                    "response": {
                        # Out-of-band: don't add this response to the
                        # server-side conversation, so history never grows
                        # across the life of the session.
                        "conversation": "none",
                        "output_modalities": ["audio"],
                        "instructions": "Say exactly this, verbatim:\n\n" + message,
                    },
                })
                barged = await self._speak_phase(st)
                st.speaking = False

            if not listen:
                return {"status": "ok", "transcript": None, "barged_in": barged}

            self.audio.set_mic(True)
            if not st.speech_started.is_set():
                if not await _wait(st, st.speech_started, listen_timeout):
                    return {"status": "silence", "transcript": None, "barged_in": barged}
            if not await _wait(st, st.speech_stopped, utterance_timeout):
                return {"status": "no-transcript", "transcript": None, "barged_in": barged}
            if not await _wait(st, st.transcript_done, 30.0):
                return {"status": "no-transcript", "transcript": None, "barged_in": barged}
            return {"status": "ok", "transcript": st.transcript, "barged_in": barged}
        finally:
            self.audio.set_mic(False)
            if self._st is st:
                self._st = None

    # -- internals ------------------------------------------------------

    async def _send(self, payload: dict) -> None:
        try:
            await self._ws.send(json.dumps(payload))
        except websockets.ConnectionClosed as e:
            raise RealtimeError(f"connection closed: {e}") from e

    async def _receiver(self) -> None:
        try:
            async for raw in self._ws:
                ev = json.loads(raw)
                etype = ev.get("type", "")
                st = self._st
                if etype in ("response.output_audio.delta", "response.audio.delta"):
                    if st is not None and st.speaking:
                        self.audio.enqueue_playback(base64.b64decode(ev["delta"]))
                elif st is None:
                    log.debug("event between turns: %s", etype)
                elif etype == "input_audio_buffer.speech_started":
                    st.speech_started.set()
                elif etype == "input_audio_buffer.speech_stopped":
                    st.speech_stopped.set()
                elif etype == "conversation.item.input_audio_transcription.completed":
                    piece = (ev.get("transcript") or "").strip()
                    if piece:
                        st.transcript = f"{st.transcript} {piece}".strip()
                    st.transcript_done.set()
                elif etype == "response.done":
                    st.response_done.set()
                elif etype == "error":
                    err = ev.get("error") or {}
                    # Benign race: we cancelled a response that had already
                    # finished (barge-in during the playback drain).
                    if err.get("code") == "response_cancel_not_active":
                        log.debug("ignoring benign error: %s", err)
                    else:
                        st.error = err
                        log.error("realtime error event: %s", st.error)
                        st.errored.set()
                else:
                    log.debug("event: %s", etype)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass

    async def _mic_sender(self) -> None:
        try:
            while True:
                frame = await self.audio.mic_frames.get()
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(frame).decode("ascii"),
                }))
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass

    async def _speak_phase(self, st: _State) -> bool:
        """Play the response; return True if the user barged in."""

        async def done_and_drained() -> None:
            await st.response_done.wait()
            await self.audio.drain_playback()

        done = asyncio.ensure_future(done_and_drained())
        barge = asyncio.ensure_future(st.speech_started.wait())
        err = asyncio.ensure_future(st.errored.wait())
        finished, pending = await asyncio.wait(
            {done, barge, err}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if err in finished:
            raise RealtimeError(str(st.error))
        if barge in finished and done not in finished:
            st.speaking = False
            self.audio.clear_playback()
            if not st.response_done.is_set():
                try:
                    await self._ws.send(json.dumps({"type": "response.cancel"}))
                except websockets.ConnectionClosed:
                    pass
            return True
        return False


async def run_turn(audio: AudioIO, *, message: str, **kwargs) -> dict:
    """One-shot convenience: connect, run a single turn, close.

    Kept for scripts/voice_check.py; the MCP server holds a RealtimeSession
    open across turns instead.
    """
    voice = kwargs.pop("voice", config.VOICE)
    model = kwargs.pop("model", config.MODEL)
    silence_ms = kwargs.pop("silence_ms", config.SILENCE_MS)
    session = RealtimeSession(
        audio, voice=voice, model=model, silence_ms=silence_ms
    )
    try:
        return await session.run_turn(message=message, **kwargs)
    finally:
        await session.close()
