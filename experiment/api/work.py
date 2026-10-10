from __future__ import annotations

import json

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Sequence, Tuple

if TYPE_CHECKING:
    from .host import Host


class JobStatus(Enum):
    QUEUED = "queued"
    LAUNCHING = "launching"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    KILLED = "killed"
    # NOTE: The job was launched but its host can't be reached, so we don't
    # know. It is never relaunched automatically.
    UNKNOWN = "unknown"

    def color(self) -> str:
        return {
            JobStatus.QUEUED: "#FAFAFA",
            JobStatus.LAUNCHING: "#F59E0B",
            JobStatus.RUNNING: "#10B981",
            JobStatus.EXITED: "#6B7280",
            JobStatus.FAILED: "#EF4444",
            JobStatus.KILLED: "#090A0D",
            JobStatus.UNKNOWN: "#A855F7",
        }[self]

    def active(self) -> bool:
        """The job may be running on its host and holds host capacity."""
        return self in (
            JobStatus.LAUNCHING,
            JobStatus.RUNNING,
            JobStatus.UNKNOWN,
        )

    def terminal(self) -> bool:
        return self in (JobStatus.EXITED, JobStatus.FAILED, JobStatus.KILLED)

    def __str__(self) -> str:
        return self.value


class Job:
    def __init__(
        self,
        experiment: str,
        cwd: Path,
        command: str,
        shorthand_command: str,
        outdir: Path,
        demand: int,
        id: str,
        aux_files: Sequence[Tuple[str, Path]] = (),
        dumps: Sequence[Tuple[str, str, Path]] = (),
    ) -> None:
        """
        :param aux_files: (label, path) of extra output files to show on the
            dashboard, e.g. ("stats", outdir / "stats.txt"). Paths must be
            inside `outdir`.
        :param dumps: (label, content, path) of files the worker writes
            before launching the job. Paths must be inside `outdir`.
        """
        self._experiment = experiment
        self._cwd = Path(cwd)
        self._command = command
        self._shorthand_command = shorthand_command
        self._outdir = Path(outdir)
        self._demand = demand
        self._id = id
        self._aux_files = tuple(aux_files)
        self._dumps = tuple(dumps)

        # NOTE: Runtime state, owned by the scheduler.
        self._status = JobStatus.QUEUED
        self._host_name: str | None = None
        self._returncode: int | None = None
        self._message: str | None = None
        # NOTE: Epoch seconds on the worker's clock, from the job's records.
        self._start_time: float | None = None
        self._end_time: float | None = None
        # NOTE: Requeues asked for by the watcher since the last `reset`.
        self._retries = 0

    # NOTE: Watcher settings; override in a subclass along with `watcher`.
    watch_interval = 60
    watch_timeout = 60
    max_retries = 3

    def compatible_with_host(self, host: "Host") -> bool:
        """Whether this job may run on `host`. Override to restrict it,
        e.g. `return host.isa() == "aarch64"`."""
        return True

    def watcher(self) -> str | None:
        """A script that checks on this job, or None for no checks.

        The worker's runner runs it every `watch_interval` seconds while the
        job runs (killing it after `watch_timeout` seconds), and once after
        the job ends. It runs with bash, or with its own interpreter if it
        starts with `#!`, in the job's outdir, with JOB_OUTDIR, JOB_ID,
        JOB_PID, JOB_ELAPSED, JOB_RUNNING (1 or 0) and, after the job ends,
        JOB_EXIT_CODE set. The first line it prints decides:

            nothing or "ok"     keep going
            "fail <message>"    kill the job; it ends as failed
            "retry <message>"   kill the job and queue it again, at most
                                `max_retries` times, then it fails

        Its stderr and every verdict go to .job/watcher.log.
        """
        pass

    def experiment(self) -> str:
        return self._experiment

    def cwd(self) -> Path:
        return self._cwd

    def command(self) -> str:
        return self._command

    def shorthand_command(self) -> str:
        return self._shorthand_command

    def outdir(self) -> Path:
        return self._outdir

    def id(self) -> str:
        return self._id

    def demand(self) -> int:
        return self._demand

    def files(self) -> Tuple[Tuple[str, Path], ...]:
        job_dir = self._outdir / ".job"
        watcher = (
            (
                ("watcher", job_dir / "watcher"),
                ("watcher.log", job_dir / "watcher.log"),
            )
            if self.watcher() is not None
            else ()
        )
        return (
            ("stdout", self._outdir / "stdout"),
            ("stderr", self._outdir / "stderr"),
            *self._aux_files,
            *((label, path) for label, _, path in self._dumps),
            *watcher,
        )

    def dumps(self) -> Tuple[Tuple[str, str, Path], ...]:
        script = self.watcher()
        if script is None:
            return self._dumps
        job_dir = self._outdir / ".job"
        config = json.dumps(
            {"interval": self.watch_interval, "timeout": self.watch_timeout}
        )
        return (
            *self._dumps,
            ("watcher", script, job_dir / "watcher"),
            ("watcher config", config, job_dir / "watcher.json"),
        )

    def status(self) -> JobStatus:
        return self._status

    def host(self) -> str | None:
        return self._host_name

    def returncode(self) -> int | None:
        return self._returncode

    def message(self) -> str | None:
        return self._message

    def start_time(self) -> float | None:
        return self._start_time

    def end_time(self) -> float | None:
        return self._end_time

    def set_status(
        self,
        status: JobStatus,
        returncode: int | None = None,
        message: str | None = None,
    ) -> None:
        self._status = status
        if returncode is not None:
            self._returncode = returncode
        if message is not None:
            self._message = message

    def set_times(self, start: float | None, end: float | None) -> None:
        if start is not None:
            self._start_time = start
        if end is not None:
            self._end_time = end

    def _clear_run(self) -> None:
        self._returncode = None
        self._message = None
        self._start_time = None
        self._end_time = None

    def assign(self, host_name: str) -> None:
        self._host_name = host_name
        self._status = JobStatus.LAUNCHING
        self._clear_run()

    def reset(self) -> bool:
        if self._status.active():
            return False
        self._status = JobStatus.QUEUED
        self._host_name = None
        self._clear_run()
        self._retries = 0
        return True

    def retries(self) -> int:
        return self._retries

    def retry(self, message: str) -> bool:
        """Queue the job again for its watcher, unless it has used up
        `max_retries`. Returns whether it was queued."""
        if self._retries >= self.max_retries:
            return False
        self._retries += 1
        self._status = JobStatus.QUEUED
        self._host_name = None
        self._clear_run()
        self._message = f"retry {self._retries}/{self.max_retries}: {message}"
        return True

    def forget(self) -> None:
        """Requeue a job whose host has no record of it."""
        self._status = JobStatus.QUEUED
        self._host_name = None

    def runtime_state(self) -> dict:
        return {
            "experiment": self._experiment,
            "outdir": str(self._outdir),
            "status": self._status.value,
            "host": self._host_name,
            "returncode": self._returncode,
            "message": self._message,
            "start_time": self._start_time,
            "end_time": self._end_time,
            "retries": self._retries,
        }

    def restore_runtime_state(self, state: dict) -> None:
        status = JobStatus(state["status"])
        # NOTE: A launch that was in flight when the scheduler died may or may
        # not have happened; treat it like any job we have to ask about.
        if status == JobStatus.LAUNCHING:
            status = JobStatus.UNKNOWN
        self._status = status
        self._host_name = state.get("host")
        self._returncode = state.get("returncode")
        self._message = state.get("message")
        self._start_time = state.get("start_time")
        self._end_time = state.get("end_time")
        self._retries = state.get("retries", 0)

    def view(self) -> dict:
        return {
            "id": self._id,
            "experiment": self._experiment,
            "command": self._shorthand_command,
            "full_command": self._command,
            "demand": self._demand,
            "host": self._host_name or "",
            "status": self._status.value,
            "status_color": self._status.color(),
            "returncode": self._returncode,
            "message": self._message or "",
            "start_time": self._start_time,
            "end_time": self._end_time,
            "retries": self._retries,
            "files": [label for label, _ in self.files()],
            "outdir": str(self._outdir),
        }

    def __str__(self):
        return (
            f"{self.__class__.__name__}(id={self._id[:8]}, "
            f"command={self._shorthand_command}, status={self._status})"
        )

    def __repr__(self):
        return self.__str__()


class Experiment:
    def __init__(self, name: str, outdir: Path) -> None:
        self._name = name
        self._outdir = Path(outdir)
        self._jobs: dict[str, Job] = dict()

    def name(self) -> str:
        return self._name

    def outdir(self) -> Path:
        return self._outdir

    def register_job(self, job: Job) -> bool:
        """Returns False (and ignores the job) if its id is already here."""
        if job.id() in self._jobs:
            return False
        self._jobs[job.id()] = job
        return True

    def jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def remove_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def candidate(
        self,
        capacity: int,
        accepts: Callable[[Job], bool] = lambda job: True,
    ) -> Job | None:
        """The largest queued job that fits in `capacity` and `accepts`."""
        return max(
            (
                job
                for job in self._jobs.values()
                if job.status() == JobStatus.QUEUED
                and job.demand() <= capacity
                and accepts(job)
            ),
            key=lambda j: j.demand(),
            default=None,
        )

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__}(name={self._name}, "
            f"outdir={self._outdir}, jobs={len(self._jobs)})"
        )

    def __repr__(self):
        return self.__str__()


def find_jobs(experiments: Iterable[Experiment]) -> Iterable[Job]:
    for experiment in experiments:
        yield from experiment.jobs()


class ProjectConfiguration:

    def name(self):
        raise NotImplementedError

    def base_dir(self):
        raise NotImplementedError

    def get_experiment_dir(self, experiment: Experiment) -> Path:
        raise NotImplementedError
