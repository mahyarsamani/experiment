"""The scheduler: places queued jobs on hosts and tracks them to completion.

Threading model: all scheduler state is owned by the thread running
`Scheduler.run`. Other threads (the console server, the dashboard) never
touch it directly; they `submit` commands, which run on the scheduler thread
between ticks, and read an immutable `Snapshot` published after every tick.
"""

import hashlib
import importlib.util
import json
import logging
import sys
import threading
import time

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterable

from ..host import (
    SIGNAL,
    Host,
    HostUnreachable,
    JobError,
    job_status_from_worker,
)
from ..work import Experiment, Job, JobStatus
from .state import StateStore


class CommandError(Exception):
    """A command was refused; the message is meant for the user."""


class HostMode(Enum):
    ACTIVE = "active"
    DRAINING = "draining"
    REMOVING = "removing"

    def __str__(self) -> str:
        return self.value


@dataclass
class Command:
    name: str
    kwargs: dict
    future: Future = field(default_factory=Future)


class EventLog:
    """Recent notable events, for consoles to show and catch up on."""

    LEVELS = {
        "info": logging.INFO,
        "warn": logging.WARNING,
        "error": logging.ERROR,
    }

    def __init__(self, logger: logging.Logger, size: int = 1000) -> None:
        self._logger = logger
        self._events: deque = deque(maxlen=size)
        self._seq = 0
        self._lock = threading.Lock()

    def add(self, level: str, message: str) -> None:
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, time.time(), level, message))
        self._logger.log(self.LEVELS[level], message)

    def info(self, message: str) -> None:
        self.add("info", message)

    def warn(self, message: str) -> None:
        self.add("warn", message)

    def error(self, message: str) -> None:
        self.add("error", message)

    def since(self, seq: int) -> list[tuple[int, float, str, str]]:
        with self._lock:
            return [event for event in self._events if event[0] > seq]

    def last_seq(self) -> int:
        with self._lock:
            return self._seq


@dataclass(frozen=True)
class Snapshot:
    taken_at: float
    info: dict
    hosts: tuple
    experiments: tuple
    jobs: tuple
    # NOTE: Not serialized; used by the dashboard to proxy file reads.
    job_locations: dict = field(default_factory=dict)
    host_objects: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "taken_at": self.taken_at,
            "info": self.info,
            "hosts": list(self.hosts),
            "experiments": list(self.experiments),
            "jobs": list(self.jobs),
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_objects(script: Path) -> tuple[list[Host], list[Experiment]]:
    """Run `script` and collect the Hosts and Experiments it defines.

    Looks at the script's top-level variables, and inside dicts, lists,
    tuples and sets they hold.
    """
    mod_name = f"_plugin_{hashlib.sha1(str(script).encode()).hexdigest()}"
    spec = importlib.util.spec_from_file_location(mod_name, str(script))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {script}")
    module = importlib.util.module_from_spec(spec)
    # NOTE: Let the script import modules that sit next to it.
    sys.path.insert(0, str(script.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(script.parent))

    def walk(obj: Any, seen: set[int]) -> Iterable[Any]:
        if id(obj) in seen:
            return
        seen.add(id(obj))
        yield obj
        if isinstance(obj, dict):
            for value in obj.values():
                yield from walk(value, seen)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for value in obj:
                yield from walk(value, seen)

    hosts, experiments, seen = [], [], set()
    for value in vars(module).values():
        for obj in walk(value, seen):
            if isinstance(obj, Host):
                hosts.append(obj)
            elif isinstance(obj, Experiment):
                experiments.append(obj)
    return hosts, experiments


class Scheduler:
    SIGNALS = {signal.name.lower(): signal.value for signal in SIGNAL}

    def __init__(
        self,
        name: str,
        polling_secs: float = 1.0,
        store: StateStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._name = name
        self._polling_secs = polling_secs
        self._store = store
        self._logger = logger or logging.getLogger(f"{name}.scheduler")
        self._events = EventLog(self._logger)
        self._info: dict = {"name": name, "started_at": time.time()}

        self._hosts: dict[str, Host] = dict()
        self._host_mode: dict[str, HostMode] = dict()
        self._experiments: dict[str, Experiment] = dict()
        self._jobs: dict[str, Job] = dict()
        self._killing: set[str] = set()
        self._scripts: dict[str, str] = dict()
        # NOTE: The script that first defined each job, for `delete --script`.
        self._job_origin: dict[str, str] = dict()
        # NOTE: Deleted by the user; loading a script skips them.
        self._deleted_jobs: set[str] = set()
        self._deleted_experiments: set[str] = set()
        self._dirty = True

        self._connect_pool = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix=f"{name}.connect"
        )
        self._connecting: dict[str, Future] = dict()
        self._reported_errors: dict[str, str | None] = dict()
        # NOTE: Jobs whose compatible_with_host raised, reported once each.
        self._bad_compatibility: set[str] = set()

        self._commands: Queue[Command] = Queue()
        self._stop = threading.Event()
        self._stopped = threading.Event()
        self._snapshot_lock = threading.Lock()
        self._snapshot = Snapshot(time.time(), dict(self._info), (), (), ())

        self._handlers = {
            "load": self._cmd_load,
            "reload": self._cmd_reload,
            "kill": self._cmd_kill,
            "drain": self._cmd_drain,
            "undrain": self._cmd_undrain,
            "remove": self._cmd_remove,
            "capacity": self._cmd_capacity,
            "signal": self._cmd_signal,
            "reset": self._cmd_reset,
            "stop": self._cmd_stop,
            "delete": self._cmd_delete,
            "undelete": self._cmd_undelete,
        }

    # ---- Thread-safe interface -------------------------------------------

    def events(self) -> EventLog:
        return self._events

    def set_info(self, **info) -> None:
        # NOTE: Called before `run` starts, e.g. with the dashboard URL.
        self._info.update(info)

    def snapshot(self) -> Snapshot:
        with self._snapshot_lock:
            return self._snapshot

    def commands(self) -> list[str]:
        return list(self._handlers)

    def submit(self, name: str, **kwargs) -> Future:
        if name not in self._handlers:
            raise CommandError(f"unknown command {name!r}")
        command = Command(name, kwargs)
        if self._stopped.is_set():
            command.future.set_exception(
                CommandError("the scheduler is stopping")
            )
        else:
            self._commands.put(command)
        return command.future

    def call(self, name: str, timeout: float = 60, **kwargs) -> list[str]:
        return self.submit(name, **kwargs).result(timeout=timeout)

    def stop(self) -> None:
        self._stop.set()

    def stopping(self) -> bool:
        return self._stop.is_set()

    def wait_stopped(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def read_job_file(
        self, job_id: str, label: str, offset: int, length: int
    ) -> bytes:
        snapshot = self.snapshot()
        location = snapshot.job_locations.get(job_id)
        if location is None:
            raise CommandError(f"no job {job_id}")
        outdir, host_name = location
        host = snapshot.host_objects.get(host_name)
        if host is None or not host.up():
            raise HostUnreachable(f"host {host_name} is not connected")
        return host.read_file(outdir, label, offset, length)

    # ---- Scheduler thread ------------------------------------------------

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                deadline = time.time() + self._polling_secs
                try:
                    self._tick()
                except Exception:
                    self._logger.exception("Unhandled error in scheduler tick.")
                self._handle_commands(deadline)
        finally:
            self._shutdown()

    def _handle_commands(self, deadline: float) -> None:
        """Run commands as they arrive until `deadline`.

        Returns early after a command so its effects are scheduled promptly.
        Pending commands always run, even if the tick used up the interval.
        """
        while not self._stop.is_set():
            timeout = deadline - time.time()
            try:
                if timeout <= 0:
                    command = self._commands.get_nowait()
                else:
                    command = self._commands.get(timeout=min(timeout, 0.2))
            except Empty:
                if timeout <= 0:
                    return
                continue
            self._run_command(command)
            while True:
                try:
                    self._run_command(self._commands.get_nowait())
                except Empty:
                    break
            # NOTE: So a view right after a command reflects it.
            self._publish()
            return

    def _run_command(self, command: Command) -> None:
        if not command.future.set_running_or_notify_cancel():
            return
        try:
            result = self._handlers[command.name](**command.kwargs)
        except CommandError as e:
            command.future.set_exception(e)
        except Exception as e:
            self._logger.exception(f"Command {command.name} failed.")
            command.future.set_exception(
                CommandError(f"{type(e).__name__}: {e}")
            )
        else:
            self._dirty = True
            command.future.set_result(result)

    def _shutdown(self) -> None:
        self._stopped.set()
        while True:
            try:
                command = self._commands.get_nowait()
            except Empty:
                break
            command.future.set_exception(
                CommandError("the scheduler is stopping")
            )
        try:
            self._persist(force=True)
        except Exception:
            self._logger.exception("Could not save state on shutdown.")
        for host in self._hosts.values():
            host.disconnect()
        self._connect_pool.shutdown(wait=False, cancel_futures=True)
        self._events.info("Scheduler stopped.")

    def _tick(self) -> None:
        self._reconnect_hosts()
        for host in list(self._hosts.values()):
            if host.up():
                self._poll_host(host)
        self._process_killing()
        self._process_host_removal()
        self._schedule()
        self._publish()
        self._persist()

    def _active_jobs(self, host_name: str) -> list[Job]:
        return [
            job
            for job in self._jobs.values()
            if job.host() == host_name and job.status().active()
        ]

    def _used_capacity(self) -> dict[str, int]:
        used = {name: 0 for name in self._hosts}
        for job in self._jobs.values():
            if job.status().active() and job.host() in used:
                used[job.host()] += job.demand()
        return used

    def _reconnect_hosts(self) -> None:
        for name, future in list(self._connecting.items()):
            if not future.done():
                continue
            del self._connecting[name]
            host = self._hosts.get(name)
            if host is None:
                continue
            if host.up():
                self._events.info(f"Host {name} connected.")
                self._reported_errors.pop(name, None)
                self._dirty = True
            elif (
                self._reported_errors.get(name) != host.last_error()
                or host.failures() % 10 == 0
            ):
                # NOTE: Report each new kind of error, and repeats only now
                # and then.
                self._reported_errors[name] = host.last_error()
                self._events.warn(
                    f"Can't reach host {name} ({host.last_error()}); "
                    "retrying in the background."
                )
        now = time.time()
        for name, host in self._hosts.items():
            if name not in self._connecting and host.due_for_reconnect(now):
                self._connecting[name] = self._connect_pool.submit(
                    host.connect
                )

    def _host_lost(self, host: Host, error: Exception) -> None:
        affected = 0
        for job in self._active_jobs(host.name()):
            if job.status() != JobStatus.UNKNOWN:
                job.set_status(JobStatus.UNKNOWN)
                affected += 1
        self._dirty = True
        self._events.warn(
            f"Lost host {host.name()} ({error}); {affected} job(s) are now "
            "unknown and will be checked when it reconnects."
        )

    def _poll_host(self, host: Host) -> None:
        jobs = self._active_jobs(host.name())
        try:
            results = host.statuses(jobs)
        except HostUnreachable as e:
            self._host_lost(host, e)
            return
        except JobError as e:
            self._events.error(f"Polling {host.name()} failed: {e}")
            return
        for job, (status, returncode, message, start, end) in zip(
            jobs, results
        ):
            before = job.status()
            if (start, end) != (job.start_time(), job.end_time()) and (
                start is not None or end is not None
            ):
                job.set_times(start, end)
                self._dirty = True
            if status == "missing":
                if before == JobStatus.UNKNOWN:
                    # NOTE: The host has no record of this job, so it never
                    # started (e.g. the scheduler died mid-launch).
                    job.forget()
                    self._events.info(
                        f"Job {job.id()[:8]} never started on {host.name()}; "
                        "queued it again."
                    )
                else:
                    job.set_status(
                        JobStatus.FAILED,
                        message="launch record disappeared "
                        "(was the outdir deleted?)",
                    )
                self._dirty = True
                continue
            if status == "retry":
                self._retry(job, host, message or "watcher asked to retry")
                continue
            new = job_status_from_worker(status)
            if new is None or new == before:
                continue
            job.set_status(new, returncode, message)
            if new.terminal() and job.end_time() is None:
                # NOTE: No exit record (e.g. SIGKILL): when we noticed.
                job.set_times(None, time.time())
            self._dirty = True
            if new == JobStatus.RUNNING:
                self._events.info(
                    f"Job {job.id()[:8]} is running on {host.name()}."
                )
            elif new == JobStatus.EXITED:
                self._events.info(
                    f"Job {job.id()[:8]} ({job.shorthand_command()}) finished."
                )
            elif new == JobStatus.FAILED:
                self._events.error(
                    f"Job {job.id()[:8]} ({job.shorthand_command()}) failed "
                    f"on {host.name()}: returncode={returncode}"
                    + (f", {message}" if message else "")
                )
            elif new == JobStatus.KILLED:
                self._events.warn(
                    f"Job {job.id()[:8]} ({job.shorthand_command()}) was killed"
                    + (f": {message}" if message else ".")
                )

    def _retry(self, job: Job, host: Host, message: str) -> None:
        self._dirty = True
        if job.retry(message):
            self._events.warn(
                f"Job {job.id()[:8]} ({job.shorthand_command()}) on "
                f"{host.name()}: {message}; queued it again "
                f"({job.retries()}/{job.max_retries})."
            )
            return
        job.set_status(
            JobStatus.FAILED,
            message=f"{message} (gave up after {job.max_retries} retries)",
        )
        if job.end_time() is None:
            job.set_times(None, time.time())
        self._events.error(
            f"Job {job.id()[:8]} ({job.shorthand_command()}) on "
            f"{host.name()}: {message}; gave up after {job.max_retries} "
            "retries."
        )

    def _process_killing(self) -> None:
        for name in list(self._killing):
            experiment = self._experiments[name]
            active = [job for job in experiment.jobs() if job.status().active()]
            if not active:
                self._remove_experiment(name)
                self._events.info(f"Experiment {name} killed and removed.")
                continue
            for job in active:
                host = self._hosts.get(job.host())
                if host is None or not host.up():
                    continue
                try:
                    host.signal(job, SIGNAL.KILL.value)
                except HostUnreachable as e:
                    self._host_lost(host, e)
                except JobError as e:
                    self._events.error(
                        f"Killing job {job.id()[:8]} on {host.name()}: {e}"
                    )

    def _remove_experiment(self, name: str) -> None:
        experiment = self._experiments.pop(name)
        self._killing.discard(name)
        for job in experiment.jobs():
            self._forget_job(job)
        self._dirty = True

    def _forget_job(self, job: Job) -> None:
        self._jobs.pop(job.id(), None)
        self._job_origin.pop(job.id(), None)
        experiment = self._experiments.get(job.experiment())
        if experiment is not None:
            experiment.remove_job(job.id())
        self._dirty = True

    def _process_host_removal(self) -> None:
        for name, mode in list(self._host_mode.items()):
            if mode == HostMode.REMOVING and not self._active_jobs(name):
                self._drop_host(name)
                self._events.info(f"Host {name} removed.")

    def _drop_host(self, name: str) -> None:
        host = self._hosts.pop(name)
        self._host_mode.pop(name)
        self._connecting.pop(name, None)
        host.disconnect()
        self._dirty = True

    def _schedule(self) -> None:
        experiments = [
            experiment
            for name, experiment in self._experiments.items()
            if name not in self._killing
        ]
        used = self._used_capacity()
        progress = True
        while progress:
            progress = False
            hosts = sorted(
                (
                    host
                    for name, host in self._hosts.items()
                    if host.up() and self._host_mode[name] == HostMode.ACTIVE
                ),
                key=lambda host: host.max_capacity() - used[host.name()],
                reverse=True,
            )
            for host in hosts:
                free = host.max_capacity() - used[host.name()]
                job = max(
                    (
                        candidate
                        for candidate in (
                            experiment.candidate(
                                free,
                                lambda job, host=host: self._compatible(
                                    job, host
                                ),
                            )
                            for experiment in experiments
                        )
                        if candidate is not None
                    ),
                    key=lambda job: job.demand(),
                    default=None,
                )
                if job is None:
                    continue
                progress = True
                self._launch(host, job)
                if job.status().active():
                    used[host.name()] += job.demand()
                if not host.up():
                    break

    def _job_view(self, job: Job) -> dict:
        view = job.view()
        if job.status() == JobStatus.QUEUED and not any(
            self._compatible(job, host) for host in self._hosts.values()
        ):
            view["message"] = "no compatible host"
        return view

    def _compatible(self, job: Job, host: Host) -> bool:
        """`job.compatible_with_host(host)`; a raising check means no."""
        try:
            return bool(job.compatible_with_host(host))
        except Exception as e:
            if job.id() not in self._bad_compatibility:
                self._bad_compatibility.add(job.id())
                self._events.error(
                    f"compatible_with_host of job {job.id()[:8]} raised "
                    f"{type(e).__name__}: {e}; treating hosts as incompatible."
                )
            return False

    def _launch(self, host: Host, job: Job) -> None:
        job.assign(host.name())
        self._dirty = True
        # NOTE: Write-ahead: if we die during the launch, the next start
        # knows to ask this host about the job instead of launching it again.
        self._persist(force=True)
        try:
            host.launch(job)
        except JobError as e:
            job.set_status(JobStatus.FAILED, message=f"launch failed: {e}")
            self._events.error(
                f"Launching job {job.id()[:8]} on {host.name()} failed: {e}"
            )
        except HostUnreachable as e:
            job.set_status(JobStatus.UNKNOWN)
            self._host_lost(host, e)
        else:
            job.set_status(JobStatus.RUNNING)
            self._events.info(
                f"Launched job {job.id()[:8]} ({job.shorthand_command()}) "
                f"on {host.name()}."
            )

    def _publish(self) -> None:
        used = self._used_capacity()
        hosts = tuple(
            {
                "name": name,
                "isa": host.isa(),
                "domain": host.domain(),
                "state": str(host.state()),
                "mode": str(self._host_mode[name]),
                "capacity": host.max_capacity(),
                "used": used[name],
                "jobs": len(self._active_jobs(name)),
                "error": host.last_error(),
            }
            for name, host in self._hosts.items()
        )
        experiments = []
        for name, experiment in self._experiments.items():
            counts: dict[str, int] = {}
            for job in experiment.jobs():
                counts[job.status().value] = (
                    counts.get(job.status().value, 0) + 1
                )
            experiments.append(
                {
                    "name": name,
                    "state": "killing" if name in self._killing else "active",
                    "outdir": str(experiment.outdir()),
                    "jobs": len(experiment.jobs()),
                    "counts": counts,
                }
            )
        snapshot = Snapshot(
            taken_at=time.time(),
            info=dict(
                self._info,
                scripts=sorted(self._scripts),
                script_jobs={
                    path: sum(
                        1 for origin in self._job_origin.values()
                        if origin == path
                    )
                    for path in self._scripts
                },
                deleted_jobs=sorted(self._deleted_jobs),
                deleted_experiments=sorted(self._deleted_experiments),
            ),
            hosts=hosts,
            experiments=tuple(experiments),
            jobs=tuple(self._job_view(job) for job in self._jobs.values()),
            job_locations={
                job_id: (job.outdir(), job.host())
                for job_id, job in self._jobs.items()
            },
            host_objects=dict(self._hosts),
        )
        with self._snapshot_lock:
            self._snapshot = snapshot

    # ---- Persistence -----------------------------------------------------

    def _state_data(self) -> dict:
        return {
            "name": self._name,
            "scripts": [
                {"path": path, "sha256": sha}
                for path, sha in self._scripts.items()
            ],
            "hosts": {
                name: {
                    "spec": host.spec(),
                    "mode": self._host_mode[name].value,
                }
                for name, host in self._hosts.items()
            },
            "killing": sorted(self._killing),
            "deleted_jobs": sorted(self._deleted_jobs),
            "deleted_experiments": sorted(self._deleted_experiments),
            "jobs": {
                job_id: job.runtime_state()
                for job_id, job in self._jobs.items()
                if job.status() != JobStatus.QUEUED
            },
        }

    def _persist(self, force: bool = False) -> None:
        if self._store is None or not (self._dirty or force):
            return
        if self._store.save(self._state_data(), force=force):
            self._dirty = False

    def resume(self, data: dict) -> None:
        """Rebuild from a saved state. Call before `run`.

        Jobs keep their saved status: only queued jobs are ever launched, so
        jobs that were running are checked with their host, never restarted.
        """
        # NOTE: Before the scripts run, so they skip what was deleted.
        self._deleted_jobs = set(data.get("deleted_jobs", []))
        self._deleted_experiments = set(data.get("deleted_experiments", []))
        for script in data.get("scripts", []):
            path = Path(script["path"])
            try:
                if path.exists() and _sha256(path) != script["sha256"]:
                    self._events.warn(f"{path} changed since it was loaded.")
                for line in self._load(path, merge=True):
                    self._logger.info(line)
            except Exception as e:
                self._events.error(f"Could not reload {path}: {e}")

        for name, saved in data.get("hosts", {}).items():
            spec = saved["spec"]
            if name not in self._hosts:
                try:
                    host = Host.from_spec(spec)
                except (KeyError, ValueError) as e:
                    # NOTE: e.g. a host saved before hosts had an isa.
                    self._events.warn(
                        f"Not restoring host {name} from saved state ({e}); "
                        "load a script that defines it."
                    )
                    continue
                self._add_host(host)
            self._hosts[name].set_capacity(spec["max_capacity"])
            self._host_mode[name] = HostMode(saved["mode"])

        for name in data.get("killing", []):
            if name in self._experiments:
                self._killing.add(name)

        orphans = []
        for job_id, saved in data.get("jobs", {}).items():
            job = self._jobs.get(job_id)
            if job is None:
                if JobStatus(saved["status"]).active():
                    orphans.append((job_id, saved))
                continue
            job.restore_runtime_state(saved)
        for job_id, saved in orphans:
            self._events.warn(
                f"Job {job_id[:8]} ({saved['status']} on {saved['host']}, "
                f"outdir {saved['outdir']}) is no longer defined by any "
                "script; it is not tracked anymore."
            )
        self._dirty = True
        self._publish()
        self._events.info(
            f"Resumed {len(self._experiments)} experiment(s) and "
            f"{len(self._hosts)} host(s) from saved state."
        )

    # ---- Commands (run on the scheduler thread) ---------------------------

    def _add_host(self, host: Host) -> None:
        self._hosts[host.name()] = host
        self._host_mode[host.name()] = HostMode.ACTIVE
        self._dirty = True

    def _load(self, script: Path, merge: bool) -> list[str]:
        script = Path(script).expanduser().resolve()
        if not script.is_file() or script.suffix != ".py":
            raise CommandError(f"{script} is not a Python file")
        hosts, experiments = _load_objects(script)
        lines = []

        for host in hosts:
            if host.name() in self._hosts:
                if not merge:
                    lines.append(f"host {host.name()} already added; skipped")
                continue
            self._add_host(host)
            lines.append(f"added host {host.name()}")

        origin = str(script)
        skipped_deleted = 0
        for experiment in experiments:
            name = experiment.name()
            if name in self._killing:
                lines.append(f"experiment {name} is being killed; skipped")
                continue
            if name in self._deleted_experiments:
                lines.append(
                    f"experiment {name} was deleted; skipped (`undelete "
                    f"--experiment {name}` to bring it back)"
                )
                continue
            existing = self._experiments.get(name)
            if existing is not None and not merge:
                lines.append(
                    f"experiment {name} already loaded; use `reload` to add "
                    "new jobs to it"
                )
                continue
            if existing is None:
                existing = Experiment(name, experiment.outdir())
                self._experiments[name] = existing
                lines.append(f"added experiment {name}")
            added = 0
            for job in experiment.jobs():
                if job.id() in self._deleted_jobs:
                    skipped_deleted += 1
                    continue
                owner = self._jobs.get(job.id())
                if owner is not None:
                    if owner.experiment() != name:
                        lines.append(
                            f"job {job.id()[:8]} in {name} duplicates one in "
                            f"{owner.experiment()}; skipped"
                        )
                    continue
                existing.register_job(job)
                self._jobs[job.id()] = job
                self._job_origin[job.id()] = origin
                added += 1
            defined = {job.id() for job in experiment.jobs()}
            stale = [
                job
                for job in existing.jobs()
                if job.id() not in defined
                and self._job_origin.get(job.id()) == origin
            ]
            lines.append(f"{name}: {added} new job(s)")
            if merge and stale:
                lines.append(
                    f"{name}: {len(stale)} job(s) are no longer in the script "
                    "(kept)"
                )
        if skipped_deleted:
            lines.append(f"{skipped_deleted} deleted job(s) skipped")

        self._scripts[origin] = _sha256(script)
        self._dirty = True
        return lines

    def _cmd_load(self, script: str) -> list[str]:
        if str(Path(script).expanduser().resolve()) in self._scripts:
            raise CommandError(
                f"{script} is already loaded; use `reload` to pick up changes"
            )
        return self._load(Path(script), merge=False)

    def _cmd_reload(self, script: str) -> list[str]:
        return self._load(Path(script), merge=True)

    def _experiment(self, name: str) -> Experiment:
        if name not in self._experiments:
            raise CommandError(f"no experiment named {name!r}")
        return self._experiments[name]

    def _host(self, name: str) -> Host:
        if name not in self._hosts:
            raise CommandError(f"no host named {name!r}")
        return self._hosts[name]

    def _resolve_job(self, prefix: str) -> Job:
        matches = [
            job for job_id, job in self._jobs.items()
            if job_id.startswith(prefix)
        ]
        if not matches:
            raise CommandError(f"no job id starts with {prefix!r}")
        if len(matches) > 1:
            raise CommandError(
                f"{prefix!r} matches {len(matches)} jobs; use a longer prefix"
            )
        return matches[0]

    def _cmd_kill(self, experiment: str) -> list[str]:
        self._experiment(experiment)
        self._killing.add(experiment)
        return [f"killing experiment {experiment}"]

    def _cmd_drain(self, host: str) -> list[str]:
        self._host(host)
        self._host_mode[host] = HostMode.DRAINING
        return [f"host {host} won't get new jobs"]

    def _cmd_undrain(self, host: str) -> list[str]:
        self._host(host)
        self._host_mode[host] = HostMode.ACTIVE
        return [f"host {host} is accepting jobs again"]

    def _cmd_remove(self, host: str, force: bool = False) -> list[str]:
        self._host(host)
        active = self._active_jobs(host)
        if force:
            self._drop_host(host)
            return [
                f"removed host {host}"
                + (
                    f"; {len(active)} job(s) on it are untracked, "
                    "`reset --force` them to run them elsewhere"
                    if active
                    else ""
                )
            ]
        self._host_mode[host] = HostMode.REMOVING
        return [
            f"host {host} will be removed once its {len(active)} running "
            "job(s) finish"
        ]

    def _cmd_capacity(self, host: str, change: str) -> list[str]:
        target = self._host(host)
        try:
            if change.startswith(("+", "-")):
                value = target.max_capacity() + int(change)
            else:
                value = int(change.lstrip("="))
        except ValueError:
            raise CommandError(f"capacity must be +N, -N or =N, not {change!r}")
        target.set_capacity(value)
        return [f"host {host} capacity is now {target.max_capacity()}"]

    def _cmd_signal(self, jobs: list[str], signal: str) -> list[str]:
        if signal not in self.SIGNALS:
            raise CommandError(
                f"signal must be one of {', '.join(self.SIGNALS)}"
            )
        lines = []
        for prefix in jobs:
            job = self._resolve_job(prefix)
            host = self._hosts.get(job.host() or "")
            if not job.status().active() or host is None:
                lines.append(f"{job.id()[:8]}: not running")
                continue
            if not host.up():
                lines.append(f"{job.id()[:8]}: host {host.name()} is down")
                continue
            try:
                sent = host.signal(job, self.SIGNALS[signal])
            except HostUnreachable as e:
                self._host_lost(host, e)
                lines.append(f"{job.id()[:8]}: {e}")
                continue
            except JobError as e:
                lines.append(f"{job.id()[:8]}: {e}")
                continue
            lines.append(
                f"{job.id()[:8]}: sent {signal}"
                if sent
                else f"{job.id()[:8]}: was not running"
            )
        return lines

    def _cmd_reset(
        self,
        jobs: list[str] = (),
        experiment: str | None = None,
        status: str | None = None,
        force: bool = False,
    ) -> list[str]:
        targets: list[Job] = [self._resolve_job(prefix) for prefix in jobs]
        if experiment is not None:
            wanted = JobStatus(status) if status else None
            targets += [
                job
                for job in self._experiment(experiment).jobs()
                if (wanted is None and job.status().terminal())
                or job.status() == wanted
            ]
        if not targets:
            raise CommandError("no jobs to reset")
        lines, count = [], 0
        for job in targets:
            if job.status() == JobStatus.UNKNOWN and force:
                job.set_status(JobStatus.KILLED)
            if job.reset():
                count += 1
            else:
                lines.append(
                    f"{job.id()[:8]} is {job.status()}; "
                    + (
                        "use --force to requeue it anyway"
                        if job.status() == JobStatus.UNKNOWN
                        else "kill it first"
                    )
                )
        lines.append(f"requeued {count} job(s)")
        return lines

    def _cmd_stop(self, kill_jobs: bool = False) -> list[str]:
        lines = []
        if kill_jobs:
            killed = 0
            for job in list(self._jobs.values()):
                host = self._hosts.get(job.host() or "")
                if not job.status().active() or host is None or not host.up():
                    continue
                try:
                    killed += host.signal(job, SIGNAL.KILL.value)
                except (HostUnreachable, JobError) as e:
                    lines.append(f"{job.id()[:8]}: {e}")
            lines.append(f"sent SIGKILL to {killed} job(s)")
        else:
            running = sum(
                1 for job in self._jobs.values() if job.status().active()
            )
            lines.append(
                f"{running} job(s) keep running; start the scheduler again "
                "to pick them back up"
            )
        self._stop.set()
        return lines

    def _refuse_if_active(self, jobs: Iterable[Job], what: str) -> None:
        active = [job for job in jobs if job.status().active()]
        if active:
            ids = ", ".join(job.id()[:8] for job in active[:10])
            more = f" and {len(active) - 10} more" if len(active) > 10 else ""
            raise CommandError(
                f"can't delete {what}: {len(active)} job(s) are still "
                f"running ({ids}{more}); kill them first"
            )

    def _cmd_delete(
        self,
        jobs: list[str] = (),
        experiment: str | None = None,
        script: str | None = None,
    ) -> list[str]:
        """Forget jobs, an experiment, or a script's jobs. Output files on
        disk are never touched."""
        if script is not None:
            path = str(Path(script).expanduser().resolve())
            if path not in self._scripts:
                raise CommandError(f"{script} is not a loaded script")
            owned = [
                job
                for job_id, job in self._jobs.items()
                if self._job_origin.get(job_id) == path
            ]
            self._refuse_if_active(owned, f"script {script}")
            touched = {job.experiment() for job in owned}
            for job in owned:
                self._forget_job(job)
            emptied = [
                name
                for name in sorted(touched)
                if name in self._experiments
                and not self._experiments[name].jobs()
            ]
            for name in emptied:
                self._remove_experiment(name)
            del self._scripts[path]
            self._dirty = True
            return [
                f"deleted script {script}: {len(owned)} job(s)"
                + (f", experiment(s) {', '.join(emptied)}" if emptied else "")
                + "; it won't be loaded again unless you `load` it"
            ]

        if experiment is not None:
            target = self._experiment(experiment)
            self._refuse_if_active(target.jobs(), f"experiment {experiment}")
            count = len(target.jobs())
            self._remove_experiment(experiment)
            self._deleted_experiments.add(experiment)
            return [f"deleted experiment {experiment} and its {count} job(s)"]

        if not jobs:
            raise CommandError("nothing to delete")
        lines, deleted = [], 0
        for prefix in jobs:
            job = self._resolve_job(prefix)
            if job.status().active():
                lines.append(f"{job.id()[:8]}: skipped, it is {job.status()}")
                continue
            self._forget_job(job)
            self._deleted_jobs.add(job.id())
            deleted += 1
        lines.append(f"deleted {deleted} job(s)")
        return lines

    def _cmd_undelete(
        self, jobs: list[str] = (), experiment: str | None = None
    ) -> list[str]:
        """Stop skipping deleted jobs or an experiment, and reload every
        loaded script so they come back."""
        restored = 0
        if experiment is not None:
            if experiment not in self._deleted_experiments:
                raise CommandError(f"experiment {experiment} isn't deleted")
            self._deleted_experiments.discard(experiment)
            restored += 1
        for prefix in jobs:
            matches = [i for i in self._deleted_jobs if i.startswith(prefix)]
            if len(matches) != 1:
                raise CommandError(
                    f"{prefix!r} matches {len(matches)} deleted jobs"
                )
            self._deleted_jobs.discard(matches[0])
            restored += 1
        if not restored:
            raise CommandError("nothing to undelete")
        lines = []
        for path in list(self._scripts):
            try:
                lines += self._load(Path(path), merge=True)
            except Exception as e:
                lines.append(f"reloading {path} failed: {e}")
        return lines
