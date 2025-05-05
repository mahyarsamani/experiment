import argparse

from rpyc.utils.server import ThreadedServer

from ..api.worker import Worker


def parse_work_args(args):
    parser = argparse.ArgumentParser(
        description="Spawn a worker server for rpyc."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9100,
        help="Port number for the worker server.",
    )

    return parser.parse_known_args(args)


def finalize_work_args(known_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    server = ThreadedServer(Worker, port=known_args.port, hostname="0.0.0.0")
    server.start()
