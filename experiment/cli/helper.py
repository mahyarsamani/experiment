import argparse
import sys

from ..common.log import error, install_warning_format

COMMANDS = {
    "initialize": "Initialize a project.",
    "build": "Build gem5.",
    "run": "Run gem5.",
    "work": "Run a worker that the scheduler launches jobs on.",
    "schedule": "Start (or attach to) the scheduler and open its console.",
    "console": "Attach a console to the running scheduler.",
    "certs": "Manage certificates for scheduler <-> worker connections.",
}


def _dispatch(command: str, args: list[str]) -> int | None:
    if command == "initialize":
        from .initialize import parse_initialize_args, _process_initialize_args

        return _process_initialize_args(*parse_initialize_args(args))
    if command == "build":
        from ..common.config_util import _get_project_config
        from .build import parse_build_args, _process_build_args

        return _process_build_args(
            _get_project_config(), *parse_build_args(args)
        )
    if command == "run":
        from ..common.config_util import _get_project_config
        from .run import parse_run_args, _process_run_args

        return _process_run_args(_get_project_config(), *parse_run_args(args))
    if command == "work":
        from .work import parse_work_args, _process_work_args

        return _process_work_args(*parse_work_args(args))
    if command == "schedule":
        from .schedule import parse_schedule_args, _process_schedule_args

        return _process_schedule_args(*parse_schedule_args(args))
    if command == "console":
        from .schedule import parse_console_args, _process_console_args

        return _process_console_args(*parse_console_args(args))
    if command == "certs":
        from .certs import parse_certs_args, _process_certs_args

        return _process_certs_args(*parse_certs_args(args))
    return None


def main_function():
    install_warning_format()
    parser = argparse.ArgumentParser(prog="helper", add_help=False)
    parser.add_argument("command", nargs="?")
    parsed_args, for_subparser = parser.parse_known_args()

    if parsed_args.command in COMMANDS:
        try:
            sys.exit(_dispatch(parsed_args.command, for_subparser) or 0)
        except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as e:
            error(e)
            sys.exit(1)
        except KeyboardInterrupt:
            sys.exit(130)

    print("usage: helper <command> [args]\n\ncommands:")
    for command, description in COMMANDS.items():
        print(f"  {command:<12} {description}")
    print("\nRun `helper <command> -h` for help on a command.")
    sys.exit(0 if parsed_args.command in (None, "-h", "--help") else 2)
