import rpyc
import traceback

from enum import Enum
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .work import Experiment, Job, JobStatus


class SIGNAL(Enum):
    KILL = 9


class Result:
    def __init__(self, success: bool):
        self._success = success

    def ok(self):
        return self._success


class Success(Result):
    def __init__(self, value: Any):
        super().__init__(True)
        self._value = value

    def value(self) -> Any:
        return self._value


class Failure(Result):
    def __init__(self, site_name: str, exception: BaseException):
        super().__init__(False)
        self._exception = exception

        self._message = f"{exception} raised at {site_name}"

    def message(self) -> str:
        assert not self._success
        return self._message

    def traceback(self) -> str:
        return "".join(
            traceback.format_exception(
                type(self._exception),
                self._exception,
                self._exception.__traceback__,
            )
        )


class Human:
    def __init__(self) -> None:
        self._failed = False

    def _fail(self) -> None:
        self._failed = True

    def failed(self) -> bool:
        return self._failed


def healthy(humans: Iterable[Human]) -> Iterable[Human]:
    for human in humans:
        if not human.failed():
            yield human


class Host(Human):
    def __init__(
        self,
        name: str,
        domain: str,
        max_capacity: int,
        port: int,
        file_server_port: int,
    ) -> None:
        super().__init__()
        self._name = name
        self._domain = domain
        self._file_domain = f"{domain}:{file_server_port}"
        self._max_capacity = max_capacity
        self._port = port
        self._file_server_port = file_server_port

        self._connection = None

        self._running_jobs = dict()
        self._finished_jobs = dict()

    def name(self) -> str:
        return self._name

    def max_capacity(self) -> int:
        return self._max_capacity

    def upgrade(self, additional_capacity: int) -> None:
        self._max_capacity += additional_capacity

    def capacity(self) -> int:
        return self._max_capacity - sum(
            [
                job.demand()
                for _, jobs in self._running_jobs.items()
                for job in jobs
            ]
        )

    def _fail_gracefully(self, func: Callable, *args, **kwargs) -> Result:
        try:
            ret = func(*args, **kwargs)
            return Success(ret)
        except Exception as e:
            self._fail()
            return Failure(f"{self._name}::{func.__name__}", e)

    def _connect(self) -> bool:
        if self._connection is not None:
            raise RuntimeError(f"Already connected to {self}.")
        self._connection = rpyc.connect(self._domain, self._port)
        return True

    def connect(self) -> Result:
        return self._fail_gracefully(self._connect)

    def _disconnect(self) -> bool:
        if self._connection is None:
            raise RuntimeError(f"Not connected to {self}.")
        self._connection.close()
        self._connection = None
        return True

    def disconnect(self) -> Result:
        return self._fail_gracefully(self._disconnect)

    def _launch_job(self, job: Job) -> int:
        if self._connection is None:
            raise RuntimeError(f"Connection not established for {self}.")
        job.set_pid(
            self._connection.root.launch_job(
                job.cwd().as_posix(),
                job.command(),
                job.outdir().as_posix(),
                [path.as_posix() for _, path in job.aux_file_io()],
                [
                    (content, path.as_posix())
                    for _, content, path in job.optional_dump()
                ],
            )
        )
        job.set_links(
            self._name,
            [
                (
                    label,
                    f"/files?host={self._file_domain}&path={quote(str(path))}",
                )
                for label, path in job.file_io()
            ],
        )
        job.set_status(JobStatus.PENDING)

        if job.experiment() not in self._running_jobs:
            self._running_jobs[job.experiment()] = []
            self._finished_jobs[job.experiment()] = []
        self._running_jobs[job.experiment()].append(job)

        return job.pid()

    def launch_job(self, job: Job) -> Result:
        return self._fail_gracefully(self._launch_job, job)

    def _kill_job(self, job: Job, signal: int) -> bool:
        if self._connection.root.kill_job(job.pid(), signal):
            job.set_status(JobStatus.KILLED)
            self._finished_jobs[job.experiment()].append(job)
            self._running_jobs[job.experiment()].remove(job)
            return True
        else:
            raise RuntimeError(f"{self._name} failed to kill {job}.")

    def kill_job(self, job: Job, signal: int) -> Result:
        return self._fail_gracefully(self._kill_job, job, signal)

    def _update(self) -> bool:
        for experiment, jobs in self._running_jobs.items():
            for job in jobs:
                job.set_status(self._connection.root.job_status(job.pid()))
                if not job.running():
                    self._finished_jobs[experiment].append(job)
                    self._running_jobs[experiment].remove(job)
        return True

    def update(self) -> Result:
        return self._fail_gracefully(self._update)

    def _kill_experiment(self, experiment: Experiment) -> Result:
        if experiment.name() not in self._running_jobs:
            True

        for job in self._running_jobs[experiment.name()]:
            self._kill_job(job, 9)

        return True

    def kill_experiment(self, experiment: Experiment) -> Result:
        return self._fail_gracefully(self._kill_experiment, experiment)

    def idle(self):
        return sum([len(jobs) for _, jobs in self._running_jobs.items()]) == 0

    def serialize(self) -> dict:
        return {
            "name": self._name,
            "domain": self._domain,
            "max_capacity": self._max_capacity,
            "port": self._port,
            "file_server_port": self._file_server_port,
        }

    @classmethod
    def deserialize(cls, serialized_host: dict) -> "Host":
        return cls(
            name=serialized_host["name"],
            domain=serialized_host["domain"],
            max_capacity=serialized_host["max_capacity"],
            port=serialized_host["port"],
            file_server_port=serialized_host["file_server_port"],
        )

    def __hash__(self) -> int:
        return hash(
            (
                self._name,
                self._domain,
                self._max_capacity,
                self._port,
                self._file_server_port,
            )
        )

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(nickname={self.name()}, capacity={self.capacity()})"

    def __repr__(self) -> str:
        return self.__str__()
