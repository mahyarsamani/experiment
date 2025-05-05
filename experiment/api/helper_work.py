from .experiment import Experiment
from .job import Job

from enum import Enum
from pathlib import Path


class OtherValues(Enum):
    Store_Const = "store_const"

    def has_value(self, value):
        if value == OtherValues.Store_Const:
            return False
        return True


class HelperJob(Job):
    def __init__(
        self,
        run_script_path: Path,
        *args,
        **kwargs,
    ):
        super().__init__()
        self._partial_command = self._build_partial_command(
            run_script_path, *args, **kwargs
        )
        self._run_script_path = run_script_path
        self._args = args
        self._kwargs = kwargs

    def _build_partial_command(self, run_script_path, *args, **kwargs) -> str:
        positional_args = " ".join(map(str, args))
        keyword_items = []
        for key, value in kwargs.items():
            if isinstance(value, OtherValues) and not value.has_value(value):
                keyword_items += [f"--{key.replace('_', '-')}"]
            else:
                keyword_items += [f"--{key.replace('_', '-')} {value}"]
        keyword_args = " ".join(keyword_items)
        return f"{run_script_path} {positional_args} {keyword_args}".strip()


class HelperExperiment(Experiment):
    def __init__(self, name: str, project_dir: Path):
        super().__init__(name)
        self._project_dir = project_dir

    def get_project_dir(self) -> Path:
        return self._project_dir

    def get_env_path(self) -> Path:
        return self.get_project_dir() / "env"

    def _register_job(self, job):
        if not isinstance(job, HelperJob):
            raise RuntimeError("Job is not a HelperJob.")

        job.set_command(
            f"helper run --build-name {self._name} "
            f"--outdir={self._name}/{job.id()} {job.partial_command()}"
        )
        job.set_cwd(self.get_project_dir())
        job.set_env_path(self.get_env_path())
