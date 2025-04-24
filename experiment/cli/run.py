from ..util.common_util import _parse_build_opt
from ..util.configuration import _get_project_config

import argparse
import os


def parse_run_args(args):
    parser = argparse.ArgumentParser("Parse run command from helper.")
    parser.add_argument("config", type=str, help="Config script to simulate.")
    parser.add_argument(
        "--build-opt",
        type=str,
        help="gem5 Build option to run.",
        required=False,
    )
    parser.add_argument(
        "--build-name",
        type=str,
        required=False,
        default="release",
        help="Build name to use for gem5 compilation.",
    )
    parser.add_argument(
        "--outdir", type=str, help="gem5's output directory.", required=False
    )
    parser.add_argument(
        "--debug-flags",
        type=str,
        help="Debug flags to pass to gem5.",
        required=False,
    )
    parser.add_argument(
        "--debug-start",
        dest="debug_start",
        type=int,
        help="Debug start tick to pass to gem5.",
        required=False,
    )
    parser.add_argument(
        "--debug-end",
        type=int,
        help="Debug start tick to pass to gem5.",
        required=False,
    )
    parser.add_argument(
        "--override-py",
        dest="override",
        action="store_const",
        const=True,
        default=False,
        help="Override m5 python source.",
    )
    parser.add_argument(
        "--with-gdb",
        dest="gdb",
        action="store_const",
        const=True,
        default=False,
        help="Run with gdb.",
    )
    parser.add_argument(
        "--gdb-init",
        type=str,
        required=False,
        help="Path to custom gdb init file if any.",
    )

    return parser.parse_known_args(args)


def _check_arg_dependencies(run_args):
    if run_args.gdb_init is not None and not run_args.gdb:
        raise ValueError("gdb-init requires --with-gdb flag.")


def finalize_run_args(run_args, unknown_args):
    _check_arg_dependencies(run_args)
    configuration = _get_project_config()

    isa, protocol, opt = _parse_build_opt(run_args.build_opt, configuration)
    base_dir = configuration["gem5_binary_base_dir"]

    command = f"{base_dir}/{isa.lower()}-{protocol.lower()}-{opt.lower()}-{run_args.build_name}/gem5.{opt}"
    if not run_args.outdir is None:
        outdir_base = configuration["gem5_out_base_dir"]
        command += f" -re --outdir={outdir_base}/{run_args.outdir}"
    if not run_args.debug_flags is None:
        command += f" --debug-flags={run_args.debug_flags}"
    if not run_args.debug_start is None:
        command += f" --debug-start={run_args.debug_start}"
    if not run_args.debug_end is None:
        command += f" --debug-end={run_args.debug_end}"
    if run_args.gdb:
        if run_args.gdb_init is not None:
            command = f"gdb -x {run_args.gdb_init} --args {command}"
        else:
            command = f"gdb --args {command}"
    if run_args.override:
        command = "M5_OVERRIDE_PY_SOURCE=true " + command
    command += f" {run_args.config}"
    for unknown_arg in unknown_args:
        command += f" {unknown_arg}"

    cwd = os.path.abspath(os.getcwd())

    return [(command, cwd)]
