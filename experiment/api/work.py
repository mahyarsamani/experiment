from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence, Tuple


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
        return (
            ("stdout", self._outdir / "stdout"),
            ("stderr", self._outdir / "stderr"),
            *self._aux_files,
            *((label, path) for label, _, path in self._dumps),
        )

    def dumps(self) -> Tuple[Tuple[str, str, Path], ...]:
        return self._dumps

    def status(self) -> JobStatus:
        return self._status

    def host(self) -> str | None:
        return self._host_name

    def returncode(self) -> int | None:
        return self._returncode

    def message(self) -> str | None:
        return self._message

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

    def assign(self, host_name: str) -> None:
        self._host_name = host_name
        self._status = JobStatus.LAUNCHING
        self._returncode = None
        self._message = None

    def reset(self) -> bool:
        if self._status.active():
            return False
        self._status = JobStatus.QUEUED
        self._host_name = None
        self._returncode = None
        self._message = None
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

    def job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def candidate(self, capacity: int) -> Job | None:
        return max(
            (
                job
                for job in self._jobs.values()
                if job.status() == JobStatus.QUEUED
                and job.demand() <= capacity
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
