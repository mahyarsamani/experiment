"""The service each worker machine runs; the scheduler drives it over rpyc.

Everything the worker needs to know about a job lives in the job's outdir
(see `runner.py`), so the worker can be restarted while jobs run. Exposed
methods take and return only immutable builtins (str, int, float, bytes,
tuples) so rpyc passes them by value rather than as remote references.
"""

import os
import signal as signal_module
import subprocess
import sys
import threading
import time

from pathlib import Path

import psutil
import rpyc

from .runner import (
    EXIT_FILE,
    FILES_FILE,
    JOB_DIR,
    LAUNCH_FILE,
    read_json,
    write_json_atomic,
)

MAX_READ = 1 << 20
ALLOWED_SIGNALS = {
    int(signal_module.SIGTERM),
    int(signal_module.SIGINT),
    int(signal_module.SIGQUIT),
    int(signal_module.SIGKILL),
}

PROTOCOL_CONFIG = {
    "allow_public_attrs": False,
    "allow_all_attrs": False,
    "allow_pickle": False,
    "allow_setattr": False,
    "allow_delattr": False,
    "sync_request_timeout": 30,
}


def _job_dir(outdir: Path) -> Path:
    return outdir / JOB_DIR


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _leader_alive(launch: dict) -> bool:
    """Is the process recorded in launch.json still the same live process?"""
    try:
        proc = psutil.Process(launch["pid"])
        # NOTE: Guards against the pid having been reused by another process.
        if abs(proc.create_time() - launch["create_time"]) > 1.0:
            return False
        return proc.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


Status = tuple[str, int | None, str | None, float | None, float | None]


def resolve_status(outdir: Path) -> Status:
    """Returns (status, returncode, message, start_time, end_time) for the
    job in `outdir`. Times are epoch seconds on this machine's clock.

    status is one of "missing", "running", "exited", "failed", "killed",
    and "retry" (the job's watcher asked for it to be queued again).
    """
    job_dir = _job_dir(outdir)
    launch = read_json(job_dir / LAUNCH_FILE)
    if launch is None:
        return "missing", None, None, None, None
    start = launch.get("start_time")

    def from_exit(record: dict) -> Status:
        rc = record.get("returncode")
        end = record.get("end_time")
        verdict = record.get("watcher")
        if verdict:
            status = "retry" if verdict["action"] == "retry" else "failed"
            return status, rc, f"watcher: {verdict['message']}", start, end
        if rc == 0:
            return "exited", rc, None, start, end
        if rc is not None and rc < 0 and -rc in record.get(
            "signals_received", []
        ):
            return "killed", rc, f"stopped by signal {-rc}", start, end
        if rc is not None and rc < 0:
            return "failed", rc, f"terminated by signal {-rc}", start, end
        return "failed", rc, record.get("error"), start, end

    record = read_json(job_dir / EXIT_FILE)
    if record is not None:
        return from_exit(record)
    if _leader_alive(launch):
        return "running", None, None, start, None
    # NOTE: The runner may have written exit.json right after our first look.
    record = read_json(job_dir / EXIT_FILE)
    if record is not None:
        return from_exit(record)
    return (
        "killed",
        None,
        "ended without an exit record (e.g. SIGKILL)",
        start,
        None,
    )


class Worker(rpyc.Service):
    def __init__(self) -> None:
        super().__init__()
        self._children: dict[str, subprocess.Popen] = dict()
        self._lock = threading.Lock()

    def _reap(self) -> None:
        # NOTE: Runners are our children until we restart; poll them so they
        # don't linger as zombies.
        with self._lock:
            for key, child in list(self._children.items()):
                if child.poll() is not None:
                    del self._children[key]

    def exposed_ping(self) -> str:
        return os.uname().nodename

    def exposed_launch_job(
        self,
        job_id: str,
        cwd: str,
        command: str,
        outdir: str,
        files: tuple,
        dumps: tuple,
    ) -> int:
        self._reap()
        out = Path(outdir)
        if not out.is_absolute() or not Path(cwd).is_absolute():
            raise ValueError("cwd and outdir must be absolute paths")
        job_dir = _job_dir(out)
        job_dir.mkdir(parents=True, exist_ok=True)

        status = resolve_status(out)[0]
        if status == "running":
            raise RuntimeError(f"a job is already running in {out}")
        for name in (LAUNCH_FILE, EXIT_FILE):
            (job_dir / name).unlink(missing_ok=True)

        allowed = {}
        for label, path in tuple(files):
            p = Path(path)
            if not _inside(p, out):
                raise ValueError(f"file {label}={p} is outside {out}")
            allowed[str(label)] = str(p)
        for content, path in tuple(dumps):
            p = Path(path)
            if not _inside(p, out):
                raise ValueError(f"dump {p} is outside {out}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(content))
        write_json_atomic(job_dir / FILES_FILE, allowed)

        with open(job_dir / "runner.log", "w") as runner_log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "experiment.api.runner",
                    "--outdir",
                    str(out),
                    "--cwd",
                    str(cwd),
                    "--job-id",
                    str(job_id),
                    "--",
                    str(command),
                ],
                stdin=subprocess.DEVNULL,
                stdout=runner_log,
                stderr=runner_log,
                start_new_session=True,
                close_fds=True,
            )
        try:
            create_time = psutil.Process(child.pid).create_time()
        except psutil.Error:
            create_time = time.time()
        write_json_atomic(
            job_dir / LAUNCH_FILE,
            {
                "job_id": str(job_id),
                "pid": child.pid,
                "pgid": child.pid,
                "create_time": create_time,
                "start_time": time.time(),
                "cwd": str(cwd),
                "command": str(command),
            },
        )
        with self._lock:
            self._children[str(out)] = child
        return child.pid

    def exposed_job_statuses(self, outdirs: tuple) -> tuple:
        self._reap()
        return tuple(resolve_status(Path(outdir)) for outdir in tuple(outdirs))

    def exposed_signal_job(self, outdir: str, signum: int) -> bool:
        """Returns False if the job wasn't running (nothing to signal)."""
        signum = int(signum)
        if signum not in ALLOWED_SIGNALS:
            raise ValueError(f"signal {signum} is not allowed")
        launch = read_json(_job_dir(Path(outdir)) / LAUNCH_FILE)
        if launch is None or not _leader_alive(launch):
            return False
        try:
            os.killpg(launch["pgid"], signum)
        except ProcessLookupError:
            return False
        return True

    def exposed_read_file(
        self, outdir: str, label: str, offset: int, length: int
    ) -> bytes:
        out = Path(outdir)
        allowed = read_json(_job_dir(out) / FILES_FILE) or {}
        if label not in allowed:
            raise PermissionError(f"{label} is not a file of this job")
        path = Path(allowed[label])
        if not _inside(path, out):
            raise PermissionError(f"{path} is outside {out}")
        try:
            with open(path, "rb") as f:
                f.seek(max(0, int(offset)))
                return f.read(max(0, min(int(length), MAX_READ)))
        except FileNotFoundError:
            raise FileNotFoundError(f"{label} does not exist (yet)")
