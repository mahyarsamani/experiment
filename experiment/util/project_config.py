from ..cli import assets
from .defaults import config_file_name, gem5_repo_url

import json
import os
import platform
import shutil
import subprocess

from argparse import Namespace
from enum import Enum
from git import Repo
from importlib.resources import files
from pathlib import Path
from typing import List, Dict
from warnings import warn


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


class Linker(Enum):
    BFD = "bfd"
    GOLD = "gold"
    LLD = "lld"
    MOLD = "mold"

    @classmethod
    def from_string(cls, linker: str) -> None:
        uppered = linker.upper()
        if uppered == "BFD":
            return cls.BFD
        elif uppered == "GOLD":
            return cls.GOLD
        elif uppered == "LLD":
            return cls.LLD
        elif uppered == "MOLD":
            return cls.MOLD
        else:
            raise ValueError(f"Unknown linker: {linker}")

    @classmethod
    def return_all_values(cls) -> List[str]:
        return ["bfd", "gold", "lld", "mold"]

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.__str__()


def _is_valid(args, attr):
    if hasattr(args, attr):
        return getattr(args, attr) is not None
    else:
        return False


class CompileConfiguration:
    def __init__(
        self,
        isas: str,
        protocols: str,
        binary_opt: str,
        linker: str,
        bits_per_set: int,
        threads: int,
    ):
        self.isas = ISA.from_comma_separated_string(isas)
        self.protocols = Protocol.from_comma_separated_string(protocols)
        self.binary_opt = BinaryOpt.from_string(binary_opt)
        self.linker = Linker.from_string(linker)
        self.bits_per_set = bits_per_set
        self.threads = threads

        self._check_validitry()

    def _check_validitry(self):
        if Protocol.VIPER in self.protocols and ISA.X86 not in self.isas:
            raise ValueError("VIPER protocol only works with X86 ISA.")
        if Protocol.NOTHING in self.protocols:
            if self.bits_per_set != -1:
                raise ValueError(
                    "`bits_per_set` is meaningless for non RubyProtocols."
                )
        else:
            if self.bits_per_set == -1:
                raise ValueError(
                    "`bits_per_set` is required for RubyProtocols."
                )

    def is_same(self, other_dict):
        this_dict = self.to_dict()
        return (
            this_dict["isas"] == other_dict["isas"]
            and this_dict["protocols"] == other_dict["protocols"]
            and this_dict["binary_opt"] == other_dict["binary_opt"]
            and this_dict["bits_per_set"] == other_dict["bits_per_set"]
        )

    def make_build_dir_name(self, args: Namespace) -> str:
        if _is_valid(args, "build_name"):
            return args.build_name
        isa_part = "_and_".join([str(isa) for isa in self.isas])
        protocol_part = "_and_".join(
            [str(protocol) for protocol in self.protocols]
        )
        opt_part = str(self.binary_opt)
        build_dir_name = f"{isa_part}-{protocol_part}-{opt_part}"
        return build_dir_name

    def _make_setconfig_command(self, build_dir):
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
            "isas": [str(isa) for isa in self.isas],
            "protocols": [str(protocol) for protocol in self.protocols],
            "binary_opt": str(self.binary_opt),
            "linker": str(self.linker),
            "bits_per_set": self.bits_per_set,
            "threads": self.threads,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompileConfiguration":
        return cls(
            isas=",".join(data["isas"]),
            protocols=",".join(data["protocols"]),
            binary_opt=data["binary_opt"],
            threads=data["threads"],
            linker=data["linker"],
        )

    @classmethod
    def from_args_and_config(
        cls, args: Namespace, config: "CompileConfiguration"
    ) -> "CompileConfiguration":

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

        linker = (
            str(config.linker)
            if not _is_valid(args, "linker") is None
            else args.linker
        )
        bits_per_set = (
            config.bits_per_set
            if not _is_valid(args, "bits_per_set")
            else args.bits_per_set
        )

        threads = (
            config.threads if not _is_valid(args, "threads") else args.threads
        )

        return cls(
            isas=isas,
            protocols=protocols,
            binary_opt=binary_opt,
            linker=linker,
            bits_per_set=bits_per_set,
            threads=threads,
        )


class PathConfiguration:
    NULL_PATH = Path("/dev/null")

    def __init__(
        self,
        project_dir: str,
        gem5_source_dir: str,
        gem5_resource_json_path: str,
        gem5_binary_base_dir: str,
        gem5_out_base_dir: str,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.gem5_source_dir = Path(gem5_source_dir).resolve()
        self.gem5_resource_json_path = Path(gem5_resource_json_path).resolve()
        self.gem5_binary_base_dir = Path(gem5_binary_base_dir).resolve()
        self.gem5_out_base_dir = Path(gem5_out_base_dir).resolve()

    def initialize_directories(self, config_dict: Dict):
        self.project_dir.mkdir(parents=True, exist_ok=True)

        components_dir = self.project_dir / "components"
        components_dir.mkdir(parents=True, exist_ok=True)
        (components_dir / "__init__.py").touch(exist_ok=True)

        scripts_dir = self.project_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        scripts_util_dir = scripts_dir / "util"
        scripts_util_dir.mkdir(parents=True, exist_ok=True)
        (scripts_util_dir / "__init__.py").touch(exist_ok=True)

        if (scripts_util_dir / "decorators.py").exists():
            warn(
                "`decorators.py` already exists. I am not going to overwrite it."
            )
        else:
            decorators_py = files(assets).joinpath("decorators.py")
            shutil.copy(
                decorators_py,
                scripts_util_dir / "decorators.py",
            )
        if (scripts_dir / "run_example.py").exists():
            warn(
                "`run_example.py` already exists. I am not going to overwrite it."
            )
        else:
            example_py = files(assets).joinpath("run_example.py")
            shutil.copy(
                example_py,
                scripts_dir / "run_example.py",
            )

        warn(
            "Created `components` and `scripts` directories. "
            "I recommend putting all the components you build "
            "(e.g. cache hierarchies, processors, memories, boards, etc.) "
            "in the `components` directory. I also recommend to put all your "
            "run_scripts in `scripts` directory. I have found this to be a "
            "good practice. Additionally, I recommend using two decorators "
            "to expose the project directory and record the arguments you pass"
            "to the run function. I have already added the files needed for "
            "them under `scripts/util`. Import them like below:\n"
            "from util.decorators import expose_prject_dir, record_args\n"
            "I also will put an example in scripts directory that"
            "uses traffic generators with all the decorators."
        )

        if not self.gem5_source_dir.exists():
            self.gem5_source_dir.mkdir(parents=True, exist_ok=True)
            warn(
                f"{self.gem5_source_dir} does not exist. "
                "I am cloning mainline gem5 repository. "
                "If you want to use a different version, "
                "you should manually put it there."
            )
            Repo.clone_from(gem5_repo_url, self.gem5_source_dir)
        else:
            if not any(self.gem5_source_dir.iterdir()):
                warn(
                    f"{self.gem5_source_dir} is empty. "
                    "I am cloning mainline gem5 repository. "
                    "If you want to use a different version, "
                    "you should manually put it there."
                )
                Repo.clone_from(gem5_repo_url, self.gem5_source_dir)
        if (
            self.gem5_resource_json_path != PathConfiguration.NULL_PATH
            or not self.gem5_resource_json_path.exists()
        ):
            raise ValueError(
                "`gem5_resource_json_path` must be set to a valid path."
            )
        self.gem5_binary_base_dir.mkdir(parents=True, exist_ok=True)
        self.gem5_out_base_dir.mkdir(parents=True, exist_ok=True)

        try:
            os.symlink(
                self.gem5_binary_base_dir,
                self.gem5_source_dir / "build",
                target_is_directory=True,
            )
        except FileExistsError:
            warn(
                f"{self.gem5_source_dir / 'build'}."
                "is alread a symlink. "
                "I am not going to overwrite it. If it is not correct, "
                "You have to correct it manually."
            )
        except Exception as err:
            raise err

        try:
            os.symlink(
                self.gem5_out_base_dir,
                self.project_dir / "gem5-out",
                target_is_directory=True,
            )
        except FileExistsError:
            warn(
                f"{self.project_dir / 'gem5-out'}."
                "is alread a symlink. "
                "I am not going to overwrite it. If it is not correct, "
                "You have to correct it manually."
            )
        except Exception as err:
            raise err

        with open(self.project_dir / config_file_name, "w") as config_json:
            json.dump(config_dict, config_json, indent=2)

    def to_dict(self) -> dict:
        return {
            "project_dir": str(self.project_dir),
            "gem5_source_dir": str(self.gem5_source_dir),
            "gem5_resource_json_path": str(self.gem5_resource_json_path),
            "gem5_binary_base_dir": str(self.gem5_binary_base_dir),
            "gem5_out_base_dir": str(self.gem5_out_base_dir),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PathConfiguration":
        return cls(
            project_dir=data["project_dir"],
            gem5_source_dir=data["gem5_source_dir"],
            gem5_resource_json_path=data["gem5_resource_json_path"],
            gem5_binary_base_dir=data["gem5_binary_base_dir"],
            gem5_out_base_dir=data["gem5_out_base_dir"],
        )

    @classmethod
    def from_args_and_config(
        cls, args: Namespace, config: "PathConfiguration"
    ) -> "PathConfiguration":
        project_dir = config.project_dir
        gem5_source_dir = config.gem5_source_dir
        gem5_resource_json_path = (
            config.gem5_resource_json_path
            if (Path(args.gem5_resource_json) == PathConfiguration.NULL_PATH)
            else Path(args.gem5_resource_json).resolve()
        )
        gem5_binary_base_dir = config.gem5_binary_base_dir
        gem5_out_base_dir = config.gem5_out_base_dir
        return cls(
            project_dir=project_dir,
            gem5_source_dir=gem5_source_dir,
            gem5_resource_json_path=gem5_resource_json_path,
            gem5_binary_base_dir=gem5_binary_base_dir,
            gem5_out_base_dir=gem5_out_base_dir,
        )


class ProjectConfiguration:
    def __init__(
        self,
        project_name: str,
        project_dir: str,
        gem5_source_dir: str,
        gem5_resource_json_path: str,
        gem5_binary_base_dir: str,
        gem5_out_base_dir: str,
        default_isas: str,
        default_protocols: str,
        default_binary_opt: str,
        default_linker: str,
        default_bits_per_set: int,
        default_threads: int,
    ):
        self.project_name = project_name
        self.path_config = PathConfiguration(
            project_dir,
            gem5_source_dir,
            gem5_resource_json_path,
            gem5_binary_base_dir,
            gem5_out_base_dir,
        )
        self.compile_config = CompileConfiguration(
            default_isas,
            default_protocols,
            default_binary_opt,
            default_linker,
            default_bits_per_set,
            default_threads,
        )

    def initialize_directories(self):
        self.path_config.initialize_directories(self.to_dict())

    def configure_build_directory(
        self, args: Namespace, compile_config: CompileConfiguration
    ) -> None:
        build_dir_name = compile_config.make_build_dir_name(args)
        build_dir = self.path_config.gem5_binary_base_dir / build_dir_name
        build_config = build_dir / "gem5.build" / "compile_config.json"

        need_setconfig = True
        if build_config.exists():
            warn(f"{build_config} already exists. Checking for changes.")
            with open(build_config, "r") as config_file:
                config_dict = json.load(config_file)
                need_setconfig = not compile_config.is_same(config_dict)

        if not need_setconfig:
            warn("No need to set config as it matches current compile config.")
        else:
            warn("Setting config as it does not match current compile config.")
            shutil.rmtree(build_dir, ignore_errors=True)
            build_dir.mkdir(parents=True, exist_ok=True)
            setconfig_cmd = compile_config._make_setconfig_command(build_dir)
            subprocess.run(
                ["bash", "-c", setconfig_cmd],
                cwd=self.path_config.gem5_source_dir,
            )
            with open(build_config, "w") as config_file:
                json.dump(compile_config.to_dict(), config_file, indent=2)
        return build_dir

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "path_config": self.path_config.to_dict(),
            "compile_config": self.compile_config.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfiguration":
        path_dict = data["path_config"]
        compile_dict = data["compile_config"]
        return cls(
            project_name=data["project_name"],
            project_dir=path_dict["project_dir"],
            gem5_source_dir=path_dict["gem5_source_dir"],
            gem5_resource_json_path=path_dict["gem5_resource_json_path"],
            gem5_binary_base_dir=path_dict["gem5_binary_base_dir"],
            gem5_out_base_dir=path_dict["gem5_out_base_dir"],
            default_isas=",".join(compile_dict["isas"]),
            default_protocols=",".join(compile_dict["protocols"]),
            default_binary_opt=compile_dict["binary_opt"],
            default_linker=compile_dict["linker"],
            default_bits_per_set=compile_dict["bits_per_set"],
            default_threads=compile_dict["threads"],
        )
