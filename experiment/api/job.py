import hashlib


class Job:
    def __init__(
        self,
        *args,
        **kwargs,
    ):
        self._partial_command = self._build_partial_command(*args, **kwargs)
        self._command = None

    def _build_partial_command(self, *args, **kwargs) -> str:
        raise NotImplementedError

    def partial_command(self):
        return self._command

    def set_full_command(self, command):
        self._command = command

    def command(self):
        if self._command is None:
            raise RuntimeError("Command has not been set.")
        return self._command

    def id(self):
        hash_object = hashlib.sha256(self._partial_command.encode())
        return hash_object.hexdigest()

    def __hash__(self):
        return hash(self._command)

    def __str__(self):
        return f"Job(command={self._command})"

    def __repr__(self):
        return self.__str__()
