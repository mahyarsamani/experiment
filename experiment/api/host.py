from .job import Job

from pathlib import Path
from typing import List, Tuple


class Host:
    def __init__(
        self,
        name: str,
        domain: str,
        capacity: int,
        python_env_path: Path,
        env_vars: List[Tuple[str, str]],
    ) -> None:
        self._name = name
        self._domain = domain
        self._capacity = capacity
        self._python_env_path = python_env_path
        self._env_vars = env_vars

    def name(self) -> str:
        return self._name

    def domain(self) -> str:
        return self._domain

    def capacity(self) -> int:
        return self._capacity

    def python_env_path(self) -> Path:
        return self._python_env_path

    def env_vars(self) -> List[Tuple[str, str]]:
        return self._env_vars

    def __hash__(self) -> int:
        return hash(f"{self._name}{self._domain}{self._capacity}")

    def __str__(self) -> str:
        return f"Host(nickname={self.name()}, capacity={self.capacity()})"

    def __repr__(self) -> str:
        return self.__str__()
