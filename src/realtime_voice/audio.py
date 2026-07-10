"""Mic capture and speaker playback: 24 kHz, mono, 16-bit PCM.

PortAudio callbacks run on their own thread; mic frames are handed to the
asyncio loop thread-safely, playback is fed from a locked byte buffer so
barge-in can flush it instantly.
"""

import asyncio
import logging
import threading

import sounddevice as sd

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK_FRAMES = 2400  # 100 ms per block
BYTES_PER_FRAME = 2  # int16 mono


class AudioIO:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self.mic_frames: asyncio.Queue[bytes] = asyncio.Queue()
        self._mic_on = threading.Event()
        self._play_buf = bytearray()
        self._play_lock = threading.Lock()
        self._in = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=BLOCK_FRAMES, callback=self._on_input,
        )
        self._out = sd.RawOutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=BLOCK_FRAMES, callback=self._on_output,
        )

    def __enter__(self) -> "AudioIO":
        self._in.start()
        self._out.start()
        return self

    def __exit__(self, *exc) -> None:
        for stream in (self._in, self._out):
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    # -- microphone ---------------------------------------------------------

    def set_mic(self, on: bool) -> None:
        if on:
            self._mic_on.set()
        else:
            self._mic_on.clear()

    def _on_input(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("input stream status: %s", status)
        if not self._mic_on.is_set():
            return
        data = bytes(indata)
        try:
            self._loop.call_soon_threadsafe(self.mic_frames.put_nowait, data)
        except RuntimeError:
            pass  # event loop already closed

    # -- speaker ------------------------------------------------------------

    def enqueue_playback(self, pcm: bytes) -> None:
        with self._play_lock:
            self._play_buf.extend(pcm)

    def clear_playback(self) -> None:
        with self._play_lock:
            self._play_buf.clear()

    def playing(self) -> bool:
        with self._play_lock:
            return len(self._play_buf) > 0

    async def drain_playback(self) -> None:
        while self.playing():
            await asyncio.sleep(0.05)
        # let the final block clear the DAC
        await asyncio.sleep(0.15)

    def _on_output(self, outdata, frames, time_info, status) -> None:
        need = frames * BYTES_PER_FRAME
        with self._play_lock:
            chunk = bytes(self._play_buf[:need])
            del self._play_buf[:need]
        outdata[: len(chunk)] = chunk
        if len(chunk) < need:
            outdata[len(chunk):need] = b"\x00" * (need - len(chunk))
