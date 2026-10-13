"""Running gem5: the backend shared by `helper run` and simulation jobs.

A `RunSpec` says how to run one simulation (binary, script and its
arguments, outdir, debug options, environment); a `Simulator` runs it:

- `helper run` makes a spec from its arguments and runs it (optionally
  under gdb);
- a simulation job writes its spec into its outdir and runs, on the worker,

    python -m experiment.api.simulate <outdir>/.job/run.json

  which replaces itself with gem5, so the job's exit code and processes are
  gem5's own. `helper run --spec <outdir>/.job/run.json` reruns it by hand.
"""

import json
import os
import shlex
import sys

from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..common.log import error, info


@dataclass
class RunSpec:
    binary: str
    script: str
    script_args: list[str] = field(default_factory=list)
    outdir: str | None = None
    # NOTE: gem5's -re: send gem5's stdout and stderr to files in outdir.
    redirect: bool = False
    debug_flags: str | None = None
    debug_start: int | None = None
    debug_end: int | None = None
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def argv(self) -> list[str]:
        argv = [self.binary]
        if self.redirect:
            argv.append("-re")
        if self.outdir is not None:
            argv.append(f"--outdir={self.outdir}")
        if self.debug_flags is not None:
            argv.append(f"--debug-flags={self.debug_flags}")
        if self.debug_start is not None:
            argv.append(f"--debug-start={self.debug_start}")
        if self.debug_end is not None:
            argv.append(f"--debug-end={self.debug_end}")
        return argv + [self.script, *self.script_args]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def load(cls, path: Path) -> "RunSpec":
        return cls(**json.loads(Path(path).read_text()))


class Simulator:
    def __init__(self, spec: RunSpec) -> None:
        self._spec = spec

    def command(self, gdb: bool = False, gdbinit: str | None = None) -> list[str]:
        argv = self._spec.argv()
        if not gdb:
            return argv
        return ["gdb", *(["-x", gdbinit] if gdbinit else []), "--args", *argv]

    def describe(self, gdb: bool = False, gdbinit: str | None = None) -> str:
        """The command as one shell line, with its environment."""
        env = " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in self._spec.env.items()
        )
        command = shlex.join(self.command(gdb, gdbinit))
        return f"{env} {command}" if env else command

    def check(self) -> None:
        binary = Path(self._spec.binary)
        if not binary.exists():
            raise FileNotFoundError(
                f"gem5 binary not found at {binary} on {os.uname().nodename}; "
                "build it there first."
            )

    def _env(self) -> dict[str, str]:
        return dict(os.environ, **self._spec.env)

    def run(self, gdb: bool = False, gdbinit: str | None = None) -> int:
        """Run gem5 (in the foreground) and return its exit code."""
        import subprocess

        self.check()
        cwd = self._spec.cwd or os.getcwd()
        info(f"Running: {self.describe(gdb, gdbinit)} (in {cwd})")
        return subprocess.run(
            self.command(gdb, gdbinit), cwd=cwd, env=self._env()
        ).returncode

    def exec(self) -> None:
        """Replace this process with gem5."""
        self.check()
        if self._spec.cwd:
            os.chdir(self._spec.cwd)
        argv = self.command()
        os.execvpe(argv[0], argv, self._env())


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(
            "usage: python -m experiment.api.simulate <run.json>",
            file=sys.stderr,
        )
        return 2
    try:
        Simulator(RunSpec.load(Path(argv[0]))).exec()
    except FileNotFoundError as e:
        error(e)
        return 127
    return 0  # NOTE: Not reached; exec replaces this process.


if __name__ == "__main__":
    sys.exit(main())
