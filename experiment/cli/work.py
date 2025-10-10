from ..api.worker import Worker

import argparse

from rpyc.utils.server import ThreadedServer


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
    parser.add_argument(
        "--file-server-port",
        type=int,
        default=9101,
        help="Port number for the file server.",
    )

    return parser.parse_known_args(args)


def _process_work_args(known_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    worker = Worker(known_args.file_server_port)
    server = ThreadedServer(worker, port=known_args.port, hostname="0.0.0.0")
    worker.start_service()
    server.start()
