from .work import Job, Experiment

import rpyc


class Host:
    def __init__(
        self,
        name: str,
        domain: str,
        max_capacity: int,
        port: int,
        file_server_port: int,
    ) -> None:
        self._name = name
        self._domain = domain
        self._max_capacity = max_capacity
        self._port = port
        self._file_server_port = file_server_port

        self._connection = None

        self._running_jobs = list()
        self._finished_jobs = list()

    def name(self) -> str:
        return self._name

    def domain(self) -> str:
        return self._domain

    def file_domain(self) -> str:
        return f"{self._domain}:{self._file_server_port}"

    def max_capacity(self) -> int:
        return self._max_capacity

    def port(self) -> int:
        return self._port

    def capacity(self) -> int:
        return self._max_capacity - sum(
            [job.demand() for job in self._running_jobs]
        )

    def connect(self) -> None:
        if self._connection is not None:
            raise RuntimeError(f"Already connected to {self}.")
        self._connection = rpyc.connect(self._domain, self._port)

    def disconnect(self) -> None:
        if self._connection is None:
            raise RuntimeError(f"Not connected to {self}.")
        self._connection.close()
        self._connection = None

    def launch_job(self, job: Job) -> None:
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
        self._running_jobs.append(job)

    def kill_experiment(self, experiment: Experiment) -> None:
        # NOTE: Update to try to avoid killing jobs that have already finished.
        self.update()

        for job in self._running_jobs:
            if not job.running():
                raise RuntimeError(f"Tried to kill {job} that is not running.")
            if job.experiment() == experiment:
                self._connection.root.kill_job(job.pid())
                job.set_status("killed")

    def update(self):
        for job in self._running_jobs:
            job.set_status(self._connection.root.job_status(job.pid()))

            if job.exited():
                self._finished_jobs.append(job)
                self._running_jobs.remove(job)

    def running_jobs(self):
        return self._running_jobs

    def num_running_jobs(self):
        return len(self._running_jobs)

    def finished_jobs(self):
        return self._finished_jobs

    def jobs(self):
        return self._running_jobs + self._finished_jobs

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
        return f"Host(nickname={self.name()}, capacity={self.capacity()})"

    def __repr__(self) -> str:
        return self.__str__()


class NoHost(Host):
    def __init__(self) -> None:
        super().__init__("NO HOST", "NO HOST", 0, 0, 0)
