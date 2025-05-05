from ..util.config_util import _get_project_config
from ..util.project_config import (
    BinaryOpt,
    CompileConfiguration,
    ISA,
    Linker,
    PathConfiguration,
    Protocol,
)

import argparse
import subprocess


def parse_run_args(args):
    parser = argparse.ArgumentParser("Parse run command from helper.")
    parser.add_argument("config", type=str, help="Config script to simulate.")
    parser.add_argument(
        "--build-name",
        type=str,
        help="Build name to use for gem5 compilation.",
        required=False,
    )
    parser.add_argument(
        "--isas",
        type=str,
        help="Comma separated list of isas to compile by default. "
        f"Choose from: {ISA.return_all_values()}",
        required=False,
    )
    parser.add_argument(
        "--protocols",
        type=str,
        help="Comma separated list of isas to compile by default. "
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
        "--with-gdb",
        dest="gdb",
        action="store_const",
        const=True,
        default=False,
        help="Run with gdb.",
    )
    parser.add_argument(
        "--gdbinit",
        type=str,
        required=False,
        help="Path to custom gdb init file if any.",
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
        "--gem5-resource-json",
        type=str,
        default="/dev/null",
        required=False,
        help="Path to the gem5 resource JSON file.",
    )

    return parser.parse_known_args(args)


def finalize_run_args(run_args, unknown_args):
    def _check_arg_dependencies(run_args):
        if run_args.gdbinit is not None and not run_args.gdb:
            raise ValueError("gdb-init requires --with-gdb flag.")

    _check_arg_dependencies(run_args)
    proj_config = _get_project_config()
    compile_config = CompileConfiguration.from_args_and_config(
        run_args, proj_config.compile_config
    )
    path_config = PathConfiguration.from_args_and_config(
        run_args, proj_config.path_config
    )

    build_dir = (
        path_config.gem5_binary_base_dir
        / compile_config.make_build_dir_name(run_args)
    )
    gem5_path = build_dir / f"gem5.{compile_config.binary_opt}"
    if not gem5_path.exists():
        raise FileNotFoundError(
            f"gem5 binary not found at {gem5_path}. "
            "Please run the build command first."
        )

    command = f"{gem5_path}"
    if not run_args.outdir is None:
        outdir = (
            proj_config.path_config.gem5_out_base_dir / f"{run_args.outdir}"
        )
        command += f" -re --outdir={outdir}"
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
    if path_config.gem5_resource_json_path != PathConfiguration.NULL_PATH:
        command = (
            f"GEM5_RESOURCE_JSON_APPEND={path_config.gem5_resource_json_path} "
            + command
        )
    if run_args.override:
        command = "M5_OVERRIDE_PY_SOURCE=true " + command

    command += f" {run_args.config}"
    for unknown_arg in unknown_args:
        command += f" {unknown_arg}"

    subprocess.run(["bash", "-c", command], cwd=path_config.project_dir)
