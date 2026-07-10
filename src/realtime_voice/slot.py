"""Cross-process audio slot: only one Claude Code session may use the mic
and speakers at a time.

An exclusive flock on ~/.realtime-voice/audio.lock is the slot. Holders
advertise themselves in current.json; sessions stuck waiting advertise
themselves in waiters/<pid>.json so everyone can see the queue.
"""

import asyncio
import fcntl
import json
import os
import time
from pathlib import Path

from .config import STATE_DIR

LOCK_FILE = STATE_DIR / "audio.lock"
CURRENT_FILE = STATE_DIR / "current.json"
WAITERS_DIR = STATE_DIR / "waiters"


class SlotBusy(Exception):
    def __init__(self, holder: dict | None):
        self.holder = holder
        super().__init__("audio slot busy")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def holder_info() -> dict | None:
    try:
        info = json.loads(CURRENT_FILE.read_text())
    except (OSError, ValueError):
        return None
    return info if _pid_alive(info.get("pid", -1)) else None


def waiter_infos(exclude_pid: int | None = None) -> list[dict]:
    out = []
    if not WAITERS_DIR.is_dir():
        return out
    for f in WAITERS_DIR.glob("*.json"):
        try:
            info = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        pid = info.get("pid")
        if not _pid_alive(pid):
            f.unlink(missing_ok=True)
            continue
        if pid == exclude_pid:
            continue
        out.append(info)
    return sorted(out, key=lambda i: i.get("since", 0))


class AudioSlot:
    def __init__(self, label: str, meta: dict | None = None):
        self.label = label
        self.meta = meta or {}
        self._fd: int | None = None

    async def acquire(self, timeout: float) -> bool:
        """Block until the slot is ours. Returns True if we had to wait.

        Raises SlotBusy (carrying the current holder's info) on timeout.
        """
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        WAITERS_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        waiter_file = WAITERS_DIR / f"{os.getpid()}.json"
        waited = False
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not waited:
                        waited = True
                        waiter_file.write_text(json.dumps({
                            "pid": os.getpid(),
                            "label": self.label,
                            "since": time.time(),
                            **self.meta,
                        }))
                    if time.monotonic() >= deadline:
                        raise SlotBusy(holder_info())
                    await asyncio.sleep(0.5)
        except BaseException:
            os.close(fd)
            raise
        finally:
            waiter_file.unlink(missing_ok=True)
        self._fd = fd
        CURRENT_FILE.write_text(json.dumps({
            "pid": os.getpid(),
            "label": self.label,
            "since": time.time(),
            **self.meta,
        }))
        return waited

    def release(self) -> list[str]:
        """Give the slot back; returns labels of sessions still waiting."""
        waiting = [w.get("label", "?") for w in waiter_infos(exclude_pid=os.getpid())]
        CURRENT_FILE.unlink(missing_ok=True)
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        return waiting
