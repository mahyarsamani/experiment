"""Interactive console for a running scheduler daemon.

The console is a separate process that talks to the daemon over its Unix
socket, so it can crash, be closed (Ctrl-D) or be opened in several
terminals without affecting the scheduler. If the daemon goes away the
console stays open, keeps trying to reconnect, and offers `start`.
"""

import argparse
import json
import os
import shlex
import threading
import time

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import rpyc

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

from ..host import remote_message
from ..work import JobStatus
from ..worker import PROTOCOL_CONFIG
from .daemon import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_POLLING_SECS,
    spawn_daemon,
)
from .filters import JobFilter, format_time
from .state import InstanceLock, socket_path

SIGNALS = ("term", "int", "quit", "kill")
STATUSES = tuple(status.value for status in JobStatus)
SHORT_ID = 8

_COLORS = {"info": "", "warn": "\033[33m", "error": "\033[31m"}
_RESET = "\033[0m"


class ConsoleError(Exception):
    """Something the user should see, without a traceback."""


class Disconnected(ConsoleError):
    pass


class _HelpShown(Exception):
    pass


# ---- Talking to the daemon ------------------------------------------------


class ControlClient:
    def __init__(self) -> None:
        self._conn = None
        self._lock = threading.RLock()
        self._snapshot: dict | None = None
        self._snapshot_at = 0.0

    def connect(self) -> bool:
        with self._lock:
            if self._conn is not None:
                return True
            try:
                self._conn = rpyc.utils.factory.unix_connect(
                    str(socket_path()), config=PROTOCOL_CONFIG
                )
            except (OSError, EOFError):
                self._conn = None
                return False
            self._snapshot = None
            return True

    def connected(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None

    def _call(self, method: str, *args):
        with self._lock:
            if self._conn is None:
                raise Disconnected("not connected to the scheduler")
            try:
                return getattr(self._conn.root, method)(*args)
            except Exception as e:
                if hasattr(e, "_remote_tb"):
                    message = remote_message(e)
                    raise ConsoleError(message.split(": ", 1)[-1]) from None
                self.close()
                raise Disconnected(
                    "lost the connection to the scheduler"
                ) from None

    def execute(self, name: str, **kwargs) -> list[str]:
        lines = json.loads(self._call("execute", name, json.dumps(kwargs)))
        self._snapshot = None
        return lines

    def snapshot(self, max_age: float = 1.0) -> dict:
        if self._snapshot is None or time.time() - self._snapshot_at > max_age:
            self._snapshot = json.loads(self._call("snapshot"))
            self._snapshot_at = time.time()
        return self._snapshot

    def events(self, since: int) -> list:
        return json.loads(self._call("events", since))

    def last_event(self) -> int:
        return int(self._call("last_event"))


# ---- Parsing and completion -----------------------------------------------


class ConsoleParser(argparse.ArgumentParser):
    """argparse that never exits the process."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("exit_on_error", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str):
        if not self.prog:
            # NOTE: The top-level usage lists every command; `help` is nicer.
            raise ConsoleError(f"{message} (see `help`)")
        raise ConsoleError(f"{message}\nusage: {self.format_usage().strip()[7:]}")

    def exit(self, status: int = 0, message: str | None = None):
        if message:
            raise ConsoleError(message.strip())
        raise _HelpShown()


def _arg(parser, *names, completer: str | None = None, **kwargs):
    action = parser.add_argument(*names, **kwargs)
    action.completer = completer
    return action


def build_parser() -> tuple[ConsoleParser, dict[str, ConsoleParser]]:
    parser = ConsoleParser(prog="", add_help=False)
    sub = parser.add_subparsers(
        dest="command", required=True, parser_class=ConsoleParser
    )
    commands: dict[str, ConsoleParser] = {}

    def add(name: str, help: str, aliases: Iterable[str] = ()):
        p = sub.add_parser(
            name, help=help, description=help, aliases=list(aliases), prog=name
        )
        commands[name] = p
        for alias in aliases:
            commands[alias] = p
        return p

    p = add("load", "Load hosts and experiments from a Python script.", ["p"])
    _arg(p, "script", completer="path", help="Path to the script.")

    p = add(
        "reload",
        "Run a loaded script again; add new experiments and new jobs.",
    )
    _arg(p, "script", completer="path", help="Path to the script.")

    add("hosts", "List hosts.")
    add("experiments", "List experiments.", ["exps"])

    def filters(p, experiment: bool = True):
        if experiment:
            _arg(p, "--experiment", completer="experiment")
        _arg(p, "--host", completer="host")
        _arg(
            p,
            "--status",
            action="append",
            choices=STATUSES,
            help="Repeat for several statuses.",
        )
        _arg(p, "--since", help="Started at or after: YYYY-MM-DD [HH:MM].")
        _arg(p, "--until", help="Started before: YYYY-MM-DD [HH:MM].")
        _arg(p, "--on", help="Started on this day: YYYY-MM-DD.")
        _arg(p, "--match", help="Text in the job id or command.")

    def confirm(p):
        _arg(p, "-y", "--yes", action="store_true", help="Don't ask first.")

    p = add("jobs", "List jobs, optionally filtered.")
    _arg(p, "experiment", nargs="?", completer="experiment")
    filters(p, experiment=False)
    _arg(p, "--full", action="store_true", help="Show full ids and commands.")

    p = add(
        "signal",
        "Send a signal to running jobs: give job ids, or filters "
        "(e.g. `signal kill --on 2026-10-09`).",
    )
    _arg(p, "signal", choices=SIGNALS)
    _arg(p, "jobs", nargs="*", completer="job", help="Job ids (or prefixes).")
    filters(p)
    confirm(p)

    p = add("kill", "Kill all jobs of an experiment and remove it.")
    _arg(p, "experiment", completer="experiment")

    p = add(
        "reset",
        "Queue finished jobs again: give job ids, or filters "
        "(e.g. `reset --experiment e --status failed`).",
    )
    _arg(p, "jobs", nargs="*", completer="job")
    filters(p)
    _arg(
        p,
        "--force",
        action="store_true",
        help="Also requeue jobs whose host is unreachable (they may still "
        "be running there).",
    )
    confirm(p)

    p = add(
        "delete",
        "Forget jobs that aren't running (output files stay on disk): give "
        "job ids or filters; `--experiment E` alone deletes the experiment; "
        "`--script S` deletes a script's jobs and stops loading it.",
    )
    _arg(p, "jobs", nargs="*", completer="job")
    _arg(p, "--script", completer="script", help="A loaded script.")
    filters(p)
    confirm(p)

    p = add("undelete", "Stop skipping deleted jobs or an experiment.")
    _arg(p, "jobs", nargs="*", completer="deleted_job")
    _arg(p, "--experiment", completer="deleted_experiment")

    p = add("drain", "Stop giving new jobs to a host.")
    _arg(p, "host", completer="host")
    p = add("undrain", "Give new jobs to a drained host again.")
    _arg(p, "host", completer="host")
    p = add("remove", "Remove a host once its running jobs finish.")
    _arg(p, "host", completer="host")
    _arg(
        p,
        "--force",
        action="store_true",
        help="Remove it now and stop tracking its jobs.",
    )

    p = add("capacity", "Change a host's capacity: +N, -N or =N.", ["c"])
    _arg(p, "host", completer="host")
    _arg(p, "change")

    p = add("events", "Show recent scheduler events.")
    _arg(p, "-n", type=int, default=20, help="How many.")

    add("info", "Show the dashboard URL, state file and logs.")
    p = add("stop", "Stop the scheduler (jobs keep running by default).")
    _arg(p, "--kill-jobs", action="store_true", help="Kill all jobs first.")
    add("start", "Start the scheduler again if it is not running.")
    p = add("help", "Show help for all commands or one command.")
    _arg(p, "topic", nargs="?", completer="command")
    add("exit", "Leave the console; the scheduler keeps running.", ["quit"])
    return parser, commands


def _takes_value(action: argparse.Action) -> bool:
    return action.nargs != 0


class ConsoleCompleter(Completer):
    def __init__(
        self,
        commands: dict[str, ConsoleParser],
        names: Callable[[str], list[tuple[str, str]]],
    ) -> None:
        self._commands = commands
        self._names = names
        self._paths = PathCompleter(
            expanduser=True,
            file_filter=lambda name: os.path.isdir(name) or name.endswith(".py"),
        )

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        if text and not text[-1].isspace() and words:
            current = words.pop()
        else:
            current = ""

        if not words:
            for name, parser in self._commands.items():
                if name.startswith(current):
                    yield Completion(
                        name,
                        start_position=-len(current),
                        display_meta=parser.description or "",
                    )
            return

        parser = self._commands.get(words[0])
        if parser is None:
            return
        positionals = [a for a in parser._actions if not a.option_strings]
        options = {
            option: action
            for action in parser._actions
            for option in action.option_strings
            if option not in ("-h", "--help")
        }

        if current.startswith("-"):
            for option, action in options.items():
                if option.startswith(current):
                    yield Completion(
                        option,
                        start_position=-len(current),
                        display_meta=action.help or "",
                    )
            return

        expecting = None
        index = 0
        for word in words[1:]:
            if expecting is not None:
                expecting = None
            elif word in options:
                if _takes_value(options[word]):
                    expecting = options[word]
            elif index < len(positionals):
                if positionals[index].nargs not in ("+", "*"):
                    index += 1
        action = expecting or (
            positionals[index] if index < len(positionals) else None
        )
        if action is None:
            return
        yield from self._values(action, current, complete_event)

    def _values(self, action: argparse.Action, current: str, complete_event):
        if action.choices:
            for choice in action.choices:
                if str(choice).startswith(current):
                    yield Completion(str(choice), start_position=-len(current))
            return
        kind = getattr(action, "completer", None)
        if kind == "path":
            yield from self._paths.get_completions(
                Document(current, len(current)), complete_event
            )
        elif kind == "command":
            for name in self._commands:
                if name.startswith(current):
                    yield Completion(name, start_position=-len(current))
        elif kind is not None:
            for value, meta in self._names(kind):
                if value.startswith(current):
                    yield Completion(
                        value, start_position=-len(current), display_meta=meta
                    )


# ---- Output ---------------------------------------------------------------


def table(headers: list[str], rows: list[list], max_width: int = 60) -> str:
    rows = [
        [str(cell) if cell is not None else "" for cell in row] for row in rows
    ]
    rows = [
        [cell if len(cell) <= max_width else cell[: max_width - 1] + "…"
         for cell in row]
        for row in rows
    ]
    widths = [
        max([len(header)] + [len(row[i]) for row in rows])
        for i, header in enumerate(headers)
    ]
    lines = ["  ".join(h.upper().ljust(w) for h, w in zip(headers, widths))]
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in rows]
    return "\n".join(line.rstrip() for line in lines)


def short_ids(ids: list[str]) -> dict[str, str]:
    """Shortest prefix (at least SHORT_ID long) that is unique among ids."""
    result = {}
    ordered = sorted(ids)
    for i, job_id in enumerate(ordered):
        length = SHORT_ID
        for neighbor in ordered[max(0, i - 1) : i] + ordered[i + 1 : i + 2]:
            common = len(os.path.commonprefix([job_id, neighbor]))
            length = max(length, common + 1)
        result[job_id] = job_id[:length]
    return result


def format_event(event) -> str:
    _, stamp, level, message = event
    when = datetime.fromtimestamp(stamp).strftime("%H:%M:%S")
    color = _COLORS.get(level, "")
    tag = f"{color}{level}{_RESET}" if color else level
    return f"[{when}] {tag}: {message}"


# ---- The console ------------------------------------------------------------


class Console:
    def __init__(self, client: ControlClient, restart_args: dict) -> None:
        self._client = client
        self._restart_args = dict(restart_args)
        self._parser, self._commands = build_parser()
        self._last_seq = 0
        self._stop = threading.Event()
        self._told_not_running = False
        self._name = restart_args.get("name", "scheduler")
        self._session: PromptSession | None = None

    # -- completion data --

    def _names(self, kind: str) -> list[tuple[str, str]]:
        try:
            snapshot = self._client.snapshot()
        except ConsoleError:
            return []
        if kind == "experiment":
            return [
                (e["name"], f"{e['jobs']} jobs, {e['state']}")
                for e in snapshot["experiments"]
            ]
        if kind == "host":
            return [
                (h["name"], f"{h['state']}, {h['used']}/{h['capacity']}")
                for h in snapshot["hosts"]
            ]
        if kind == "job":
            jobs = snapshot["jobs"]
            ids = short_ids([job["id"] for job in jobs])
            return [
                (ids[job["id"]], f"{job['status']}: {job['command']}")
                for job in jobs
            ]
        info = snapshot["info"]
        if kind == "script":
            return [
                (path, f"{info.get('script_jobs', {}).get(path, 0)} jobs")
                for path in info.get("scripts", [])
            ]
        if kind == "deleted_job":
            deleted = info.get("deleted_jobs", [])
            ids = short_ids(deleted)
            return [(ids[job_id], "deleted") for job_id in deleted]
        if kind == "deleted_experiment":
            return [
                (name, "deleted")
                for name in info.get("deleted_experiments", [])
            ]
        return []

    # -- background: events and reconnects --

    def _on_connected(self) -> None:
        snapshot = self._client.snapshot(max_age=0)
        info = snapshot["info"]
        self._name = info.get("name", self._name)
        self._restart_args.update(
            name=self._name,
            dashboard_port=info.get("dashboard_port", DEFAULT_DASHBOARD_PORT),
            polling_secs=info.get("polling_secs", DEFAULT_POLLING_SECS),
        )
        self._told_not_running = False

    def _watch(self) -> None:
        delay = 1.0
        while not self._stop.wait(delay):
            if self._client.connected():
                try:
                    for event in self._client.events(self._last_seq):
                        self._last_seq = event[0]
                        print(format_event(event))
                    delay = 1.0
                except Disconnected:
                    print(
                        "error: lost the connection to the scheduler; "
                        "reconnecting..."
                    )
                except ConsoleError:
                    pass
                continue
            if self._client.connect():
                try:
                    self._on_connected()
                    self._last_seq = self._client.last_event()
                    print("info: reconnected to the scheduler.")
                except ConsoleError:
                    self._client.close()
                delay = 1.0
                continue
            if not self._told_not_running and InstanceLock().is_free():
                self._told_not_running = True
                print(
                    "warn: the scheduler is not running. Type `start` to "
                    "start it again (it resumes from its saved state)."
                )
            delay = min(delay * 2, 5.0)

    def _prompt(self):
        if self._client.connected():
            return HTML(f"<ansigreen>{self._name}</ansigreen>&gt; ")
        return HTML("<ansired>[disconnected]</ansired>&gt; ")

    # -- commands --

    def _print_hosts(self, _args) -> None:
        hosts = self._client.snapshot(max_age=0)["hosts"]
        print(
            table(
                [
                    "host",
                    "isa",
                    "domain",
                    "state",
                    "mode",
                    "used",
                    "jobs",
                    "error",
                ],
                [
                    [
                        h["name"],
                        h.get("isa", ""),
                        h["domain"],
                        h["state"],
                        h["mode"],
                        f"{h['used']}/{h['capacity']}",
                        h["jobs"],
                        h["error"] or "",
                    ]
                    for h in hosts
                ],
            )
            if hosts
            else "no hosts"
        )

    def _print_experiments(self, _args) -> None:
        experiments = self._client.snapshot(max_age=0)["experiments"]
        print(
            table(
                ["experiment", "state", "jobs", "status"],
                [
                    [
                        e["name"],
                        e["state"],
                        e["jobs"],
                        ", ".join(
                            f"{count} {status}"
                            for status, count in sorted(e["counts"].items())
                        ),
                    ]
                    for e in experiments
                ],
            )
            if experiments
            else "no experiments"
        )

    def _print_jobs(self, args) -> None:
        jobs = self._client.snapshot(max_age=0)["jobs"]
        ids = short_ids([job["id"] for job in jobs])
        jobs = self._filter(args, args.experiment).select(jobs)
        if not jobs:
            print("no matching jobs")
            return
        width = 10_000 if args.full else 60
        print(
            table(
                [
                    "id",
                    "status",
                    "rc",
                    "host",
                    "started",
                    "ended",
                    "experiment",
                    "command",
                ],
                [
                    [
                        job["id"] if args.full else ids[job["id"]],
                        job["status"],
                        job["returncode"],
                        job["host"],
                        format_time(job.get("start_time")),
                        format_time(job.get("end_time")),
                        job["experiment"],
                        job["full_command"] if args.full else job["command"],
                    ]
                    for job in jobs
                ],
                max_width=width,
            )
        )
        print(f"{len(jobs)} job(s)")

    def _print_events(self, args) -> None:
        events = self._client.events(0)[-max(1, args.n) :]
        for event in events:
            print(format_event(event))
        if not events:
            print("no events")

    def _print_info(self, _args) -> None:
        info = self._client.snapshot(max_age=0)["info"]
        started = datetime.fromtimestamp(info["started_at"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        print(f"scheduler   {info.get('name')} (pid {info.get('pid')}), started {started}")
        print(f"dashboard   {info.get('dashboard_url')}")
        print(f"state file  {info.get('state_file')}")
        print(f"logs        {info.get('log_dir')}")
        for script in info.get("scripts", []):
            print(f"script      {script}")

    def _start(self, _args) -> None:
        if self._client.connected() or self._client.connect():
            print("the scheduler is already running")
            return
        if spawn_daemon(**self._restart_args) and self._client.connect():
            self._on_connected()
            self._last_seq = 0

    def _help(self, args) -> None:
        if args.topic:
            parser = self._commands.get(args.topic)
            if parser is None:
                raise ConsoleError(f"no command {args.topic!r}")
            print(parser.format_help().rstrip())
            return
        seen = set()
        for name, parser in self._commands.items():
            if id(parser) in seen:
                continue
            seen.add(id(parser))
            aliases = [
                alias
                for alias, p in self._commands.items()
                if p is parser and alias != name
            ]
            label = name + (f" ({', '.join(aliases)})" if aliases else "")
            print(f"  {label:<18} {parser.description}")
        print("Tab completes commands, paths, and experiment/host/job names.")

    def _execute(self, name: str, **kwargs) -> None:
        lines = self._client.execute(name, **kwargs)
        # NOTE: Bulk actions report each skipped job; keep it readable.
        if len(lines) > 12:
            lines = lines[:10] + [f"... {len(lines) - 11} more"] + lines[-1:]
        for line in lines:
            print(line)

    def _confirm(self, question: str) -> bool:
        ask = self._session.prompt if self._session else input
        return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")

    @staticmethod
    def _filter(args, experiment: str | None = None) -> JobFilter:
        return JobFilter.from_args(
            experiment=experiment,
            host=args.host,
            statuses=args.status or (),
            since=args.since,
            until=args.until,
            on=args.on,
            text=args.match,
        )

    def _select(self, args, verb: str) -> list[str] | None:
        """Job ids from `args.jobs` plus whatever the filters match.
        Returns None if the user declined the confirmation."""
        selector = self._filter(args, getattr(args, "experiment", None))
        ids = list(args.jobs)
        if not selector.empty():
            jobs = self._client.snapshot(max_age=0)["jobs"]
            matched = [job["id"] for job in selector.select(jobs)]
            if not matched and not ids:
                raise ConsoleError("no jobs match these filters")
            if len(matched) > 1 and not args.yes:
                if not self._confirm(f"{verb} {len(matched)} jobs?"):
                    return None
            ids += matched
        if not ids:
            raise ConsoleError("give job ids or filters (see `help`)")
        return ids

    def _delete(self, args) -> None:
        if args.script:
            script = Path(args.script).expanduser().resolve()
            if not args.yes and not self._confirm(
                f"delete script {script} and its jobs?"
            ):
                return
            self._execute("delete", script=str(script))
            return
        only_experiment = (
            args.experiment is not None
            and not args.jobs
            and self._filter(args).empty()
        )
        if only_experiment:
            if not args.yes and not self._confirm(
                f"delete experiment {args.experiment} and all its jobs?"
            ):
                return
            self._execute("delete", experiment=args.experiment)
            return
        ids = self._select(args, "delete")
        if ids is not None:
            self._execute("delete", jobs=ids)

    def dispatch(self, args) -> bool:
        """Run one command. Returns False when the console should exit."""
        command = args.command
        canonical = {"p": "load", "exps": "experiments", "c": "capacity",
                     "quit": "exit"}.get(command, command)
        local = {
            "help": self._help,
            "start": self._start,
        }
        if canonical == "exit":
            return False
        if canonical in local:
            local[canonical](args)
            return True
        if not self._client.connected():
            raise ConsoleError(
                "not connected to the scheduler"
                + ("; type `start` to start it" if InstanceLock().is_free() else "")
            )
        views = {
            "hosts": self._print_hosts,
            "experiments": self._print_experiments,
            "jobs": self._print_jobs,
            "events": self._print_events,
            "info": self._print_info,
        }
        if canonical in views:
            views[canonical](args)
        elif canonical in ("load", "reload"):
            script = Path(args.script).expanduser().resolve()
            self._execute(canonical, script=str(script))
        elif canonical == "signal":
            ids = self._select(args, f"send {args.signal} to")
            if ids is not None:
                self._execute("signal", jobs=ids, signal=args.signal)
        elif canonical == "kill":
            self._execute("kill", experiment=args.experiment)
        elif canonical == "reset":
            ids = self._select(args, "reset")
            if ids is not None:
                self._execute("reset", jobs=ids, force=args.force)
        elif canonical == "delete":
            self._delete(args)
        elif canonical == "undelete":
            self._execute(
                "undelete", jobs=args.jobs, experiment=args.experiment
            )
        elif canonical in ("drain", "undrain"):
            self._execute(canonical, host=args.host)
        elif canonical == "remove":
            self._execute("remove", host=args.host, force=args.force)
        elif canonical == "capacity":
            self._execute("capacity", host=args.host, change=args.change)
        elif canonical == "stop":
            self._execute("stop", kill_jobs=args.kill_jobs)
            return False
        return True

    def run(self) -> int:
        history = Path.home() / ".local" / "state" / "experiment"
        history.mkdir(parents=True, exist_ok=True)
        session = PromptSession(
            history=FileHistory(str(history / "console_history")),
            completer=ConsoleCompleter(self._commands, self._names),
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=True,
        )
        # NOTE: A plain session (no completion or history) for y/N questions.
        self._session = PromptSession()
        if self._client.connected():
            self._on_connected()
            self._last_seq = self._client.last_event()
        watcher = threading.Thread(target=self._watch, daemon=True)
        with patch_stdout(raw=True):
            if self._client.connected():
                self._print_info(None)
                print("Type `help` for commands. Ctrl-D leaves the console.")
            watcher.start()
            try:
                while True:
                    try:
                        line = session.prompt(self._prompt, refresh_interval=1.0)
                    except KeyboardInterrupt:
                        continue
                    except EOFError:
                        break
                    if not line.strip():
                        continue
                    try:
                        args = self._parser.parse_args(shlex.split(line))
                        if not self.dispatch(args):
                            break
                    except _HelpShown:
                        pass
                    except argparse.ArgumentError as e:
                        if e.argument_name == "command":
                            word = shlex.split(line)[0]
                            print(f"error: unknown command {word!r} (see `help`)")
                        else:
                            print(f"error: {e}")
                    except ConsoleError as e:
                        print(f"error: {e}")
                    except ValueError as e:
                        # NOTE: shlex.split on unbalanced quotes.
                        print(f"error: {e}")
                    except TimeoutError:
                        print("error: the scheduler did not answer in time")
                    except Exception as e:
                        print(f"error: {type(e).__name__}: {e}")
            finally:
                self._stop.set()
        self._client.close()
        return 0


def run_console(client: ControlClient, restart_args: dict) -> int:
    return Console(client, restart_args).run()
