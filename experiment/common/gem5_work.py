from ..api.work import Job, Experiment, ProjectConfiguration

import platform

from argparse import Namespace
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from warnings import warn


def _is_valid(args, attr):
    if hasattr(args, attr):
        return getattr(args, attr) is not None
    else:
        return False


def calculate_hash(items: List) -> str:
    str_form = "".join(map(str, items))
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


class gem5FSSimulation(Job):
    def make_command(
        gem5_path, outdir, run_script_path: Path, *args, **kwargs
    ) -> str:
        positional_args = " ".join(map(str, args))
        keyword_items = []
        for key, value in kwargs.items():
            if isinstance(value, OtherValues) and not value.has_value(value):
                keyword_items += [f"--{key.replace('_', '-')}"]
            else:
                keyword_items += [f"--{key.replace('_', '-')} {value}"]
        keyword_args = " ".join(keyword_items)
        return f"{gem5_path} --outdir={outdir} {run_script_path} {positional_args} {keyword_args}".strip()

    def write_constructor(
        demand: int, run_script_path: Path, *_args, **_kwargs
    ) -> str:
        args = ""
        for arg in _args:
            if isinstance(arg, str):
                args += f'    "{arg}",\n'
            elif isinstance(arg, Path):
                args += f'    Path("{arg}"),\n'
            else:
                args += f"    {arg},\n"
        kwargs = ""
        for key, val in _kwargs.items():
            if isinstance(val, str):
                kwargs += f'    {key}="{val}",\n'
            elif isinstance(val, Path):
                kwargs += f'    {key}=Path("{val}"),\n'
            else:
                kwargs += f"    {key}={val},\n"
        return f"""
job = gem5FSSimulation(
    getExperiment(),
    {demand},
    Path("{run_script_path.as_posix()}"),
{args}
{kwargs}
)
"""

    def __init__(
        self,
        experiment: "gem5Experiment",
        demand: int,
        run_script_path: Path,
        *args,
        **kwargs,
    ):
        items = (
            [run_script_path]
            + list(args)
            + [item for pair in kwargs.items() for item in pair]
        )
        id = calculate_hash(items)
        outdir = experiment.outdir() / id
        command = gem5FSSimulation.make_command(
            experiment.gem5_path(), outdir, run_script_path, *args, **kwargs
        )
        super().__init__(
            experiment,
            experiment.cwd(),
            command,
            outdir,
            demand,
            id,
            aux_file_io=[
                ("stats", outdir / "stats.txt"),
                ("terminal", outdir / "board.terminal"),
            ],
            optional_dump=[
                (
                    "constructor",
                    gem5FSSimulation.write_constructor(
                        demand, run_script_path, *args, **kwargs
                    ),
                    outdir / "constructor.py",
                )
            ],
        )
        self._run_script_path = run_script_path
        self._args = args
        self._kwargs = kwargs

    def id_dict(self) -> Dict:
        return {
            "run_script_path": self._run_script_path.as_posix(),
            "args": [str(arg) for arg in self._args],
            "kwargs": {key: str(value) for key, value in self._kwargs.items()},
        }


class gem5Experiment(Experiment):
    def __init__(
        self,
        name: str,
        cwd: Path,
        gem5_path: Path,
        outdir: Path,
    ):
        super().__init__(name, outdir)
        self._cwd = cwd.resolve()
        self._gem5_path = gem5_path.resolve()

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
