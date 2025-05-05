from ..util.config_util import _get_project_config
from ..util.project_config import (
    BinaryOpt,
    CompileConfiguration,
    Linker,
    ISA,
    Protocol,
)

import argparse
import subprocess


def parse_build_args(args):
    parser = argparse.ArgumentParser("Parse build command from helper.")
    parser.add_argument(
        "--build-name",
        type=str,
        help="Build name to run.",
        required=False,
    )
    parser.add_argument(
        "--isas",
        type=str,
        help="Comma separated list of compiled to run."
        f"Choose from: {ISA.return_all_values()}",
        required=False,
    )
    parser.add_argument(
        "--protocols",
        type=str,
        help="Comma separated list of isas to compile by default."
        f"Choose from: {Protocol.return_all_values()}",
        required=False,
    )
    parser.add_argument(
        "--binary_opt",
        type=str,
        help="Default binary option to use when compiling.",
        choices=BinaryOpt.return_all_values(),
        required=False,
    )
    parser.add_argument(
        "--linker",
        type=str,
        help="Linker to use when compiling.",
        choices=Linker.return_all_values(),
        required=False,
    )
    parser.add_argument(
        "--bits-per-set",
        type=int,
        help="Number of bits per sit to use for Ruby compilation.",
        required=False,
    )
    parser.add_argument(
        "--threads",
        type=int,
        help="Number of threads to use when compiling.",
        required=False,
    )
    parser.add_argument(
        "--gprof",
        dest="gprof",
        action="store_const",
        const=True,
        default=False,
        help="Whether to compile gem5 with gprof.",
    )
    parser.add_argument(
        "--no-tcmalloc",
        dest="no_tcmalloc",
        action="store_const",
        const=True,
        default=False,
        help="Whether to compile gem5 without tcmalloc.",
    )
    return parser.parse_known_args(args)


def finalize_build_args(build_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    proj_config = _get_project_config()

    compile_config = CompileConfiguration.from_args_and_config(
        build_args, proj_config.compile_config
    )

    build_dir = proj_config.configure_build_directory(
        build_args, compile_config
    )

    command = (
        f"scons -C {proj_config.path_config.gem5_source_dir} "
        f"gem5.{compile_config.binary_opt} "
        f"-j {compile_config.threads} "
        f"--linker={compile_config.linker}"
    )

    if build_args.gprof:
        command += " --gprof"
    if build_args.no_tcmalloc:
        command += " --without-tcmalloc"

    subprocess.run(["bash", "-c", command], cwd=build_dir)
