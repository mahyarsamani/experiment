from .job import Job

from typing import List


class Experiment:
    def __init__(self, name: str) -> None:
        self._name = name
        self._jobs = list()

    def _complete_partial_job(self, job: Job) -> None:
        raise NotImplementedError

    def create_job(self, *args, **kwargs) -> None:
        new_job = Job(*args, **kwargs)
        self._complete_partial_job(new_job)
        self._jobs.append(new_job)

    def get_jobs(self) -> List[Job]:
        return self._jobs
