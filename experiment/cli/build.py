import argparse

from ..common.cmd_util import run_command
from ..common.config_util import configure_build_directory
from ..common.gem5_work import (
    BinaryOpt,
    gem5BuildConfiguration,
    ISA,
    Protocol,
)


def parse_build_args(args):
    parser = argparse.ArgumentParser("Parse build command from helper.")
    parser.add_argument(
        "build_name",
        type=str,
        help="Build name to run.",
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
        "--bits-per-set",
        type=int,
        help="Number of bits per set to use for Ruby compilation.",
        required=False,
    )
    parser.add_argument(
        "--linker",
        type=str,
        help="Linker to use when compiling other than ld.",
        choices=["bfd", "gold", "mold"],
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
    parser.add_argument(
        "--threads",
        type=int,
        help="Number of threads to use when compiling.",
        required=False,
    )
    return parser.parse_known_args(args)


def _process_build_args(proj_config, build_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    build_dir = (
        proj_config.path_config().gem5_binary_base_dir()
        / build_args.build_name
    )

    build_config = gem5BuildConfiguration.from_args_and_config(
        build_args,
        proj_config.build_config(),
    )

    configure_build_directory(
        proj_config.path_config().gem5_source_dir(), build_dir, build_config
    )

    command = (
        f"scons -C {proj_config.path_config().gem5_source_dir()} "
        f"gem5.{build_config.binary_opt}"
    )
    if build_args.linker:
        command += f" --linker={build_args.linker}"
        linker_override_path = build_dir / "gem5.build" / "linker_override"
        linker_override_path.unlink(missing_ok=True)
        linker_override_path.touch()
        linker_override_path.write_text(f"{build_args.linker}\n")
    if build_args.gprof:
        command += " --gprof"
        (build_dir / "gem5.build" / "built_with_gprof").touch()
    if build_args.no_tcmalloc:
        command += " --without-tcmalloc"
        (build_dir / "gem5.build" / "built_without_tcmalloc").touch()
    if build_args.threads:
        command += f" -j {build_args.threads}"

    run_command(["bash", "-c", command], build_dir)
