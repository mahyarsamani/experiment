import argparse
import logging
import platform
import socket

from pathlib import Path

from rpyc.utils.server import ThreadedServer

from ..api.worker import PROTOCOL_CONFIG, Worker
from ..common import pki
from ..common.log import error, info, warn


def parse_work_args(args):
    parser = argparse.ArgumentParser(
        prog="helper work",
        description="Run a worker that a scheduler can launch jobs on. "
        "Connections use mutual TLS; see `helper certs`.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9100,
        help="Port to listen on.",
    )
    parser.add_argument(
        "--bind",
        default="0.0.0.0",
        help="Address to listen on (default: all interfaces).",
    )
    parser.add_argument(
        "--name",
        default=platform.node(),
        help="Certificate name to use: <pki dir>/<name>.crt and .key "
        "(default: this machine's name).",
    )
    parser.add_argument("--cert", type=Path, help="Worker certificate.")
    parser.add_argument("--key", type=Path, help="Worker private key.")
    parser.add_argument(
        "--ca", type=Path, help="CA that signed the scheduler's certificate."
    )
    parser.add_argument(
        "--insecure-localhost",
        action="store_true",
        help="No TLS; only listen on 127.0.0.1. For testing on one machine.",
    )
    return parser.parse_known_args(args)


def _process_work_args(known_args, unknown_args) -> int:
    if unknown_args:
        error(f"unrecognized arguments: {' '.join(unknown_args)}")
        return 2

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    authenticator = None
    bind = known_args.bind
    if known_args.insecure_localhost:
        bind = "127.0.0.1"
        warn(
            "Running without TLS. Anyone with a shell on this machine can "
            "launch commands as you through this worker."
        )
    else:
        default_cert, default_key = pki.paths(known_args.name)
        cert = known_args.cert or default_cert
        key = known_args.key or default_key
        ca = known_args.ca or pki.ca_path()
        missing = [path for path in (cert, key, ca) if not path.exists()]
        if missing:
            error(
                f"Missing {', '.join(map(str, missing))}. Create them with:\n"
                "  helper certs init            # once, anywhere\n"
                f"  helper certs issue worker {known_args.name} "
                f"--san {socket.getfqdn()}\n"
                "or pass --cert/--key/--ca."
            )
            return 1
        authenticator = pki.RoleAuthenticator(cert, key, ca, pki.SCHEDULER)

    server = ThreadedServer(
        Worker(),
        hostname=bind,
        port=known_args.port,
        authenticator=authenticator,
        protocol_config=PROTOCOL_CONFIG,
        logger=logging.getLogger("worker"),
    )
    info(
        f"Worker listening on {bind}:{known_args.port}"
        + ("" if authenticator is None else " (mutual TLS)")
    )
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0
