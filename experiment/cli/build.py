import argparse

from ..api.build import Builder, BuildSpec
from ..common.log import error
from ..common.gem5_work import (
    BinaryOpt,
    ISA,
    Protocol,
)


def parse_build_args(args):
    parser = argparse.ArgumentParser(
        prog="helper build", description="Build gem5."
    )
    parser.add_argument(
        "build_name",
        type=str,
        help="Build name to run.",
    )
    parser.add_argument(
        "--isas",
        type=str,
        help="Comma separated list of ISAs to compile. "
        f"Choose from: {ISA.return_all_values()}",
        required=False,
    )
    parser.add_argument(
        "--protocols",
        type=str,
        help="Comma separated list of Ruby protocols to compile. "
        f"Choose from: {Protocol.return_all_values()}",
        required=False,
    )
    parser.add_argument(
        "--binary_opt",
        type=str,
        help="Binary option to compile.",
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


def _process_build_args(proj_config, build_args, unknown_args) -> int:
    if unknown_args:
        error(f"unrecognized arguments: {' '.join(unknown_args)}")
        return 2

    spec = BuildSpec.from_project(
        proj_config,
        build_args.build_name,
        isas=build_args.isas,
        protocols=build_args.protocols,
        binary_opt=build_args.binary_opt,
        bits_per_set=build_args.bits_per_set,
        linker=build_args.linker,
        gprof=build_args.gprof,
        no_tcmalloc=build_args.no_tcmalloc,
        threads=build_args.threads,
    )
    return Builder(spec).run()
