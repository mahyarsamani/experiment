import argparse
import time

from pathlib import Path

from ..api.simulate import RunSpec, Simulator
from ..common.log import info, warn
from ..common.gem5_work import (
    BinaryOpt,
    gem5BuildConfiguration,
)


def parse_run_args(args):
    parser = argparse.ArgumentParser(
        prog="helper run",
        description="Run gem5. Either give a build name and a config "
        "script, or --spec to rerun a scheduled job exactly as it ran.",
    )
    parser.add_argument(
        "build_name",
        type=str,
        nargs="?",
        help="Build name to use for gem5 compilation.",
    )

    parser.add_argument(
        "config", type=str, nargs="?", help="Config script to simulate."
    )

    parser.add_argument(
        "--spec",
        type=Path,
        help="A job's run spec (<job outdir>/.job/run.json) to rerun; its "
        "outputs go to a new directory unless --outdir is given.",
    )
    parser.add_argument(
        "--binary_opt",
        type=str,
        help="Binary option to run.",
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
        "--debug-start",
        dest="debug_start",
        type=int,
        help="Debug start tick to pass to gem5.",
        required=False,
    )
    parser.add_argument(
        "--debug-end",
        type=int,
        help="Debug end tick to pass to gem5.",
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
        default=None,
        required=False,
        help="Path to a gem5 resource JSON file to append (default: the "
        "project's, if it has one).",
    )

    return parser.parse_known_args(args)


def _find_config(path_config, config: str) -> Path:
    path = Path(config)
    if path.is_absolute():
        return path
    if path.resolve().exists():
        return path.resolve()
    if (path_config.project_dir() / path).exists():
        return (path_config.project_dir() / path).resolve()
    raise FileNotFoundError(
        "Tried looking for script as an absolute path, relative to your "
        f"cwd ({Path.cwd()}), and relative to project base directory "
        f"({path_config.project_dir()}). None of them exist."
    )


def _spec_from_args(proj_config, run_args, unknown_args) -> RunSpec:
    if run_args.build_name is None or run_args.config is None:
        raise ValueError("give a build name and a config script, or --spec")
    path_config = proj_config.path_config()
    build_config = gem5BuildConfiguration.from_args_and_config(
        run_args, proj_config.build_config()
    )
    env = {}
    if run_args.gem5_resource_json is not None:
        env["GEM5_RESOURCE_JSON_APPEND"] = run_args.gem5_resource_json
    elif path_config.gem5_resource_json_path() is not None:
        env["GEM5_RESOURCE_JSON_APPEND"] = str(
            path_config.gem5_resource_json_path()
        )
    else:
        warn(
            "GEM5_RESOURCE_JSON_APPEND not specified or defined by project configuration."
        )
    if run_args.override:
        env["M5_OVERRIDE_PY_SOURCE"] = "true"
    outdir = (
        str(path_config.gem5_out_base_dir() / run_args.outdir)
        if run_args.outdir is not None
        else None
    )
    return RunSpec(
        binary=str(
            path_config.gem5_binary_base_dir()
            / run_args.build_name
            / f"gem5.{build_config.binary_opt}"
        ),
        script=str(_find_config(path_config, run_args.config)),
        script_args=list(unknown_args),
        outdir=outdir,
        redirect=outdir is not None,
        debug_flags=run_args.debug_flags,
        debug_start=run_args.debug_start,
        debug_end=run_args.debug_end,
        env=env,
        cwd=str(path_config.project_dir()),
    )


def _spec_from_job(run_args, unknown_args) -> RunSpec:
    """The job's spec, with the debugging options given on the command
    line, writing to a new directory so the job's outputs stay intact."""
    if run_args.build_name or run_args.config or unknown_args:
        raise ValueError(
            "--spec reruns a job as it ran; it takes no build name, config "
            "script or script arguments"
        )
    spec = RunSpec.load(run_args.spec)
    if run_args.outdir is not None:
        spec.outdir = str(Path(run_args.outdir).resolve())
    else:
        spec.outdir = str(
            Path(spec.outdir or Path.cwd())
            / f"rerun-{time.strftime('%Y%m%d-%H%M%S')}"
        )
    for option in ("debug_flags", "debug_start", "debug_end"):
        if getattr(run_args, option) is not None:
            setattr(spec, option, getattr(run_args, option))
    if run_args.override:
        spec.env["M5_OVERRIDE_PY_SOURCE"] = "true"
    if run_args.gem5_resource_json is not None:
        spec.env["GEM5_RESOURCE_JSON_APPEND"] = run_args.gem5_resource_json
    info(f"Rerunning {run_args.spec}; outputs go to {spec.outdir}")
    return spec


def _process_run_args(proj_config, run_args, unknown_args) -> int:
    if run_args.gdbinit is not None and not run_args.gdb:
        raise ValueError("gdb-init requires --with-gdb flag.")
    if run_args.spec is not None:
        spec = _spec_from_job(run_args, unknown_args)
    else:
        spec = _spec_from_args(proj_config(), run_args, unknown_args)
    return Simulator(spec).run(gdb=run_args.gdb, gdbinit=run_args.gdbinit)
