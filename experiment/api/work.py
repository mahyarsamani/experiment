import psutil

from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple


class Job:
    def __init__(
        self,
        experiment: "Experiment",
        cwd: Path,
        command: str,
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
        self._outdir = outdir
        self._stdout = self._outdir / "stdout"
        self._stderr = self._outdir / "stderr"
        self._pid = -1
        self._status = "pending"

        # NOTE: Scheduling and managing a job
        self._demand = demand

        # NOTE: Tracking and printing job status
        self._id = id
        self._optional_dump = optional_dump
        self._aux_file_io = aux_file_io

    def experiment(self) -> "Experiment":
        return self._experiment

    def cwd(self) -> Path:
        return self._cwd

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

    def set_status(self, status: str) -> None:
        self._status = status

    def status(self) -> str:
        return self._status

    def running(self) -> bool:
        return self._status == "running"

    def exited(self) -> bool:
        return self._status == "exited"

    def set_pid(self, pid: int) -> None:
        self._pid = pid

    def pid(self) -> int:
        return self._pid

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

    def id_dict(self) -> Dict:
        raise NotImplementedError

    def serialize(self) -> Dict:
        return {
            "cwd": self._cwd.as_posix(),
            "command": self._command,
            "outdir": self._outdir.as_posix(),
            "demand": self._demand,
            "id": self._id,
            "aux_file_io": [
                (name, path.as_posix()) for name, path in self._aux_file_io
            ],
            "optional_dump": [
                (name, content, path.as_posix())
                for name, content, path in self._optional_dump
            ],
        }

    @classmethod
    def deserialize(
        cls, experiment: "Experiment", serialized_job: Dict
    ) -> "Job":
        cwd = Path(serialized_job["cwd"])
        command = serialized_job["command"]
        outdir = Path(serialized_job["outdir"])
        demand = serialized_job["demand"]
        id = serialized_job["id"]
        aux_file_io = [
            (name, Path(path)) for name, path in serialized_job["aux_file_io"]
        ]
        optional_dump = [
            (name, content, Path(path))
            for name, content, path in serialized_job["optional_dump"]
        ]

        job = cls(
            experiment,
            cwd,
            command,
            outdir,
            demand,
            id,
            aux_file_io=aux_file_io,
            optional_dump=optional_dump,
        )

        return job

    def __str__(self):
        return f"{__class__.__name__}(cwd={self._cwd}, command={self._command}, outdir={self._outdir}, status={self._status}, pid={self._pid})"


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
        return (
            f"{__class__.__name__}(name={self._name}, outdir={self._outdir})"
        )


class ProjectConfiguration:

    def name(self):
        raise NotImplementedError

    def base_dir(self):
        raise NotImplementedError

    def get_experiment_dir(self, experiment: Experiment) -> Path:
        raise NotImplementedError
