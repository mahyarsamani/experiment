import json
import platform
import shlex

from argparse import Namespace
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import List, Optional

from ..api.work import Job, Experiment, ProjectConfiguration
from .log import warn


def _is_valid(args, attr):
    if hasattr(args, attr):
        return getattr(args, attr) is not None
    else:
        return False


def calculate_hash(items: List) -> str:
    # NOTE: json keeps item boundaries, so ("a", "bc") and ("ab", "c")
    # hash differently.
    str_form = json.dumps(items, default=str)
    return sha256(str_form.encode()).hexdigest()


class ISA(Enum):
    NOTHING = "null"
    ARM = "ARM"
    RISCV = "RISCV"
    X86 = "X86"

    @classmethod
    def from_string(cls, isa: str) -> None:
        uppered = isa.upper()
        if uppered in ["NULL", "NOTHING", ""]:
            return cls.NOTHING
        if uppered == "ARM":
            return cls.ARM
        elif uppered == "RISCV":
            return cls.RISCV
        elif uppered == "X86":
            return cls.X86
        else:
            raise ValueError(f"Unknown ISA: {isa}")

    @classmethod
    def from_comma_separated_string(cls, isas: str) -> List["ISA"]:
        isa_list = isas.split(",")
        isa_list = sorted(isa_list)
        ret = [cls.from_string(isa) for isa in isa_list]
        if cls.NOTHING in ret and len(ret) > 1:
            raise ValueError(
                "Cannot use Nothing ISA with other ISAs. "
                "Please use only Nothing ISA."
            )
        return ret

    @classmethod
    def from_kvm_support(cls, os_reported: str) -> "ISA":
        if os_reported == "aarch64":
            return cls.ARM
        if os_reported == "x86_64":
            return cls.X86
        raise ValueError(f"Unknown OS reported ISA: {os_reported}")

    @classmethod
    def return_all_values(cls) -> List[str]:
        return [
            "nothing",
            "arm",
            "riscv",
            "x86",
        ]

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.__str__()


class Protocol(Enum):
    NOTHING = "Nothing"
    CHI = "CHI"
    MESI2 = "MESI_Two_Level"
    MESI3 = "MESI_Three_Level"
    CXL = "CXL"
    VIPER = "GPU_VIPER"

    @classmethod
    def from_string(cls, protocol: str) -> None:
        uppered = protocol.upper()
        if uppered in ["NULL", "NOTHING", ""]:
            return cls.NOTHING
        if uppered in ["CHI"]:
            return cls.CHI
        elif uppered in ["MESI2", "MESI_TWO_LEVEL"]:
            return cls.MESI2
        elif uppered in ["MESI3", "MESI_THREE_LEVEL"]:
            return cls.MESI3
        elif uppered in ["CXL"]:
            return cls.CXL
        elif uppered in ["VIPER", "GPU_VIPER"]:
            return cls.VIPER
        else:
            raise ValueError(f"Unknown protocol: {protocol}")

    @classmethod
    def from_comma_separated_string(cls, protocols: str) -> List["Protocol"]:
        protocol_list = protocols.split(",")
        protocol_list = sorted(protocol_list)
        ret = [cls.from_string(protocol) for protocol in protocol_list]
        if cls.NOTHING in ret and len(ret) > 1:
            raise ValueError(
                "Cannot use Nothing protocol with other protocols. "
                "Please use only Nothing protocol."
            )
        return ret

    @classmethod
    def return_all_values(cls) -> List[str]:
        return ["nothing", "chi", "mesi2", "mesi3", "cxl", "viper"]

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.__str__()


class BinaryOpt(Enum):
    DEBUG = "debug"
    OPT = "opt"
    FAST = "fast"

    @classmethod
    def from_string(cls, binary_opt: str) -> None:
        uppered = binary_opt.upper()
        if uppered == "DEBUG":
            return cls.DEBUG
        elif uppered == "OPT":
            return cls.OPT
        elif uppered == "FAST":
            return cls.FAST
        else:
            raise ValueError(f"Unknown binary option: {binary_opt}")

    @classmethod
    def return_all_values(cls) -> List[str]:
        return ["debug", "opt", "fast"]

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.__str__()


class OtherValues(Enum):
    Store_Const = "store_const"

    def has_value(self, value):
        if value == OtherValues.Store_Const:
            return False
        return True

    def __str__(self) -> str:
        if self == OtherValues.Store_Const:
            return "OtherValues.Store_Const"


def _literal(value) -> str:
    """Python source that evaluates to `value` (for constructor.py)."""
    if isinstance(value, Path):
        return f"Path({str(value)!r})"
    if isinstance(value, OtherValues):
        return f"OtherValues.{value.name}"
    return repr(value)


class gem5Job(Job):
    """One gem5 run: `gem5 --outdir=<outdir> <script> <args> --<kwargs>`.

    Positional args are passed as they are; each keyword argument becomes
    `--key-name value` (or just `--key-name` for OtherValues.Store_Const).
    """

    @staticmethod
    def _cli_args(args, kwargs, shorten: bool) -> list[str]:
        def show(value) -> str:
            if shorten and isinstance(value, Path):
                return str(Path(*value.parts[-2:]))
            return str(value)

        words = [show(arg) for arg in args]
        for key, value in kwargs.items():
            flag = f"--{key.replace('_', '-')}"
            if isinstance(value, OtherValues) and not value.has_value(value):
                words.append(flag)
            else:
                words += [flag, show(value)]
        return words

    @staticmethod
    def make_command(
        gem5_path: Path, outdir: Path, run_script_path: Path, *args, **kwargs
    ) -> str:
        words = [
            str(gem5_path),
            f"--outdir={outdir}",
            str(run_script_path),
            *gem5Job._cli_args(args, kwargs, shorten=False),
        ]
        return shlex.join(words)

    @staticmethod
    def make_shorthand_command(
        gem5_path: Path, run_script_path: Path, *args, **kwargs
    ) -> str:
        words = [
            str(Path(*gem5_path.parts[-2:])),
            run_script_path.name,
            *gem5Job._cli_args(args, kwargs, shorten=True),
        ]
        return " ".join(words)

    @staticmethod
    def write_constructor(
        experiment: "gem5Experiment",
        demand: int,
        run_script_path: Path,
        *args,
        **kwargs,
    ) -> str:
        """Source for a script that recreates this job."""
        call_args = [
            "experiment",
            repr(demand),
            _literal(Path(run_script_path)),
            *(_literal(arg) for arg in args),
            *(f"{key}={_literal(value)}" for key, value in kwargs.items()),
        ]
        body = "".join(f"    {arg},\n" for arg in call_args)
        return (
            "from pathlib import Path\n\n"
            "from experiment.common.gem5_work import (\n"
            "    OtherValues,\n"
            "    gem5Experiment,\n"
            "    gem5Job,\n"
            ")\n\n"
            "experiment = gem5Experiment(\n"
            f"    name={experiment.name()!r},\n"
            f"    cwd={_literal(experiment.cwd())},\n"
            f"    gem5_path={_literal(experiment.gem5_path())},\n"
            f"    outdir={_literal(experiment.outdir())},\n"
            ")\n"
            f"job = gem5Job(\n{body})\n"
            "experiment.register_job(job)\n"
        )

    def __init__(
        self,
        experiment: "gem5Experiment",
        demand: int,
        run_script_path: Path,
        *args,
        **kwargs,
    ):
        run_script_path = Path(run_script_path)
        id = calculate_hash(
            [
                experiment.name(),
                experiment.gem5_path(),
                run_script_path,
                list(args),
                sorted(kwargs.items()),
            ]
        )
        outdir = experiment.outdir() / id
        super().__init__(
            experiment.name(),
            experiment.cwd(),
            gem5Job.make_command(
                experiment.gem5_path(),
                outdir,
                run_script_path,
                *args,
                **kwargs,
            ),
            gem5Job.make_shorthand_command(
                experiment.gem5_path(), run_script_path, *args, **kwargs
            ),
            outdir,
            demand,
            id,
            aux_files=[
                ("stats", outdir / "stats.txt"),
                ("terminal", outdir / "board.terminal"),
            ],
            dumps=[
                (
                    "constructor",
                    gem5Job.write_constructor(
                        experiment, demand, run_script_path, *args, **kwargs
                    ),
                    outdir / "constructor.py",
                )
            ],
        )
        self._run_script_path = run_script_path
        self._args = args
        self._kwargs = kwargs


# NOTE: Old name, kept so existing experiment scripts keep working.
gem5FSSimulation = gem5Job


class gem5Experiment(Experiment):
    def __init__(
        self,
        name: str,
        cwd: Path,
        gem5_path: Path,
        outdir: Path,
    ):
        super().__init__(name, Path(outdir).resolve())
        self._cwd = Path(cwd).resolve()
        self._gem5_path = Path(gem5_path).resolve()

    def cwd(self) -> Path:
        return self._cwd

    def gem5_path(self) -> Path:
        return self._gem5_path


class gem5BuildConfiguration:
    def __init__(
        self,
        isas: str,
        protocols: str,
        binary_opt: str,
        bits_per_set: int,
    ):
        self.isas = ISA.from_comma_separated_string(isas)
        self.protocols = Protocol.from_comma_separated_string(protocols)
        self.binary_opt = BinaryOpt.from_string(binary_opt)
        self.bits_per_set = bits_per_set

        self._check_validity()

    def _check_validity(self):
        if Protocol.VIPER in self.protocols and ISA.X86 not in self.isas:
            raise ValueError("VIPER protocol only works with X86 ISA.")

    def is_same(self, other_dict):
        this_dict = self.dump_config()
        return (
            this_dict["isas"] == other_dict["isas"]
            and this_dict["protocols"] == other_dict["protocols"]
            and this_dict["bits_per_set"] == other_dict["bits_per_set"]
        )

    def make_setconfig_command(self, build_dir):
        command = f"scons setconfig {build_dir}"
        if ISA.NOTHING not in self.isas:
            command += " BUILD_ISA=y "
            command += " ".join([f"USE_{str(isa)}_ISA=y" for isa in self.isas])

        if Protocol.NOTHING not in self.protocols:
            command += " RUBY=y "
            if len(self.protocols) > 1:
                command += 'USE_MULTIPLE_PROTOCOLS=y PROTOCOL="MULTIPLE" '
            if Protocol.VIPER in self.protocols:
                command += "BUILD_GPU=y VEGA_GPU_ISA=y "
            command += " ".join(
                [
                    f"RUBY_PROTOCOL_{str(protocol)}=y"
                    for protocol in self.protocols
                ]
            )

            command += f" NUMBER_BITS_PER_SET={self.bits_per_set}"

        try:
            kvm_isa = ISA.from_kvm_support(platform.machine())
            if kvm_isa in self.isas:
                command += f" USE_KVM=y KVM_ISA={str(kvm_isa).lower()}"
        except ValueError:
            warn("KVM is not supported on this platform for this compilation.")
        return command

    def to_dict(self) -> dict:
        return {
            "isas": self.isas,
            "protocols": self.protocols,
            "binary_opt": self.binary_opt,
            "bits_per_set": self.bits_per_set,
        }

    def dump_config(self) -> dict:
        return {
            "isas": [str(isa) for isa in self.isas],
            "protocols": [str(protocol) for protocol in self.protocols],
            "bits_per_set": self.bits_per_set,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "gem5BuildConfiguration":
        return cls(
            isas=",".join(data["isas"]),
            protocols=",".join(data["protocols"]),
            binary_opt=data["binary_opt"],
            bits_per_set=data["bits_per_set"],
        )

    @classmethod
    def from_args_and_config(
        cls, args: Namespace, config: "gem5BuildConfiguration"
    ) -> "gem5BuildConfiguration":
        isas = (
            ",".join([str(isa) for isa in config.isas])
            if not _is_valid(args, "isas")
            else args.isas
        )

        protocols = (
            ",".join([str(protocol) for protocol in config.protocols])
            if not _is_valid(args, "protocols")
            else args.protocols
        )

        binary_opt = (
            str(config.binary_opt)
            if not _is_valid(args, "binary_opt")
            else args.binary_opt
        )

        bits_per_set = (
            config.bits_per_set
            if not _is_valid(args, "bits_per_set")
            else args.bits_per_set
        )

        return cls(
            isas=isas,
            protocols=protocols,
            binary_opt=binary_opt,
            bits_per_set=bits_per_set,
        )


class gem5PathConfiguration:
    def __init__(
        self,
        project_dir: str,
        gem5_source_dir: str,
        gem5_binary_base_dir: str,
        gem5_out_base_dir: str,
        gem5_resource_json_path: Optional[str] = None,
    ):
        self._project_dir = Path(project_dir).resolve()
        self._gem5_source_dir = Path(gem5_source_dir).resolve()
        self._gem5_binary_base_dir = Path(gem5_binary_base_dir).resolve()
        self._gem5_out_base_dir = Path(gem5_out_base_dir).resolve()
        if gem5_resource_json_path is not None:
            self._gem5_resource_json_path = Path(
                gem5_resource_json_path
            ).resolve()
        else:
            self._gem5_resource_json_path = None

    def project_dir(self):
        return self._project_dir

    def gem5_source_dir(self):
        return self._gem5_source_dir

    def gem5_resource_json_path(self):
        return self._gem5_resource_json_path

    def gem5_binary_base_dir(self):
        return self._gem5_binary_base_dir

    def gem5_out_base_dir(self):
        return self._gem5_out_base_dir

    def to_dict(self) -> dict:
        return {
            "project_dir": self._project_dir,
            "gem5_source_dir": self._gem5_source_dir,
            "gem5_binary_base_dir": self._gem5_binary_base_dir,
            "gem5_out_base_dir": self._gem5_out_base_dir,
            "gem5_resource_json_path": self._gem5_resource_json_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "gem5PathConfiguration":
        return cls(
            project_dir=data["project_dir"],
            gem5_source_dir=data["gem5_source_dir"],
            gem5_binary_base_dir=data["gem5_binary_base_dir"],
            gem5_out_base_dir=data["gem5_out_base_dir"],
            gem5_resource_json_path=data["gem5_resource_json_path"],
        )


class gem5ProjectConfiguration(ProjectConfiguration):
    def __init__(
        self,
        project_name: str,
        project_dir: str,
        gem5_source_dir: str,
        gem5_binary_base_dir: str,
        gem5_out_base_dir: str,
        gem5_resource_json_path: str,
        default_isas: str,
        default_protocols: str,
        default_binary_opt: str,
        default_bits_per_set: int,
    ):
        super().__init__()
        self._name = project_name
        self._path_config = gem5PathConfiguration(
            project_dir,
            gem5_source_dir,
            gem5_binary_base_dir,
            gem5_out_base_dir,
            gem5_resource_json_path,
        )
        self._build_config = gem5BuildConfiguration(
            default_isas,
            default_protocols,
            default_binary_opt,
            default_bits_per_set,
        )

    def name(self):
        return self._name

    def base_dir(self):
        return self._path_config.project_dir()

    def get_experiment_dir(self, experiment: gem5Experiment) -> Path:
        if not isinstance(experiment, gem5Experiment):
            raise ValueError(
                "experiment must be an instance of gem5Experiment"
            )
        return self._path_config.project_dir() / experiment.name()

    def path_config(self):
        return self._path_config

    def build_config(self):
        return self._build_config

    def to_dict(self) -> dict:
        return {
            "project_name": self.name(),
            "path_config": self._path_config.to_dict(),
            "build_config": self._build_config.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "gem5ProjectConfiguration":
        path_dict = data["path_config"]
        build_dict = data["build_config"]
        return cls(
            project_name=data["project_name"],
            project_dir=path_dict["project_dir"],
            gem5_source_dir=path_dict["gem5_source_dir"],
            gem5_resource_json_path=path_dict["gem5_resource_json_path"],
            gem5_binary_base_dir=path_dict["gem5_binary_base_dir"],
            gem5_out_base_dir=path_dict["gem5_out_base_dir"],
            default_isas=",".join(build_dict["isas"]),
            default_protocols=",".join(build_dict["protocols"]),
            default_binary_opt=build_dict["binary_opt"],
            default_bits_per_set=build_dict["bits_per_set"],
        )
