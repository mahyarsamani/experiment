import argparse

from pathlib import Path
from warnings import warn

from ..common.cmd_util import run_command
from ..common.gem5_work import (
    BinaryOpt,
    gem5BuildConfiguration,
)


def parse_run_args(args):
    parser = argparse.ArgumentParser("Parse run command from helper.")
    parser.add_argument(
        "build_name",
        type=str,
        help="Build name to use for gem5 compilation.",
    )

    parser.add_argument("config", type=str, help="Config script to simulate.")

    parser.add_argument(
        "--binary_opt",
        type=str,
        help="Default binary option to use when compiling.",
        choices=BinaryOpt.return_all_values(),
        required=False,
    )
    parser.add_argument(
        "--outdir",
        type=str,
        help="Where to redirect gem5's output to relative to project's gem5-out-base-dir.",
        required=False,
    )
    parser.add_argument(
        "--debug-flags",
        type=str,
        help="Debug flags to pass to gem5.",
        required=False,
    )
    parser.add_argument(
        "--debug-file",
        type=str,
        help="File to output debug prints to.",
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


def _process_run_args(proj_config, run_args, unknown_args):
    def _check_arg_validity(run_args):
        if run_args.gdbinit is not None and not run_args.gdb:
            raise ValueError("gdb-init requires --with-gdb flag.")

    _check_arg_validity(run_args)
    path_config = proj_config.path_config()
    build_config = gem5BuildConfiguration.from_args_and_config(
        run_args, proj_config.build_config()
    )
    gem5_path = (
        path_config.gem5_binary_base_dir()
        / run_args.build_name
        / f"gem5.{build_config.binary_opt}"
    )
    if not gem5_path.exists():
        raise FileNotFoundError(
            f"gem5 binary not found at {gem5_path}. "
            "Please run the build command first."
        )

    command = f"{gem5_path}"
    if run_args.outdir is not None:
        outdir = path_config.gem5_out_base_dir() / run_args.outdir
        command += f" -re --outdir={outdir}"
    if run_args.debug_flags is not None:
        command += f" --debug-flags={run_args.debug_flags}"
    if run_args.debug_file is not None:
        debug_file = path_config.gem5_out_base_dir() / run_args.debug_file
        command += f" --debug-file={debug_file}"
    if run_args.debug_start is not None:
        command += f" --debug-start={run_args.debug_start}"
    if run_args.debug_end is not None:
        command += f" --debug-end={run_args.debug_end}"
    if run_args.gdb:
        if run_args.gdbinit is not None:
            command = f"gdb -x {run_args.gdbinit} --args {command}"
        else:
            command = f"gdb --args {command}"

    if run_args.gem5_resource_json is not None:
        command = (
            f"GEM5_RESOURCE_JSON_APPEND={run_args.gem5_resource_json} "
            + command
        )
    elif path_config.get_gem5_resource_json_path() is not None:
        command = (
            f"GEM5_RESOURCE_JSON_APPEND={path_config.get_gem5_resource_json_path()} "
            + command
        )
    else:
        warn(
            "GEM5_RESOURCE_JSON_APPEND not specified or defined by project configuration."
        )

    if run_args.override:
        command = "M5_OVERRIDE_PY_SOURCE=true " + command

    config = Path(run_args.config)
    if config.is_absolute():
        command += f" {config}"
    elif config.resolve().exists():
        command += f" {config.resolve()}"
    elif (path_config.project_dir() / config).exists():
        command += f" {(path_config.project_dir() / config).resolve()}"
    else:
        raise FileNotFoundError(
            "Tried looking for script as an absolute path, relative to your "
            f"cwd ({Path.cwd()}), and relative to project base directory "
            f"({path_config.project_dir()}). None of them exist."
        )

    for unknown_arg in unknown_args:
        command += f" {unknown_arg}"

    run_command(["bash", "-c", command], path_config.project_dir())
