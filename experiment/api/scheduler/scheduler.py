import argparse
import hashlib
import importlib.util
import logging
import requests
import shlex
import time


from dataclasses import dataclass
from enum import Enum
from flask import (
    abort,
    Flask,
    jsonify,
    render_template,
    request,
    Response,
    stream_with_context,
)
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Queue
from threading import RLock, Thread
from typing import Any, Dict, List, Iterable
from urllib.parse import quote
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server


from ..host import healthy, Host
from ..work import Job, Experiment


START_DELAY = 2


def console_print(to_print: Any):
    print(f"info: {to_print}")


def console_warn(to_warn: Any):
    print(f"warn: {to_warn}")


def console_error(to_err: Any):
    print(f"error: {to_err}")


class Scheduler:
    class JobSignal(Enum):
        TERM = "term"
        INT = "int"
        QUIT = "quit"
        KILL = "kill"
        RESET = "reset"

        def signal_value(self):
            return {
                self.TERM: 15,
                self.INT: 2,
                self.QUIT: 3,
                self.KILL: 9,
                self.RESET: -1,
            }[self]

        def handle(self, job: Job, host: Host) -> str:
            if self.signal_value() != -1:
                res = host.kill_job(job, self.signal_value())
                return (
                    f"Success sending signal {self.signal_value()} to {job.id()} ({job.shorthand_command()}) running on {host.name()}"
                    if res.ok()
                    else f"Sending signal {self.signal_value()} to {job.id()} ({job.shorthand_command()}) on {host.name()} raised {res.message()}"
                )
            else:
                return (
                    f"Success clearing {job.id()} ({job.shorthand_command()})"
                    if job.clear()
                    else f"Failed to clear job {job.id()} ({job.shorthand_command()})"
                )

    @dataclass
    class DashboardSignal:
        experiment: str
        job_id: str
        host: str
        pid: int
        signal: str

    def __init__(self, name: str, dashboard_port: int, polling_secs: int):
        self._name = name
        self._dashboard_port = dashboard_port
        self._polling_secs = polling_secs

        self._title = f"Scheduler Dashboard {name}:{dashboard_port}"

        self._hosts = list()
        self._hosts_pending_removal = list()
        self._hosts_lock = RLock()

        self._experiments = list()
        self._experiments_pending_removal = list()
        self._experiments_drained = list()
        self._experiments_lock = RLock()

        self._dashboard_app = Flask(
            __name__,
            template_folder="templates",
            static_folder="static",
            static_url_path="/static",
        )
        self._dashboard_server = make_server(
            "localhost",
            self._dashboard_port,
            self._dashboard_app,
            threaded=True,
        )
        self._dashboard_signals = Queue()
        self._dashboard_messages = Queue()

        self._stop = False

        self._console = Thread(
            target=self._run_console, name=f"{self._name}.console"
        )
        self._scheduler = Thread(
            target=self._run_scheduler, name=f"{self._name}.scheduler"
        )
        self._dashboard = Thread(
            target=self._dashboard_server.serve_forever,
            name=f"{self._name}.dashboard",
        )

        self._setup_api_routes()

        self._setup_loggers()

    def start(self):
        self._console.start()
        self._scheduler.start()
        self._dashboard.start()

    def stop(self):
        self._dashboard_server.shutdown()
        self._stop = True

    def _setup_api_routes(self):
        @self._dashboard_app.get("/")
        def index():
            return render_template("base.html", title=self._title)

        @self._dashboard_app.get("/api/state")
        def api_state():
            with self._experiments_lock, self._hosts_lock:
                return jsonify(
                    {
                        "title": self._title,
                        "hosts": [host.name() for host in self._hosts],
                        "jobs": [
                            job.view()
                            for experiment in self._experiments
                            for job in experiment.jobs()
                        ],
                        "last_update_epoch": time.time(),
                    }
                )

        @self._dashboard_app.get("/health")
        def health():
            return {"ok": True, "title": self._title}, 200

        @self._dashboard_app.errorhandler(HTTPException)
        def _http_error(e: HTTPException):
            return (
                jsonify({"ok": False, "error": e.description or e.name}),
                e.code,
            )

        @self._dashboard_app.errorhandler(Exception)
        def _unhandled_error(e: Exception):
            return (
                jsonify({"ok": False, "error": "internal server error"}),
                500,
            )

        @self._dashboard_app.post("/api/job_action")
        def api_job_action():
            data = request.get_json(silent=True) or {}

            action = data.get("action")

            for k in ("job_id", "pid", "experiment", "host"):
                if k not in data:
                    abort(400, f"missing {k}")

            if action == "signal" and data.get("signal") not in {
                "TERM",
                "INT",
                "QUIT",
                "KILL",
                "RESET",
            }:
                abort(400, "invalid signal")

            self._dashboard_signals.put(
                Scheduler.DashboardSignal(
                    experiment=data["experiment"],
                    job_id=data["job_id"],
                    host=data["host"],
                    pid=data["pid"],
                    signal=data["signal"],
                )
            )

            return jsonify(
                {
                    "ok": True,
                    "received": data,
                    "server_epoch": time.time(),
                }
            )

        @self._dashboard_app.get("/files")
        def proxy_files():
            host_dom = request.args.get("host", "")
            raw_path = request.args.get("path", "")

            if not host_dom or not raw_path:
                abort(400, "missing host or path")

            p = Path(raw_path)
            if not p.is_absolute():
                abort(400, "path must be absolute")

            # Build the worker URL and stream it back
            worker_url = f"http://{host_dom}/files?path={quote(str(p))}"
            try:
                r = requests.get(worker_url, stream=True, timeout=10)
            except requests.RequestException:
                abort(502, "upstream worker unreachable")

            # Mirror status & content-type; stream the body
            headers = {}
            ct = r.headers.get("Content-Type")
            if ct:
                headers["Content-Type"] = ct

            def generate():
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            return Response(
                stream_with_context(generate()),
                status=r.status_code,
                headers=headers,
            )

    def _setup_loggers(self):
        def _get_logger(
            name: str, log_file: Path, log_level: int | str
        ) -> logging.Logger:
            assert log_file.exists() and log_file.is_file()

            logger = logging.getLogger(name)
            logger.setLevel(log_level)
            logger.propagate = False
            logger.handlers.clear()

            handler = RotatingFileHandler(
                log_file.as_posix(),
                maxBytes=10 * 1024 * 1024,  # 10 MiB
                backupCount=5,
                encoding="utf-8",
                delay=True,  # don't open until first emit
            )
            handler.setLevel(log_level)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s "
                    "[%(process)d:%(threadName)s] %(name)s: %(message)s"
                )
            )
            logger.addHandler(handler)

            return logger

        dashboard_log_file = Path(f"{self._name}.dashboard.log").resolve()
        dashboard_log_file.touch()
        scheduler_log_file = Path(f"{self._name}.scheduler.log").resolve()
        scheduler_log_file.touch()

        self._dashboard_logger = _get_logger(
            "werkzeug", dashboard_log_file, logging.DEBUG
        )

        self._scheduler_logger = _get_logger(
            f"{self._name}.scheduler", scheduler_log_file, logging.DEBUG
        )

    def _add_experiments(self, new_experiments: List[Experiment]) -> None:
        with self._experiments_lock:
            for new_experiment in new_experiments:
                if new_experiment.name() in [
                    experiment.name() for experiment in self._experiments
                ]:
                    console_warn(
                        f"{new_experiment.name()} already added! "
                        "Not adding again."
                    )
                elif new_experiment.name() in [
                    experiment.name()
                    for experiment in self._experiments_pending_removal
                ]:
                    console_warn(
                        f"{new_experiment.name()} is pending removal. "
                        "Please wait for it to be "
                        "removed and then try adding again."
                    )
                elif new_experiment.name() in [
                    experiment.name()
                    for experiment in self._experiments_drained
                ]:
                    console_warn(f"{new_experiment.name()} already drained!")
                else:
                    self._experiments.append(new_experiment)

    def _add_hosts(self, new_hosts: List[Host]) -> None:
        with self._hosts_lock:
            for new_host in new_hosts:
                if new_host.name() in [host.name() for host in self._hosts]:
                    console_warn(
                        f"{new_host.name()} already added! Won't add again."
                    )
                elif new_host.name() in [
                    host.name() for host in self._hosts_pending_removal
                ]:
                    console_warn(
                        f"new_host.name() is pending removal. "
                        "Please wait for it to be removed and add again."
                    )
                else:
                    if not (res := new_host.connect()).ok():
                        console_error(
                            f"Connecting to {new_host.name()} "
                            f"raised {res.message()}."
                        )
                    else:
                        self._hosts.append(new_host)

    def _process(self, script_path: str):
        def _extract_hosts_and_experiments(
            script_path: Path,
        ) -> tuple[list[Host], list[Experiment]]:
            def _load_module_from_path(path: Path):
                path = path.resolve()
                if not path.exists():
                    raise FileNotFoundError(path)
                if path.suffix != ".py":
                    raise ValueError(f"Expected a .py file, got: {path}")

                mod_name = (
                    f"_plugin_{hashlib.sha1(str(path).encode()).hexdigest()}"
                )
                spec = importlib.util.spec_from_file_location(
                    mod_name, str(path)
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"Could not load spec for {path}")

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module

            def _walk(obj: Any, *, seen: set[int]) -> Iterable[Any]:
                oid = id(obj)
                if oid in seen:
                    return
                seen.add(oid)

                yield obj

                if obj is None or isinstance(
                    obj, (str, bytes, bytearray, int, float, bool, Path)
                ):
                    return

                if isinstance(obj, dict):
                    for v in obj.values():
                        yield from _walk(v, seen=seen)
                    return

                if isinstance(obj, (list, tuple, set, frozenset)):
                    for v in obj:
                        yield from _walk(v, seen=seen)
                    return

            def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
                out: list[Any] = []
                seen_ids: set[int] = set()
                for it in items:
                    if id(it) not in seen_ids:
                        seen_ids.add(id(it))
                        out.append(it)
                return out

            hosts = list()
            experiments = list()
            seen = set()
            for _, top_level_value in vars(
                _load_module_from_path(script_path)
            ).items():
                for x in _walk(top_level_value, seen=seen):
                    if isinstance(x, Host):
                        hosts.append(x)
                    elif isinstance(x, Experiment):
                        experiments.append(x)

            return _dedupe_preserve_order(hosts), _dedupe_preserve_order(
                experiments
            )

        hosts, experiments = _extract_hosts_and_experiments(Path(script_path))

        console_print(f"Found the following in {Path(script_path).resolve()}:")
        console_print(f"Hosts: {hosts}")
        console_print(f"Experiments: {experiments}")

        self._add_hosts(hosts)
        self._add_experiments(experiments)

    def _list_experiments(self) -> Dict[str, List[Experiment]]:
        with self._experiments_lock:
            console_print(
                {
                    "experiments": [
                        str(experiment) for experiment in self._experiments
                    ],
                    "experiments_pending_removal": [
                        str(experiment)
                        for experiment in self._experiments_pending_removal
                    ],
                }
            )

    def _list_hosts(self) -> Dict[str, List[Host]]:
        with self._hosts_lock:
            console_print(
                {
                    "hosts": [str(host) for host in self._hosts],
                    "hosts_pending_removal": [
                        str(host) for host in self._hosts_pending_removal
                    ],
                }
            )

    def _kill_experiment(self, experiment_name: str) -> None:
        with self._experiments_lock:
            if experiment_name in [
                experiment.name()
                for experiment in self._experiments_pending_removal
            ]:
                console_warn(
                    f"{experiment_name} already pending "
                    "removal. Won't do anything."
                )
            elif experiment_name not in [
                experiment.name() for experiment in self._experiments
            ]:
                console_error(f"{experiment_name} does not exist!")
            else:
                to_kill = next(
                    (
                        experiment
                        for experiment in self._experiments
                        if experiment.name() == experiment_name
                    )
                )
                self._experiments_pending_removal.append(to_kill)
                self._experiments.remove(to_kill)

    def _kill_host(self, host_name: str) -> None:
        with self._hosts_lock:
            if host_name in [
                host.name() for host in self._hosts_pending_removal
            ]:
                console_warn(
                    f"{host_name} already pending removal. Won't do anything."
                )
            elif host_name not in [host.name() for host in self._hosts]:
                console_error(f"{host_name} does not exist!")
            else:
                to_kill = next(
                    (host for host in self._hosts if host.name() == host_name)
                )
                self._hosts_pending_removal.append(to_kill)
                self._hosts.remove(to_kill)

    def _run_console(self):
        def build_parser() -> argparse.ArgumentParser:
            parser = argparse.ArgumentParser(
                prog="", add_help=True, exit_on_error=False
            )
            subparsers = parser.add_subparsers(dest="command", required=True)

            process_parser = subparsers.add_parser(
                "process", aliases=["p"], exit_on_error=False
            )
            process_parser.add_argument(
                "script", type=str, help="Path to python script to process."
            )

            list_parser = subparsers.add_parser(
                "list", aliases=["l"], exit_on_error=False
            )
            list_parser.add_argument("kind", choices=["experiment", "host"])

            kill_parser = subparsers.add_parser(
                "kill", aliases=["k"], exit_on_error=False
            )
            kill_parser.add_argument("kind", choices=["experiment", "host"])
            kill_parser.add_argument(
                "name", help="Name of the object to kill."
            )

            subparsers.add_parser("stop", exit_on_error=False)

            return parser

        def process_cmd(
            parser: argparse.ArgumentParser, line: str
        ) -> argparse.Namespace | None:
            line = line.strip()
            if not line:
                return None  # ignore empty lines

            argv = shlex.split(line)
            return parser.parse_args(argv)

        def dispatch(args):
            if args.command in ["process", "p"]:
                self._process(args.script)
            if args.command in ["list", "l"]:
                if args.kind == "experiment":
                    self._list_experiments()
                if args.kind == "host":
                    self._list_hosts()
            if args.command in ["kill", "k"]:
                if args.kind == "experiment":
                    self._kill_experiment(args.name)
                if args.kind == "host":
                    self._kill_host(args.name)
            if args.command == "stop":
                self.stop()

        parser = build_parser()
        time.sleep(START_DELAY)

        while not self._stop:
            try:
                args = process_cmd(parser, input("> "))
                if args is None:
                    continue
                dispatch(args)
            except argparse.ArgumentError as e:
                console_print(e)
            except SystemExit:
                pass
            except Exception as e:
                console_print(e)

    def _run_scheduler(self):
        def _get_candidate(experiments: List[Experiment], capacity: int):
            candidates = sorted(
                [
                    candidate
                    for candidate in [
                        experiment.candidate(capacity)
                        for experiment in experiments
                    ]
                    if candidate is not None
                ],
                key=lambda j: j.demand(),
                reverse=True,
            )
            return candidates[0] if len(candidates) > 0 else None

        while not self._stop:
            with self._experiments_lock, self._hosts_lock:
                while not self._dashboard_signals.empty():
                    req = self._dashboard_signals.get()
                    experiment = next(
                        (
                            experiment
                            for experiment in self._experiments
                            if experiment.name() == req.experiment
                        ),
                        None,
                    )
                    job = next(
                        (
                            job
                            for job in experiment.jobs()
                            if job.id() == req.job_id
                        ),
                        None,
                    )
                    host = next(
                        (h for h in self._hosts if h.name() == req.host), None
                    )

                    signal = Scheduler.JobSignal(req.signal)
                    if (
                        experiment is None
                        or job is None
                        or host is None
                        or job.pid() != req.pid
                        or signal is None
                    ):
                        message = (
                            f"Couldn't handle signal for experiment {req.experiment}, job {req.job_id}, host {req.host} with pid {req.pid} and signal {req.signal}.\n"
                            f"Found experiment: {experiment}, job: {job}, host: {host}, signal {signal}."
                        )
                        self._dashboard_messages.put(message)
                        self._scheduler_logger.warning(message)
                        continue

                    message = signal.handle(job, host)
                    self._dashboard_messages.put(message)
                    self._scheduler_logger.info(message)

                # NOTE: Update healthy hosts.
                for host in healthy(self._hosts + self._hosts_pending_removal):
                    host.update()

                # NOTE: kill experiments.
                for experiment in self._experiments_pending_removal:
                    safe_to_remove = True
                    for host in healthy(
                        self._hosts + self._hosts_pending_removal
                    ):
                        if not (res := host.kill_experiment(experiment)).ok():
                            self._scheduler_logger.warning(
                                f"Killing {experiment.name()} on "
                                f"{host.name()} raised {res.message()}"
                            )
                            safe_to_remove &= False
                        else:
                            self._scheduler_logger.info(
                                f"Killed {experiment.name()} on {host.name()}."
                            )
                    experiment.set_safe_to_remove(safe_to_remove)

                self._experiments_pending_removal = [
                    experiment
                    for experiment in self._experiments_pending_removal
                    if not experiment.safe_to_remove()
                ]

                self._hosts_pending_removal = [
                    host
                    for host in healthy(self._hosts_pending_removal)
                    if not host.idle()
                ]

                found_work = True
                while found_work:

                    found_work = False
                    self._hosts.sort(
                        key=lambda host: host.capacity(), reverse=True
                    )
                    for host in healthy(self._hosts):
                        job = _get_candidate(
                            self._experiments, host.capacity()
                        )
                        if job is not None:
                            found_work |= True
                            if not (res := host.launch_job(job)).ok():
                                self._scheduler_logger.warning(
                                    f"Launching {job} on {host.name()} "
                                    f"raised {res.message()}"
                                )
                            else:
                                self._scheduler_logger.info(
                                    f"Launched {job} on {host.name()}."
                                )

                self._hosts = [
                    host for host in self._hosts if not host.failed()
                ]
                self._hosts_pending_removal = [
                    host
                    for host in self._hosts_pending_removal
                    if not host.failed()
                ]
            time.sleep(self._polling_secs)
