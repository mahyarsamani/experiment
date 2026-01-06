import argparse
import platform

from ..api.scheduler.scheduler import Scheduler


def parse_schedule_args(args):
    parser = argparse.ArgumentParser(
        description="Spawn a scheduler server for rpyc."
    )
    parser.add_argument(
        "--name",
        type=str,
        default=platform.node(),
        help="Name you want to give the scheduler.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=9200,
        help="Port number for the dashboard server.",
    )
    parser.add_argument(
        "--polling-secs",
        type=int,
        default=1,
        help="Polling interval in seconds.",
    )

    return parser.parse_known_args(args)


def _process_schedule_args(known_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    scheduler = Scheduler(
        known_args.name, known_args.dashboard_port, known_args.polling_secs
    )
    scheduler.start()
