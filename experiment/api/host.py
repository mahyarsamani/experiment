from .job import Job


class Host:
    def __init__(
        self, name: str, domain: str, capacity: int, max_threads: int
    ) -> None:
        self._name = name
        self._domain = domain
        self._capacity = capacity
        self._max_threads = max_threads

    def name(self) -> str:
        return self._name

    def domain(self) -> str:
        return self._domain

    def capacity(self) -> int:
        return self._capacity

    def max_threads(self) -> int:
        return self._max_threads

    def __str__(self) -> str:
        return (
            f"Host(nickname={self.name()}, "
            f"capacity={self.capacity()}, "
            f"max_threads={self.max_threads()})"
        )

    def __repr__(self) -> str:
        return self.__str__()
