"""Where the scheduler keeps its lock, control socket, and saved state.

- Runtime files (lock, socket) live in /tmp/experiment-<uid>, a local,
  private (0700) directory. flock is unreliable on NFS, which is where home
  directories often are.
- The state file lives in ~/.local/state/experiment/<hostname>/<name>, keyed
  by hostname because the home directory is often shared between machines.
"""

import fcntl
import json
import os
import platform
import stat
import time

from pathlib import Path

STATE_VERSION = 1


def runtime_dir() -> Path:
    path = Path(
        os.environ.get("EXPERIMENT_RUNTIME_DIR")
        or f"/tmp/experiment-{os.getuid()}"
    )
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError(
            f"{path} exists but is not a directory owned by you."
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def socket_path() -> Path:
    return runtime_dir() / "scheduler.sock"


def state_dir(name: str) -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    path = Path(base) / "experiment" / platform.node() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class InstanceLock:
    """Allows one scheduler per user per machine.

    The kernel drops a flock when its process dies (even with SIGKILL), so a
    crashed scheduler never leaves a stale lock behind.
    """

    def __init__(self) -> None:
        self._path = runtime_dir() / "scheduler.lock"
        self._fd: int | None = None

    def acquire(self, info: dict) -> bool:
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(info).encode())
        os.fsync(fd)
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.ftruncate(self._fd, 0)
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def holder(self) -> dict | None:
        """Info written by the running scheduler, or None if none runs."""
        if self.is_free():
            return None
        try:
            return json.loads(self._path.read_text() or "null")
        except (OSError, json.JSONDecodeError):
            return {}

    def is_free(self) -> bool:
        if self._fd is not None:
            return False
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)


class StateStore:
    def __init__(self, name: str, min_interval: float = 1.0) -> None:
        self._dir = state_dir(name)
        self._path = self._dir / "state.json"
        self._min_interval = min_interval
        self._last_save = 0.0

    def directory(self) -> Path:
        return self._dir

    def path(self) -> Path:
        return self._path

    def load(self) -> dict | None:
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return None
        if data.get("version") != STATE_VERSION:
            raise ValueError(
                f"{self._path} has state version {data.get('version')}, "
                f"expected {STATE_VERSION}. Start with --fresh."
            )
        return data

    def save(self, data: dict, force: bool = False) -> bool:
        now = time.time()
        if not force and now - self._last_save < self._min_interval:
            return False
        data = dict(data, version=STATE_VERSION, saved_at=now)
        tmp = self._path.with_name(".state.json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)
        self._last_save = now
        return True

    def archive(self) -> Path | None:
        if not self._path.exists():
            return None
        target = self._dir / f"state.{time.strftime('%Y%m%d-%H%M%S')}.json"
        os.replace(self._path, target)
        return target
