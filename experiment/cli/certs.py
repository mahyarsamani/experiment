import argparse
import platform
import socket

from pathlib import Path

from ..common import pki
from ..common.log import error, info


def parse_certs_args(args):
    parser = argparse.ArgumentParser(
        prog="helper certs",
        description="Manage the certificates that secure scheduler <-> "
        "worker connections. Files live in ~/.config/experiment/pki (or "
        "$EXPERIMENT_PKI_DIR).",
    )
    parser.add_argument(
        "--dir", type=Path, help="Use this directory instead of the default."
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="Create the certificate authority (once).")
    issue = sub.add_parser(
        "issue", help="Issue a certificate for a worker or a scheduler."
    )
    issue.add_argument("role", choices=pki.ROLES)
    issue.add_argument(
        "name",
        nargs="?",
        help="Certificate name (default: this machine's name for a worker, "
        "'scheduler' for a scheduler).",
    )
    issue.add_argument(
        "--san",
        action="append",
        default=[],
        help="Extra DNS name or IP the worker is reached at (repeatable). "
        "The `domain` of a Host must be one of the worker's names.",
    )
    issue.add_argument("--days", type=int, default=825)
    sub.add_parser("list", help="List certificates.")
    return parser.parse_known_args(args)


def _process_certs_args(known_args, unknown_args) -> int:
    if unknown_args:
        error(f"unrecognized arguments: {' '.join(unknown_args)}")
        return 2
    directory = known_args.dir or pki.pki_dir()
    try:
        if known_args.action == "init":
            path = pki.init_ca(directory)
            info(f"Created CA {path}.")
            info(
                "Keep ca.key private. Next: `helper certs issue scheduler` "
                "and `helper certs issue worker <name>` for each worker."
            )
        elif known_args.action == "issue":
            name = known_args.name
            sans = list(known_args.san)
            if name is None:
                if known_args.role == pki.WORKER:
                    name = platform.node()
                    fqdn = socket.getfqdn()
                    if fqdn not in (name, *sans):
                        sans.append(fqdn)
                else:
                    name = pki.SCHEDULER
            cert, key = pki.issue(
                known_args.role, name, sans, directory, known_args.days
            )
            info(f"Issued {cert} and {key}.")
            if known_args.role == pki.WORKER:
                info(f"Run the worker with: helper work --name {name}")
        else:
            for path in sorted(directory.glob("*.crt")):
                print(path)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        error(e)
        return 1
    return 0
