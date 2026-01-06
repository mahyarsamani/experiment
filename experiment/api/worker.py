import os
import psutil

import subprocess
import time

from dataclasses import dataclass
from flask import abort, Flask, send_file
from pathlib import Path
from rpyc import Service
from threading import Thread
from typing import List, Tuple


@dataclass
class JobInfo:
    pid: int
    create_time: float
    pgid: int
    popen: subprocess.Popen


def _safe_route(app: Flask, rule: str):
    def deco(func):
        from functools import wraps

        @wraps(func)
        def wrapper(*a, **kw):
            try:
                return func(*a, **kw)
            except Exception as e:
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
        self._jobs = dict()

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
                abort(400, "Missing ?path")
            p = Path(raw)
            if not p.is_absolute():
                abort(400, "path must be absolute")
            if not _is_allowed(p):
                abort(403, f"path {p} not allowed")
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
        self,
        cwd: str,
        command: str,
        outdir_str: str,
        other_paths: List[str],
        optional_dump: List[Tuple[str, str]],
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
                p = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    shell=True,
                    start_new_session=True,
                )
                pid = p.pid
                try:
                    proc = psutil.Process(pid)
                    ct = proc.create_time()
                except psutil.Error:
                    ct = time.time()
                try:
                    pgid = os.getpgid(pid)
                except ProcessLookupError:
                    pgid = pid
                self._jobs[pid] = JobInfo(
                    pid=pid, create_time=ct, pgid=pgid, popen=p
                )
            self._allowed_paths.extend(
                [stdout, stderr] + [Path(p) for p in other_paths]
            )
            self._allowed_paths.extend(
                [Path(path) for _, path in optional_dump]
            )
            for file_content, file_path in optional_dump:
                with open(file_path, "w") as dump:
                    dump.write(file_content)
            return pid
        except Exception as e:
            return -1

    def exposed_kill_job(self, pid: int, signal: int) -> bool:
        if pid <= 1:
            return False
        try:
            os.killpg(pid, signal)
        except Exception as e:
            print(e)
            return False

        return True

    def exposed_job_status(self, pid: int) -> str:
        def _proc_is_executing(proc: psutil.Process) -> bool:
            try:
                if not proc.is_running():
                    return False
                st = proc.status()
                return st not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                return False
            except psutil.AccessDenied:
                try:
                    os.kill(proc.pid, 0)
                    return True
                except OSError:
                    return False

        def _any_alive_in_pgid(pgid: int) -> bool:
            for p in psutil.process_iter(attrs=["pid"]):
                pid = p.info["pid"]
                try:
                    if os.getpgid(pid) == pgid:
                        if _proc_is_executing(psutil.Process(pid)):
                            return True
                except (ProcessLookupError, PermissionError):
                    continue
                except psutil.Error:
                    continue
            return False

        job = self._jobs.get(pid)
        if job is not None:
            rc = job.popen.poll()
            if rc is not None:
                return "running" if _any_alive_in_pgid(job.pgid) else "exited"
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - job.create_time) > 1.0:
                    self._jobs.pop(pid, None)
                    return "exited"
            except psutil.NoSuchProcess:
                return "running" if _any_alive_in_pgid(job.pgid) else "exited"
            except psutil.Error:
                pass

            return "running" if _any_alive_in_pgid(job.pgid) else "exited"

        try:
            p = psutil.Process(pid)
            return "running" if _proc_is_executing(p) else "exited"
        except psutil.NoSuchProcess:
            return "exited"
        except psutil.Error:
            try:
                os.kill(pid, 0)
                return "running"
            except OSError:
                return "exited"
