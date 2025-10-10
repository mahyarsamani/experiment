from ..common.config_util import _get_project_config
import argparse


def main_function():
    parser = argparse.ArgumentParser()
    subparser = parser.add_subparsers(help="sub-command help", dest="command")
    initialize = subparser.add_parser(
        "initialize", description="Initialize a project."
    )
    build = subparser.add_parser("build", description="Build gem5.")
    run = subparser.add_parser("run", description="Run gem5.")
    work = subparser.add_parser(
        "work", description="Spawn a worker server for rpyc."
    )
    schedule = subparser.add_parser(
        "schedule", description="Spawn a scheduler server for rpyc."
    )

    parsed_args, for_subparser = parser.parse_known_args()
    if parsed_args.command == "initialize":
        from .initialize import parse_initialize_args, _process_initialize_args

        known_args, unknown_args = parse_initialize_args(for_subparser)
        _process_initialize_args(known_args, unknown_args)
    elif parsed_args.command == "build":
        from .build import parse_build_args, _process_build_args

        known_args, unknown_args = parse_build_args(for_subparser)
        _process_build_args(_get_project_config(), known_args, unknown_args)
    elif parsed_args.command == "run":
        from .run import parse_run_args, _process_run_args

        known_args, unknown_args = parse_run_args(for_subparser)
        _process_run_args(_get_project_config(), known_args, unknown_args)
    elif parsed_args.command == "work":
        from .work import parse_work_args, _process_work_args

        known_args, unknown_args = parse_work_args(for_subparser)
        _process_work_args(known_args, unknown_args)
    elif parsed_args.command == "schedule":
        from .schedule import parse_schedule_args, _process_schedule_args

        known_args, unknown_args = parse_schedule_args(for_subparser)
        _process_schedule_args(known_args, unknown_args)
    else:
        error = "To use this script please refer to this usage.\n"
        error += "\thelper cmd [args]\n"
        error += "Here are your choices for cmd.\n"
        for cmd, cmd_parser in subparser._name_parser_map.items():
            error += f"{cmd}: {cmd_parser.description}\n"
        error += "You can see the help prompt for each cmd option by using:\n"
        error += "\thelper cmd"
        print(error)
        exit()
