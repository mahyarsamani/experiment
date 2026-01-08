from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Tuple, Union


NO_HOST = "TBD"


class JobStatus(Enum):
    NONE = "none"
    PENDING = "pending"
    RUNNING = "running"
    EXITED = "exited"
    KILLED = "killed"
    FAILED = "failed"

    def color(self) -> str:
        return {
            "none": "#FAFAFA",
            "pending": "#F59E0B",
            "running": "#10B981",
            "exited": "#6B7280",
            "killed": "#090A0D",
            "failed": "#EF4444",
        }[self.value]

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class LinkView:
    label: str
    href: str


@dataclass(frozen=True)
class JobView:
    # NOTE: Meta-Data, not viewable
    id: str
    pid: int
    # NOTE: Viewable
    experiment: str
    command: str
    links: Tuple[LinkView, ...]
    host: str
    status: str
    status_color: str


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
        aux_file_io: List[Tuple[str, Path]] = [],
        optional_dump: List[Tuple[str, str, Path]] = [],
    ) -> None:
        # NOTE: This attribute is useful for hosts to cancel
        # all jobs from a certain experiment.
        self._experiment = experiment
        # NOTE: Relating to launching a process.
        # These attributes are useful for Worker from worker.py
        self._cwd = cwd
        self._command = command
        self._shorthand_command = shorthand_command
        self._outdir = outdir
        self._stdout = self._outdir / "stdout"
        self._stderr = self._outdir / "stderr"
        self._pid = -1
        self._status = JobStatus.NONE

        # NOTE: Scheduling and managing a job
        self._demand = demand

        # NOTE: Tracking and printing job status
        self._id = id
        self._optional_dump = optional_dump
        self._aux_file_io = aux_file_io

        # NOTE: Will be set at launch
        self._host_name = NO_HOST
        self._links = list()

    def experiment(self) -> str:
        return self._experiment

    def cwd(self) -> Path:
        return self._cwd

    def command(self) -> str:
        return self._command

    def shorthand_command(self) -> str:
        return self._shorthand_command

    def outdir(self):
        return self._outdir

    def id(self) -> str:
        return self._id

    def stdout(self) -> Path:
        return self._stdout

    def stderr(self) -> Path:
        return self._stderr

    def demand(self) -> int:
        return self._demand

    def set_status(self, status: Union[JobStatus, str]) -> None:
        self._status = JobStatus(status)

    def status(self) -> str:
        return self._status

    def running(self) -> bool:
        return (
            self._status == JobStatus.RUNNING
            or self._status == JobStatus.PENDING
        )

    def set_pid(self, pid: int) -> None:
        self._pid = pid

    def pid(self) -> int:
        return self._pid

    def clear(self) -> bool:
        if self.running():
            return False
        self._status = JobStatus.NONE
        self._pid = -1
        self._host_name = NO_HOST
        self._links = list()
        return True

    def file_io(self) -> List[Tuple[str, Path]]:
        return (
            [
                ("stdout", self._stdout),
                ("stderr", self._stderr),
            ]
            + self._aux_file_io
            + [(name, path) for name, _, path in self._optional_dump]
        )

    def aux_file_io(self) -> List[Tuple[str, Path]]:
        return self._aux_file_io

    def optional_dump(self) -> List[Tuple[str, Path]]:
        return self._optional_dump

    def set_links(self, host_name: str, links: List[Tuple[str, str]]):
        self._host_name = host_name
        self._links = links

    def view(self):
        return JobView(
            id=self._id,
            pid=self._pid,
            experiment=self._experiment,
            command=self._shorthand_command,
            links=tuple(
                LinkView(label=label, href=href) for label, href in self._links
            ),
            host=self._host_name,
            status=str(self._status),
            status_color=self._status.color(),
        )

    def __str__(self):
        return f"{self.__class__.__name__}(cwd={self._cwd}, command={self._command}, outdir={self._outdir}, status={self._status}, pid={self._pid})"

    def __repr__(self):
        return self.__str__()


class Experiment:
    def __init__(self, name: str, outdir: Path) -> None:
        self._name = name
        self._outdir = outdir

        self._jobs = list()

        self._safe_to_remove = False

    def name(self) -> str:
        return self._name

    def outdir(self) -> Path:
        return self._outdir

    def register_job(self, job: Job) -> None:
        self._jobs.append(job)

    def jobs(self) -> List[Job]:
        self._jobs.sort(key=lambda j: j.demand(), reverse=True)
        return self._jobs

    def set_safe_to_remove(self, safe_to_remove: bool):
        self._safe_to_remove = safe_to_remove

    def safe_to_remove(self) -> bool:
        return self._safe_to_remove

    def candidate(self, capacity: int):
        candidates = sorted(
            (
                job
                for job in self._jobs
                if job.status() == JobStatus.NONE and job.demand() <= capacity
            ),
            reverse=True,
            key=lambda j: j.demand(),
        )

        return candidates[0] if len(candidates) > 0 else None

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(name={self._name}, outdir={self._outdir}, jobs: {len(self._jobs)})"

    def __repr__(self):
        return self.__str__()


class ProjectConfiguration:

    def name(self):
        raise NotImplementedError

    def base_dir(self):
        raise NotImplementedError

    def get_experiment_dir(self, experiment: Experiment) -> Path:
        raise NotImplementedError
