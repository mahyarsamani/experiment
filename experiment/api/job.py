import hashlib

from pathlib import Path


class Job:
    def __init__(self) -> None:
        self._partial_command = None
        self._command = None
        self._cwd = None
        self._id = None

    def set_partial_command(self, partial_command: str) -> None:
        self._partial_command = partial_command

    def partial_command(self) -> str:
        if self._partial_command is None:
            raise RuntimeError("Partial command has not been set.")
        return self._partial_command

    def set_command(self, command: str) -> None:
        self._command = command

    def command(self) -> str:
        if self._command is None:
            raise RuntimeError("Command has not been set.")
        return self._command

    def set_cwd(self, cwd: Path) -> None:
        self._cwd = cwd.resolve()

    def cwd(self) -> Path:
        if self._cwd is None:
            raise RuntimeError("CWD has not been set.")
        return self._cwd

    def id(self) -> str:
        if self._id is None:
            self._id = hashlib.sha256(
                self._partial_command.encode()
            ).hexdigest()
        return self._id

    def serialize(self) -> dict:
        return {
            "_partial_command": self._partial_command,
            "_command": self._command,
            "_cwd": str(self._cwd),
            "_id": self._id,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "Job":
        job = cls()
        job._partial_command = data["_partial_command"]
        job._command = data["_command"]
        job._cwd = Path(data["_cwd"])
        job._id = data["_id"]
        return job

    def __hash__(self):
        return hash(self._command)

    def __str__(self):
        return f"Job(command={self._command})"

    def __repr__(self):
        return self.__str__()
