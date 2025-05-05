from .job import Job

from typing import List
from warnings import warn


class Experiment:
    def __init__(self, name: str) -> None:
        self._name = name
        self._jobs = list()

    def _register_job(self, job: Job) -> None:
        raise NotImplementedError

    def register_job(self, job: Job) -> None:
        self._register_job(job)
        self._jobs.append(job)
        warn(f"Registered job: {job.id()}")

    def get_jobs(self) -> List[Job]:
        return self._jobs
