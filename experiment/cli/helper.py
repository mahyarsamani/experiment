import argparse
import subprocess


def parse_command_line():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(help="sub-command help", dest="command")
    init = subparsers.add_parser("init", description="Initiate a project.")
    build = subparsers.add_parser("build", description="Build gem5.")
    run = subparsers.add_parser("run", description="Run gem5.")

    parsed_args, for_subparser = parser.parse_known_args()
    if parsed_args.command == "init":
        from .init import parse_init_args, finalize_init_args

        known_args, unknown_args = parse_init_args(for_subparser)
        recipe = finalize_init_args(known_args, unknown_args)
    elif parsed_args.command == "build":
        from .build import parse_build_args, finalize_build_args

        known_args, unknown_args = parse_build_args(for_subparser)
        recipe = finalize_build_args(known_args, unknown_args)
    elif parsed_args.command == "run":
        from .run import parse_run_args, finalize_run_args

        known_args, unknown_args = parse_run_args(for_subparser)
        recipe = finalize_run_args(known_args, unknown_args)
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
