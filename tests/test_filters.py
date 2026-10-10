"""Job filters and the console commands that use them."""

from datetime import datetime

import pytest

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from experiment.api.scheduler.console import ConsoleCompleter, build_parser
from experiment.api.scheduler.filters import JobFilter, parse_time


def at(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()


JOBS = [
    {"id": "aa01", "experiment": "sweep", "host": "perle", "status": "running",
     "command": "gem5 run.py 1", "full_command": "x", "start_time": at("2026-10-09 03:00")},
    {"id": "aa02", "experiment": "sweep", "host": "azacca", "status": "failed",
     "command": "gem5 run.py 2", "full_command": "x", "start_time": at("2026-10-09 23:59")},
    {"id": "bb01", "experiment": "smoke", "host": "perle", "status": "queued",
     "command": "gem5 smoke.py", "full_command": "x", "start_time": None},
    {"id": "bb02", "experiment": "smoke", "host": "perle", "status": "exited",
     "command": "gem5 smoke.py", "full_command": "x", "start_time": at("2026-10-10 00:00")},
]


def ids(selector: JobFilter) -> list[str]:
    return [job["id"] for job in selector.select(JOBS)]


def test_each_filter():
    assert ids(JobFilter.from_args(experiment="smoke")) == ["bb01", "bb02"]
    assert ids(JobFilter.from_args(host="azacca")) == ["aa02"]
    assert ids(JobFilter.from_args(statuses=["running", "failed"])) == [
        "aa01", "aa02"
    ]
    assert ids(JobFilter.from_args(text="SMOKE")) == ["bb01", "bb02"]
    assert ids(JobFilter.from_args(text="aa0")) == ["aa01", "aa02"]


def test_start_date_filters():
    # NOTE: A whole local day, end exclusive; never-started jobs never match.
    assert ids(JobFilter.from_args(on="2026-10-09")) == ["aa01", "aa02"]
    assert ids(JobFilter.from_args(since="2026-10-09 12:00")) == [
        "aa02", "bb02"
    ]
    assert ids(JobFilter.from_args(until="2026-10-09 12:00")) == ["aa01"]
    assert ids(
        JobFilter.from_args(on="2026-10-09", statuses=["running"], host="perle")
    ) == ["aa01"]


def test_empty_filter_and_bad_dates():
    assert JobFilter.from_args().empty()
    assert not JobFilter.from_args(on="2026-10-09").empty()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_time("yesterday")


@pytest.mark.parametrize(
    "line",
    [
        "signal kill --on 2026-10-09 --status running -y",
        "reset --experiment sweep --status failed --status killed",
        "delete aa01 aa02",
        "delete --experiment sweep",
        "delete --script run_many.py -y",
        "delete --host perle --since 2026-10-01",
        "undelete aa01 --experiment sweep",
        "jobs sweep --host perle --match run.py",
    ],
)
def test_console_accepts_new_commands(line):
    parser, _ = build_parser()
    parser.parse_args(line.split())


def complete(text):
    _, commands = build_parser()
    names = {
        "script": [("/p/run_many.py", "8 jobs")],
        "deleted_job": [("aa01", "deleted")],
        "host": [("perle", "")],
    }
    completer = ConsoleCompleter(commands, lambda kind: names.get(kind, []))
    return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]


def test_completion_for_new_arguments():
    assert complete("delete --script ") == ["/p/run_many.py"]
    assert complete("undelete ") == ["aa01"]
    assert complete("signal kill --status r") == ["running"]
    assert complete("signal kill --status running --status f") == ["failed"]
    assert complete("reset --host ") == ["perle"]
    assert "--on" in complete("signal kill --o")
