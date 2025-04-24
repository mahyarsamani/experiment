from .experiment import Experiment
from .job import Job

from enum import Enum
from pathlib import Path
from typing import List


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
        super().__init__(*args, **kwargs)
        self._run_script_path = run_script_path

    def _build_partial_command(self, *args, **kwargs) -> str:
        positional_args = " ".join(map(str, args))
        keyword_items = []
        for key, value in kwargs.items():
            if isinstance(value, OtherValues) and not value.has_value(value):
                keyword_items += [f"--{key.replace('_', '-')}"]
            else:
                keyword_items += [f"--{key.replace('_', '-')} {value}"]
        keyword_args = " ".join(keyword_items)
        return (
            f"{self._run_script_path} {positional_args} {keyword_args}".strip()
        )

    def command(self):
        return self._command

    def __hash__(self):
        return hash(self._command)

    def __str__(self):
        return f"Job(command={self._command})"

    def __repr__(self):
        return self.__str__()


class HelperExperiment(Experiment):
    def __init__(self, name: str, project_dir: Path):
        super().__init__(name)
        self._project_dir = project_dir

    def get_project_dir(self) -> str:
        return str(self._project_dir)

    def get_python_path(self) -> str:
        return str(self._project_dir / "env" / "bin" / "python")

    def _complete_partial_job(self, job):
        job.set_full_command(
            f"helper run --build-name {self._name} "
            f"--outdir={self._name}/{job.id()} {job.partial_command()}"
        )
