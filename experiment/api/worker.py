import os
import psutil
import subprocess

from flask import abort, Flask, send_file
from pathlib import Path
from rpyc import Service
from threading import Thread
from typing import List

from .work import JobStatus


def _safe_route(app: Flask, rule: str):
    def deco(func):
        from functools import wraps

        @wraps(func)
        def wrapper(*a, **kw):
            try:
                return func(*a, **kw)
            except Exception:
                from flask import abort

                abort(500)

        app.add_url_rule(rule, func.__name__, wrapper, methods=["GET"])
        return wrapper

    return deco


class Worker(Service):
    def __init__(self, file_server_port: int) -> None:
        super().__init__()
        self._processes = dict()

        self._app = Flask(__name__)
        self._file_server_port = file_server_port
        self._file_server_thread = Thread(
            target=self.run_file_server, daemon=True
        )
        self._allowed_paths = list()

        self._initialize_file_routes()

    def run_file_server(self) -> None:
        self._app.run(
            host="0.0.0.0",
            port=self._file_server_port,
            debug=False,
            use_reloader=False,
        )

    def _initialize_file_routes(self) -> None:
        def _is_allowed(path: Path):
            return path in self._allowed_paths

        @_safe_route(self._app, "/files")
        def file_routes():
            from flask import request

            raw = request.args.get("path", "")
            if not raw:
                abort(400, "missing ?path")
            p = Path(raw)
            if not p.is_absolute():
                abort(400, "path must be absolute")
            if not _is_allowed(p):
                abort(403, "path not allowed")
            if not p.exists() or not p.is_file():
                abort(404)
            # Rely on send_file to stream efficiently
            return send_file(p, mimetype="text/plain")

    def start_service(self) -> None:
        self._file_server_thread.start()

    def on_connect(self, conn):
        print("Client connected")

    def on_disconnect(self, conn):
        print("Client disconnected")

    def exposed_launch_job(
        self, command: str, outdir_str: str, other_paths: List[str]
    ) -> int:
        def _initialize_files(outdir: Path) -> None:
            if not outdir.exists():
                outdir.mkdir(parents=True, exist_ok=True)

        outdir = Path(outdir_str)
        _initialize_files(outdir)
        try:
            pid = -1
            stdout, stderr = outdir / "stdout", outdir / "stderr"
            with open(stdout, "w") as out, open(stderr, "w") as err:
                pid = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    shell=True,
                    start_new_session=True,
                ).pid
            self._allowed_paths.extend(
                [stdout, stderr] + [Path(p) for p in other_paths]
            )
            return pid
        except:
            return -1

    def exposed_kill_job(self, pid: int) -> bool:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            return False
        except:
            return False

        return True

    def exposed_job_status(self, pid: int) -> str:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "exited"
        except:
            return "failed"

        return JobStatus.psutil_to_string(psutil.Process(pid).status())
