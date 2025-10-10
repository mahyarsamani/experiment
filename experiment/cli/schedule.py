from ..api.scheduler import Scheduler

import argparse

from rpyc.utils.server import ThreadedServer


def parse_schedule_args(args):
    parser = argparse.ArgumentParser(
        description="Spawn a scheduler server for rpyc."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9200,
        help="Port number for the scheduler server.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=9201,
        help="Port number for the dashboard server.",
    )
    parser.add_argument(
        "--polling-secs",
        type=int,
        default=240,
        help="Polling interval in seconds.",
    )

    return parser.parse_known_args(args)


def _process_schedule_args(known_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    scheduler = Scheduler(known_args.polling_secs, known_args.dashboard_port)
    server = ThreadedServer(
        scheduler,
        port=known_args.port,
        hostname="0.0.0.0",
    )

    scheduler.start_service()
    server.start()
