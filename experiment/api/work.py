import psutil

from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SLEEP = "sleep"
    WAITING = "waiting"
    STOPPED = "stopped"
    ZOMBIE = "zombie"
    EXITED = "exited"
    KILLED = "killed"
    FAILED = "failed"

    def running(self) -> bool:
        return self in [
            JobStatus.RUNNING,
            JobStatus.SLEEP,
            JobStatus.WAITING,
            JobStatus.STOPPED,
            JobStatus.ZOMBIE,
        ]

    def color(self) -> str:
        return {
            JobStatus.PENDING: "#999999",
            JobStatus.RUNNING: "#ff3fff",
            JobStatus.SLEEP: "#7fcfff",
            JobStatus.WAITING: "#ff7f00",
            JobStatus.STOPPED: "#ff0000",
            JobStatus.ZOMBIE: "#5a7f3f",
            JobStatus.EXITED: "#00ff00",
            JobStatus.KILLED: "#af00ff",
            JobStatus.FAILED: "#7f3f00",
        }[self]

    @classmethod
    def from_string(cls, status: str) -> "JobStatus":
        try:
            return {
                "pending": cls.PENDING,
                "running": cls.RUNNING,
                "sleep": cls.SLEEP,
                "waiting": cls.WAITING,
                "stopped": cls.STOPPED,
                "zombie": cls.ZOMBIE,
                "exited": cls.EXITED,
                "killed": cls.KILLED,
                "failed": cls.FAILED,
            }[status]
        except KeyError:
            raise RuntimeError(f"Unknown job status {status}.")

    @classmethod
    def psutil_to_string(cls, status: str) -> str:
        try:
            return {
                psutil.STATUS_RUNNING: "running",
                psutil.STATUS_SLEEPING: "sleep",
                psutil.STATUS_DISK_SLEEP: "waiting",
                psutil.STATUS_STOPPED: "stopped",
                psutil.STATUS_TRACING_STOP: "stopped",
                psutil.STATUS_ZOMBIE: "zombie",
                psutil.STATUS_DEAD: "exited",
                psutil.STATUS_WAKING: "running",
                psutil.STATUS_IDLE: "sleep",
                psutil.STATUS_LOCKED: "waiting",
                psutil.STATUS_WAITING: "waiting",
                psutil.STATUS_PARKED: "waiting",
            }[status]

        except KeyError:
            raise RuntimeError(
                f"Missing translator for psutil status {status}."
            )


class Job:
    def __init__(
        self,
        experiment: "Experiment",
        command: str,
        outdir: Path,
        demand: int,
        id: str,
        aux_file_io: List[Tuple[str, Path]] = [],
    ) -> None:
        # NOTE: This attribute is useful for hosts to cancel
        # all jobs from a certain experiment.
        self._experiment = experiment
        # NOTE: Relating to launching a process.
        # These attributes are useful for Worker from worker.py
        self._command = command
        self._outdir = outdir
        self._stdout = self._outdir / "stdout"
        self._stderr = self._outdir / "stderr"
        self._pid = -1
        self._status = JobStatus.PENDING

        # NOTE: Scheduling and managing a job
        self._demand = demand

        # NOTE: Tracking and printing job status
        self._id = id
        self._aux_file_io = aux_file_io

    def experiment(self) -> "Experiment":
        return self._experiment

    def command(self) -> str:
        return self._command

    def outdir(self):
        return self._outdir

    def stdout(self) -> Path:
        return self._stdout

    def stderr(self) -> Path:
        return self._stderr

    def demand(self) -> int:
        return self._demand

    def id(self) -> str:
        return self._id

    def set_status(self, status: JobStatus) -> None:
        self._status = status

    def status(self) -> JobStatus:
        return self._status

    def set_pid(self, pid: int) -> None:
        self._pid = pid

    def pid(self) -> int:
        return self._pid

    def file_io(self) -> List[Tuple[str, Path]]:
        return [
            ("stdout", self._stdout),
            ("stderr", self._stderr),
        ] + self._aux_file_io

    def aux_file_io(self) -> List[Tuple[str, Path]]:
        return self._aux_file_io

    def id_dict(self) -> Dict:
        raise NotImplementedError

    def serialize(self) -> Dict:
        return {
            "command": self._command,
            "outdir": self._outdir.as_posix(),
            "demand": self._demand,
            "id": self._id,
            "aux_file_io": [
                (name, path.as_posix()) for name, path in self._aux_file_io
            ],
        }

    @classmethod
    def deserialize(
        cls, experiment: "Experiment", serialized_job: Dict
    ) -> "Job":
        command = serialized_job["command"]
        outdir = Path(serialized_job["outdir"])
        demand = serialized_job["demand"]
        id = serialized_job["id"]
        aux_file_io = [
            (name, Path(path)) for name, path in serialized_job["aux_file_io"]
        ]

        job = cls(experiment, command, outdir, demand, id, aux_file_io)

        return job

    def __str__(self) -> str:
        return f"{__class__}(command={self._command}, outdir={self._outdir}, status={self._status}, pid={self._pid})"


class Experiment:
    def __init__(self, name: str, outdir: Path) -> None:
        self._name = name
        self._outdir = outdir

        self._jobs = list()
        self._next_job_index = 0

    def name(self) -> str:
        return self._name

    def outdir(self) -> Path:
        return self._outdir

    def register_job(self, job: Job) -> None:
        self._jobs.append(job)

    def jobs(self) -> List[Job]:
        self._jobs.sort(key=lambda j: j.demand(), reverse=True)
        return self._jobs

    def serialize(self) -> Dict:
        return {
            "name": self._name,
            "outdir": self._outdir.as_posix(),
            "jobs": [job.serialize() for job in self._jobs],
        }

    @classmethod
    def deserialize(cls, serialized_experiment: Dict) -> "Experiment":
        name = serialized_experiment["name"]
        outdir = Path(serialized_experiment["outdir"])
        experiment = cls(name, outdir)

        for job_dict in serialized_experiment["jobs"]:
            job = Job.deserialize(experiment, job_dict)
            experiment.register_job(job)

        return experiment

    def __str__(self) -> str:
        return f"{__class__}(name={self._name}, outdir={self._outdir})"


class ProjectConfiguration:

    def name(self):
        raise NotImplementedError

    def base_dir(self):
        raise NotImplementedError

    def get_experiment_dir(self, experiment: Experiment) -> Path:
        raise NotImplementedError
