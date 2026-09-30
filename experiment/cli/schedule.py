import argparse
import platform

from ..api.scheduler.console import ControlClient, run_console
from ..api.scheduler.daemon import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_POLLING_SECS,
    run_daemon,
    spawn_daemon,
)
from ..api.scheduler.state import InstanceLock
from ..common.log import error, info, warn


def parse_schedule_args(args):
    parser = argparse.ArgumentParser(
        prog="helper schedule",
        description="Start the scheduler in the background (or attach to "
        "the one already running) and open its console.",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=platform.node(),
        help="Name of the scheduler; its state is saved under this name.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=DEFAULT_DASHBOARD_PORT,
        help="Port for the dashboard (on localhost).",
    )
    parser.add_argument(
        "--polling-secs",
        type=float,
        default=DEFAULT_POLLING_SECS,
        help="How often to check on jobs.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Don't resume; archive the saved state and start empty.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run the scheduler in this terminal (logs to stderr) instead "
        "of in the background; attach with `helper console`.",
    )
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Start the scheduler but don't open a console.",
    )
    return parser.parse_known_args(args)


def _process_schedule_args(known_args, unknown_args) -> int:
    if unknown_args:
        error(f"unrecognized arguments: {' '.join(unknown_args)}")
        return 2
    settings = {
        "name": known_args.name,
        "dashboard_port": known_args.dashboard_port,
        "polling_secs": known_args.polling_secs,
    }

    client = ControlClient()
    if client.connect():
        holder = InstanceLock().holder() or {}
        info(
            f"A scheduler is already running (pid {holder.get('pid')}, "
            f"name {holder.get('name')}); attaching to it."
        )
        if known_args.fresh or known_args.foreground:
            warn("--fresh and --foreground are ignored when attaching.")
    elif known_args.foreground:
        return run_daemon(
            fresh=known_args.fresh, log_to_stderr=True, **settings
        )
    elif not spawn_daemon(fresh=known_args.fresh, **settings):
        return 1
    elif not client.connect():
        error("Started the scheduler but couldn't connect to it.")
        return 1

    if known_args.no_console:
        client.close()
        return 0
    return run_console(client, settings)


def parse_console_args(args):
    parser = argparse.ArgumentParser(
        prog="helper console",
        description="Attach a console to the running scheduler.",
    )
    return parser.parse_known_args(args)


def _process_console_args(known_args, unknown_args) -> int:
    if unknown_args:
        error(f"unrecognized arguments: {' '.join(unknown_args)}")
        return 2
    client = ControlClient()
    if not client.connect():
        error("No scheduler is running. Start one with `helper schedule`.")
        return 1
    return run_console(client, {"name": platform.node()})
