"""The scheduler's handle on one worker machine."""

import ssl
import threading
import time

from enum import Enum
from pathlib import Path

import rpyc

from ..common import pki
from .work import Job, JobStatus
from .worker import PROTOCOL_CONFIG

MAX_BACKOFF = 60.0


class SIGNAL(Enum):
    TERM = 15
    INT = 2
    QUIT = 3
    KILL = 9


class JobError(Exception):
    """An operation on a single job failed; the host itself is fine."""


class HostUnreachable(Exception):
    """The connection to the host failed; it will be retried."""


class HostState(Enum):
    DOWN = "down"
    UP = "up"

    def __str__(self) -> str:
        return self.value


def _is_remote(exception: BaseException) -> bool:
    # NOTE: rpyc re-raises exceptions from the worker locally and tags them
    # with the remote traceback.
    return hasattr(exception, "_remote_tb")


def remote_message(exception: BaseException) -> str:
    """'Type: message' of a remote exception, without its traceback."""
    lines = str(getattr(exception, "_remote_tb", "")).strip().splitlines()
    return lines[-1] if lines else f"{type(exception).__name__}: {exception}"


class Host:
    def __init__(
        self,
        name: str,
        domain: str,
        max_capacity: int,
        port: int = 9100,
        *,
        cert: Path | str | None = None,
        key: Path | str | None = None,
        ca: Path | str | None = None,
        insecure: bool = False,
    ) -> None:
        """
        :param cert, key, ca: the scheduler's client certificate, its key,
            and the CA that signed the worker's certificate. Default to
            `scheduler.crt`, `scheduler.key` and `ca.crt` in the pki dir.
        :param insecure: connect without TLS. Only for a worker started with
            `--insecure-localhost` on this machine.
        """
        self._name = name
        self._domain = domain
        self._max_capacity = max_capacity
        self._port = port
        default_cert, default_key = pki.paths(pki.SCHEDULER)
        self._cert = Path(cert) if cert else default_cert
        self._key = Path(key) if key else default_key
        self._ca = Path(ca) if ca else pki.ca_path()
        self._insecure = insecure

        self._connection = None
        self._lock = threading.RLock()
        self._state = HostState.DOWN
        self._last_error: str | None = None
        self._failures = 0
        self._next_attempt = 0.0

    def name(self) -> str:
        return self._name

    def domain(self) -> str:
        return self._domain

    def max_capacity(self) -> int:
        return self._max_capacity

    def set_capacity(self, capacity: int) -> None:
        self._max_capacity = max(0, capacity)

    def state(self) -> HostState:
        return self._state

    def up(self) -> bool:
        return self._state == HostState.UP

    def last_error(self) -> str | None:
        return self._last_error

    def failures(self) -> int:
        """Consecutive failed connection attempts."""
        return self._failures

    def due_for_reconnect(self, now: float) -> bool:
        return self._state == HostState.DOWN and now >= self._next_attempt

    def _open(self):
        if self._insecure:
            if self._domain not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError(
                    "insecure connections are only allowed to localhost"
                )
            return rpyc.connect(
                self._domain,
                self._port,
                config=PROTOCOL_CONFIG,
                keepalive=True,
            )
        for path in (self._cert, self._key, self._ca):
            if not path.exists():
                raise FileNotFoundError(
                    f"{path} is missing. See `helper certs --help`."
                )
        pki.check_key_permissions(self._key)
        # NOTE: Passing cert_reqs explicitly keeps hostname checking on: the
        # worker's certificate must name `domain`.
        return rpyc.ssl_connect(
            self._domain,
            self._port,
            keyfile=str(self._key),
            certfile=str(self._cert),
            ca_certs=str(self._ca),
            cert_reqs=ssl.CERT_REQUIRED,
            config=PROTOCOL_CONFIG,
            keepalive=True,
        )

    def connect(self) -> bool:
        """Try to connect. On failure, schedule the next attempt."""
        with self._lock:
            if self._state == HostState.UP:
                return True
            try:
                self._connection = self._open()
                self._connection.root.ping()
            except Exception as e:
                self._mark_down(e)
                return False
            self._state = HostState.UP
            self._failures = 0
            self._last_error = None
            return True

    def disconnect(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
            self._connection = None
            self._state = HostState.DOWN

    def _mark_down(self, exception: BaseException) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connection = None
        self._state = HostState.DOWN
        self._failures += 1
        self._last_error = f"{type(exception).__name__}: {exception}"
        self._next_attempt = time.time() + min(
            MAX_BACKOFF, 2.0 ** min(self._failures, 6)
        )

    def _call(self, method: str, *args):
        if self._state != HostState.UP:
            # NOTE: Don't wait on the lock while a reconnect holds it.
            raise HostUnreachable(f"{self._name} is not connected")
        with self._lock:
            if self._state != HostState.UP or self._connection is None:
                raise HostUnreachable(f"{self._name} is not connected")
            try:
                return getattr(self._connection.root, method)(*args)
            except Exception as e:
                if _is_remote(e):
                    raise JobError(remote_message(e)) from None
                self._mark_down(e)
                raise HostUnreachable(
                    f"{self._name}: {self._last_error}"
                ) from e

    def launch(self, job: Job) -> None:
        self._call(
            "launch_job",
            job.id(),
            job.cwd().as_posix(),
            job.command(),
            job.outdir().as_posix(),
            tuple((label, path.as_posix()) for label, path in job.files()),
            tuple((content, path.as_posix()) for _, content, path in job.dumps()),
        )

    def statuses(
        self, jobs: list[Job]
    ) -> list[tuple[str, int | None, str | None]]:
        if not jobs:
            return []
        return list(
            self._call(
                "job_statuses", tuple(job.outdir().as_posix() for job in jobs)
            )
        )

    def signal(self, job: Job, signum: int) -> bool:
        return bool(self._call("signal_job", job.outdir().as_posix(), signum))

    def read_file(
        self, outdir: Path, label: str, offset: int, length: int
    ) -> bytes:
        return self._call(
            "read_file", Path(outdir).as_posix(), label, offset, length
        )

    def spec(self) -> dict:
        return {
            "name": self._name,
            "domain": self._domain,
            "max_capacity": self._max_capacity,
            "port": self._port,
            "cert": str(self._cert),
            "key": str(self._key),
            "ca": str(self._ca),
            "insecure": self._insecure,
        }

    @classmethod
    def from_spec(cls, spec: dict) -> "Host":
        return cls(
            name=spec["name"],
            domain=spec["domain"],
            max_capacity=spec["max_capacity"],
            port=spec["port"],
            cert=spec.get("cert"),
            key=spec.get("key"),
            ca=spec.get("ca"),
            insecure=spec.get("insecure", False),
        )

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__}(name={self._name}, "
            f"domain={self._domain}:{self._port}, "
            f"capacity={self._max_capacity})"
        )

    def __repr__(self) -> str:
        return self.__str__()


def job_status_from_worker(status: str) -> JobStatus | None:
    return {
        "running": JobStatus.RUNNING,
        "exited": JobStatus.EXITED,
        "failed": JobStatus.FAILED,
        "killed": JobStatus.KILLED,
    }.get(status)
