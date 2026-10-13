"""Building gem5: the backend shared by `helper build` and build jobs.

A `BuildSpec` says what to build (sources, build directory, ISAs,
protocols, options); a `Builder` builds it on the machine it runs on:

- `helper build` makes a spec from its arguments and runs a Builder;
- a build job writes its spec into its outdir and runs, on the worker,

    python -m experiment.api.build <outdir>/.job/build.json

A lock on the host's build directory keeps two builds of the same name on
one host (from either frontend) from running at once.
"""

import fcntl
import json
import shutil
import sys

from dataclasses import asdict, dataclass
from pathlib import Path

from ..common.cmd_util import run_command
from ..common.gem5_work import gem5BuildConfiguration
from ..common.log import error, info, warn

COMPILE_CONFIG_FILE = "compile_config.json"


@dataclass
class BuildSpec:
    name: str
    gem5_source_dir: str
    build_dir: str
    isas: str
    protocols: str
    binary_opt: str
    bits_per_set: int
    linker: str | None = None
    gprof: bool = False
    no_tcmalloc: bool = False
    threads: int | None = None

    @classmethod
    def from_project(
        cls,
        project_config,
        name: str,
        isas: str | None = None,
        protocols: str | None = None,
        binary_opt: str | None = None,
        bits_per_set: int | None = None,
        linker: str | None = None,
        gprof: bool = False,
        no_tcmalloc: bool = False,
        threads: int | None = None,
    ) -> "BuildSpec":
        """A spec for `project_config`; unset options take the project's
        defaults (its `build_config`)."""
        defaults = project_config.build_config()
        paths = project_config.path_config()
        spec = cls(
            name=name,
            gem5_source_dir=str(paths.gem5_source_dir()),
            build_dir=str(paths.gem5_binary_base_dir() / name),
            isas=isas or ",".join(str(isa) for isa in defaults.isas),
            protocols=protocols
            or ",".join(str(protocol) for protocol in defaults.protocols),
            binary_opt=binary_opt or str(defaults.binary_opt),
            bits_per_set=(
                bits_per_set
                if bits_per_set is not None
                else defaults.bits_per_set
            ),
            linker=linker,
            gprof=gprof,
            no_tcmalloc=no_tcmalloc,
            threads=threads,
        )
        # NOTE: Fail now, not on a worker, if the options don't make sense.
        spec.build_config()
        return spec

    def build_config(self) -> gem5BuildConfiguration:
        return gem5BuildConfiguration(
            self.isas, self.protocols, self.binary_opt, self.bits_per_set
        )

    def binary(self) -> Path:
        return Path(self.build_dir) / f"gem5.{self.build_config().binary_opt}"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def load(cls, path: Path) -> "BuildSpec":
        return cls(**json.loads(Path(path).read_text()))


class Builder:
    def __init__(self, spec: BuildSpec) -> None:
        self._spec = spec
        self._config = spec.build_config()
        self._source = Path(spec.gem5_source_dir)
        self._build_dir = Path(spec.build_dir)

    def scons_command(self) -> str:
        spec = self._spec
        command = f"scons -C {self._source} gem5.{self._config.binary_opt}"
        if spec.linker:
            command += f" --linker={spec.linker}"
        if spec.gprof:
            command += " --gprof"
        if spec.no_tcmalloc:
            command += " --without-tcmalloc"
        if spec.threads:
            command += f" -j {spec.threads}"
        return command

    def _configure(self) -> int:
        """Run `scons setconfig` if the build directory's configuration
        differs. Returns its exit code (0 if not needed)."""
        recorded = self._build_dir / "gem5.build" / COMPILE_CONFIG_FILE
        if recorded.exists():
            warn(f"{recorded} already exists. Checking for changes.")
            if self._config.is_same(json.loads(recorded.read_text())):
                warn("No need to set config as it matches current build config.")
                return 0
        warn("Setting config as it does not match current build config.")
        shutil.rmtree(self._build_dir, ignore_errors=True)
        self._build_dir.mkdir(parents=True, exist_ok=True)
        returncode = run_command(
            ["bash", "-c", self._config.make_setconfig_command(self._build_dir)],
            self._source,
        )
        if returncode != 0:
            # NOTE: Don't record the config, so the next build retries it.
            return returncode
        recorded.write_text(
            json.dumps(self._config.dump_config(), indent=2, default=str)
        )
        return 0

    def _mark_options(self) -> None:
        spec = self._spec
        build = self._build_dir / "gem5.build"
        if spec.linker:
            (build / "linker_override").write_text(f"{spec.linker}\n")
        if spec.gprof:
            (build / "built_with_gprof").touch()
        if spec.no_tcmalloc:
            (build / "built_without_tcmalloc").touch()

    def _lock_path(self) -> Path:
        # NOTE: Next to the build directory, which _configure may delete.
        return self._build_dir.parent / f".{self._build_dir.name}.build.lock"

    def run(self) -> int:
        """Configure if needed and build. Returns the exit code."""
        self._build_dir.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path(), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                info(
                    f"Another build of {self._spec.name} is running on this "
                    "host; waiting for it to finish."
                )
                fcntl.flock(lock, fcntl.LOCK_EX)
            returncode = self._configure()
            if returncode != 0:
                error(f"scons setconfig failed with exit code {returncode}.")
                return returncode
            self._mark_options()
            return run_command(
                ["bash", "-c", self.scons_command()], self._build_dir
            )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m experiment.api.build <spec.json>", file=sys.stderr)
        return 2
    return Builder(BuildSpec.load(Path(argv[0]))).run()


if __name__ == "__main__":
    sys.exit(main())
