import os
import json
import shutil
import argparse
import platform
import subprocess

from .configuration import (
    _get_project_config,
    _get_settings_list,
    _get_default_settings,
    _get_automate_settings,
)

isa_translator = {"x86": "X86", "arm": "ARM", "riscv": "RISCV"}

protocol_translator = {
    "chi": "CHI",
    "mesi_two_level": "MESI_Two_Level",
    "mesi_three_level": "MESI_Three_Level",
    "mi_example": "MI_example",
}

binary_opt_translator = {"debug": "debug", "opt": "opt", "fast": "fast"}

kvm_support = {"aarch64": "arm", "x86_64": "x86"}

translators = [
    (isa_translator, "isa"),
    (protocol_translator, "protocol"),
    (binary_opt_translator, "binary_opt"),
]


def _parse_build_opt(build_opt, configuration):
    ret = {}
    if not build_opt is None:
        build_opts = build_opt.split("-")
        for opt in build_opts:
            for translator, key in translators:
                if opt in translator:
                    if key in ret:
                        raise RuntimeError(
                            f"More than one option passed for {key}."
                        )
                    ret[key] = translator[opt]

    for translator, key in translators:
        if not key in ret:
            default = translator[configuration[f"default_{key}"]]
            print(f"No {key} specified. Using {default} for {key}.")
            ret[key] = default

    return ret["isa"], ret["protocol"], ret["binary_opt"]


def finalize_init_args(init_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    configuration = {}
    settings = _get_settings_list()
    defaults = _get_default_settings()
    automate = _get_automate_settings()

    for setting in settings:
        if not getattr(init_args, setting) is None:
            configuration[setting] = getattr(init_args, setting)
        elif setting in defaults:
            print(
                f"Setting {setting} not specified. "
                f"Defaulting to {defaults[setting]}."
            )
            configuration[setting] = defaults[setting]
        elif setting in automate:
            configuration[setting] = automate[setting]()
        else:
            raise RuntimeError(f"Don't know what to do with {setting}.")

    with open("project_config.json", "w") as config_file:
        json.dump(configuration, config_file, indent=2)

    return []


def finalize_build_args(build_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    configuration = _get_project_config()

    isa, protocol, opt = _parse_build_opt(build_args.build_opt, configuration)

    if build_args.threads is None:
        print(
            f"Number of threads not specified. "
            "Using 7/8 of available cores by default."
        )
        threads = int(os.cpu_count() * 7 / 8) or 1
    else:
        threads = build_args.threads

    gem5_dir = configuration["gem5_dir"]
    project_name = configuration["project_name"]
    base_dir = configuration["gem5_binary_base_dir"]

    path_parts = base_dir.split("/")[1:]
    assert os.path.exists(f"/{path_parts[0]}")
    assert os.access(f"/{path_parts[0]}", os.W_OK and os.R_OK)

    current_path = f"/{path_parts[0]}"
    for path_part in path_parts[1:] + [project_name]:
        test_path = os.path.join(current_path, path_part)
        if not os.path.exists(test_path):
            print(f"Path {test_path} does not exist, calling mkdir.")
            os.mkdir(test_path)
        else:
            print(f"Path {test_path} already exists.")
        current_path = test_path

    build_dir = os.path.join(
        current_path, f"{isa.lower()}-{protocol.lower()}-{opt.lower()}"
    )
    build_config = os.path.join(build_dir, "gem5.build")
    if os.path.exists(build_config):
        print(f"Path {build_config} already exists. Deleting it.")
        shutil.rmtree(build_config)
    else:
        print(f"Path {build_config} does not exist.")

    command = (
        f'scons setconfig {build_dir} BUILD_ISA="y" '
        f'USE_{isa}_ISA="y" RUBY="y" RUBY_PROTOCOL_{protocol}="y"'
    )
    if not build_args.bits_per_set is None:
        command += f" NUMBER_BITS_PER_SET={build_args.bits_per_set}"
    if platform.machine() in kvm_support:
        command += f" USE_KVM=y KVM_ISA={kvm_support[platform.machine()]}"
    ret = [(command, gem5_dir)]

    command = f"scons {build_dir}/gem5.{opt} -j {threads}"
    if build_args.gold:
        command += " --linker=gold"
    if build_args.gprof:
        command += " --gprof"
    ret += [(command, gem5_dir)]

    if not build_args.no_link:
        ret += [(f"ln -sf {current_path} build", gem5_dir)]
    return ret


def finalize_run_args(run_args, unknown_args):
    assert run_args.command == "run"
    # assert not (
    #     (not run_args.outdir is None) and (not run_args.experiment is None)
    # )

    configuration = _get_project_config()

    isa, protocol, opt = _parse_build_opt(run_args.build_opt, configuration)
    base_dir = configuration["gem5_binary_base_dir"]
    project_name = configuration["project_name"]

    command = f"{base_dir}/{project_name}/{isa.lower()}-{protocol.lower()}-{opt.lower()}/gem5.{opt}"
    if not run_args.outdir is None:
        outdir_base = configuration["gem5_out_base_dir"]
        command += (
            f" -re --outdir={outdir_base}/{project_name}/{run_args.outdir}"
        )
    # if not run_args.experiment is None:
    #     outdir_base = configuration["gem5_out_base_dir"]
    #     addition = f" -re --outdir={outdir_base}/{project_name}/"
    #     for arg in unknown_args:
    #         addition += f"{arg}/"
    if not run_args.debug_flags is None:
        command += f" --debug-flags={run_args.debug_flags}"
    if not run_args.debug_start is None:
        command += f" --debug-start={run_args.debug_start}"
    if not run_args.debug_start is None:
        command += f" --debug-end={run_args.debug_end}"
    if run_args.gdb:
        command = "gdb --args " + command
    if run_args.override:
        command = "M5_OVERRIDE_PY_SOURCE=true " + command
    command += f" {run_args.config}"
    for unknown_arg in unknown_args:
        command += f" {unknown_arg}"

    cwd = os.path.abspath(os.getcwd())
    return [(command, cwd)]


def finalize_help_args(help_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)
    assert help_args.command == "help"

    configuration = _get_project_config()

    isa, protocol, opt = _parse_build_opt(help_args.build_opt, configuration)
    base_dir = configuration["gem5_binary_base_dir"]
    project_name = configuration["project_name"]

    command = f"{base_dir}/{project_name}/{isa.lower()}-{protocol.lower()}-{opt.lower()}/gem5.{opt}"
    if help_args.option == "gem5":
        command += " --help"
    if help_args.option == "debug":
        command += " --debug-help"

    cwd = os.path.abspath(os.getcwd())
    return [(command, cwd)]


def parse_command_line():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(help="sub-command help", dest="command")
    init = subparsers.add_parser("init", description="Initiate a project.")
    init.add_argument(
        "gem5_binary_base_dir",
        type=str,
        help="Path to the directory to store the gem5 binaries. "
        "Typically shared among projects.",
    )
    init.add_argument(
        "gem5_out_base_dir",
        type=str,
        help="Path to the directory to store the gem5 outputs "
        "(simout, simerr, stats, ...). Typically shared among projects.",
    )
    init.add_argument(
        "--project-name", type=str, help="Name of the project.", required=False
    )
    init.add_argument(
        "--default-isa",
        type=str,
        help="Default isa for the project.",
        required=False,
        choices=["x86", "arm", "riscv"],
    )
    init.add_argument(
        "--default-protocol",
        type=str,
        help="Default protocol for the project.",
        required=False,
        choices=["chi", "mesi_two_level", "mesi_three_level", "mi_example"],
    )
    init.add_argument(
        "--default-binary-opt",
        type=str,
        help="Default binary option to use.",
        required=False,
        choices=["debug", "opt", "fast"],
    )
    init.add_argument(
        "--gem5-dir",
        type=str,
        help="Path to the gem5 directory.",
        required=False,
    )

    build = subparsers.add_parser("build", description="Build gem5")
    build.add_argument(
        "--build-opt",
        type=str,
        help="Build option to build gem5 with.",
        required=False,
    )
    build.add_argument(
        "--threads",
        type=int,
        help="Number of threads to use to build gem5.",
        required=False,
    )
    build.add_argument(
        "--bits-per-set",
        type=int,
        help="Number of bits per sit to use for Ruby compilation.",
        required=False,
    )
    build.add_argument(
        "--gold",
        dest="gold",
        action="store_const",
        const=True,
        default=False,
        help="Whether to use gold linker or not.",
    )
    build.add_argument(
        "--gprof",
        dest="gprof",
        action="store_const",
        const=True,
        default=False,
        help="Whether to compile gem5 with gprof.",
    )
    build.add_argument(
        "--no-link",
        dest="no_link",
        action="store_const",
        const=True,
        default=False,
        help="Whether to create a soft link to build after compilation.",
    )

    run = subparsers.add_parser("run", description="Run gem5")
    run.add_argument("config", type=str, help="Config script to simulate.")
    run.add_argument(
        "--build-opt",
        type=str,
        help="gem5 Build option to run.",
        required=False,
    )
    run.add_argument(
        "--override-py",
        dest="override",
        action="store_const",
        const=True,
        default=False,
        help="Override m5 python source.",
    )
    run.add_argument(
        "--with-gdb",
        dest="gdb",
        action="store_const",
        const=True,
        default=False,
        help="Run with gdb.",
    )
    run.add_argument(
        "--outdir", type=str, help="gem5's output directory.", required=False
    )
    run.add_argument(
        "--debug-flags",
        type=str,
        help="Debug flags to pass to gem5.",
        required=False,
    )
    run.add_argument(
        "--debug-start",
        dest="debug_start",
        type=int,
        help="Debug start tick to pass to gem5.",
        required=False,
    )
    run.add_argument(
        "--debug-end",
        type=int,
        help="Debug start tick to pass to gem5.",
        required=False,
    )

    help = subparsers.add_parser("help", description="Build gem5")
    help.add_argument(
        "--build-opt",
        type=str,
        help="gem5 Build option to run.",
        required=False,
    )
    help.add_argument(
        "option",
        type=str,
        choices=["gem5", "debug"],
        help="Which help to print gem5 or gem5-debug",
    )

    known_args, unknown_args = parser.parse_known_args()
    if known_args.command == "init":
        recipe = finalize_init_args(known_args, unknown_args)
    elif known_args.command == "build":
        recipe = finalize_build_args(known_args, unknown_args)
    elif known_args.command == "run":
        recipe = finalize_run_args(known_args, unknown_args)
    elif known_args.command == "help":
        recipe = finalize_help_args(known_args, unknown_args)
    else:
        error = "To use this script please refer to this usage.\n"
        error += "\thelper cmd [args]\n"
        error += "Here are your choices for cmd.\n"
        for cmd, cmd_parser in subparsers._name_parser_map.items():
            error += f"{cmd}: {cmd_parser.description}\n"
        error += "You can see the help prompt for each cmd option by using:\n"
        error += "\thelper cmd"
        print(error)
        exit()
    return recipe


def main_function():
    recipe = parse_command_line()
    for command, cwd in recipe:
        print(f"Running {command} in {cwd}")
        subprocess.run(
            command,
            shell=True,
            cwd=cwd,
        )
