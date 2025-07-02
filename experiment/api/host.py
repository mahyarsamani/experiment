from .job import Job

from pathlib import Path

class Host:
    def __init__(self, name: str, domain: str, capacity: int, python_env_path: Path) -> None:
        self._name = name
        self._domain = domain
        self._capacity = capacity
        self._python_env_path = python_env_path

    def name(self) -> str:
        return self._name

    def domain(self) -> str:
        return self._domain

    def capacity(self) -> int:
        return self._capacity

    def python_env_path(self) -> Path:
        return self._python_env_path

    def __hash__(self) -> int:
        return hash(f"{self._name}{self._domain}{self._capacity}")

    def __str__(self) -> str:
        return f"Host(nickname={self.name()}, capacity={self.capacity()})"

    def __repr__(self) -> str:
        return self.__str__()
