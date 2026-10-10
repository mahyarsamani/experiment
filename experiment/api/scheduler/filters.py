"""Selecting jobs by experiment, host, status, start time and text.

The console and the dashboard both use `JobFilter` on the job views in a
scheduler snapshot (`Job.view`), so a filter selects the same jobs in both.
Times are the local time of the machine doing the filtering.
"""

import time

from dataclasses import dataclass, field
from datetime import datetime, timedelta

TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_time(text: str) -> float:
    """'2026-10-09', '2026-10-09 14:00' or '2026-10-09 14:00:05' (local)."""
    text = text.strip().replace("T", " ")
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(
        f"can't read {text!r} as a time; use YYYY-MM-DD or YYYY-MM-DD HH:MM"
    )


def day_range(text: str) -> tuple[float, float]:
    """[start, end) of the local day `text` names (YYYY-MM-DD)."""
    day = datetime.strptime(text.strip(), "%Y-%m-%d")
    return day.timestamp(), (day + timedelta(days=1)).timestamp()


def format_time(epoch: float | None) -> str:
    if epoch is None:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


@dataclass
class JobFilter:
    experiment: str | None = None
    host: str | None = None
    statuses: set[str] = field(default_factory=set)
    # NOTE: On the job's start time: since <= start < until.
    since: float | None = None
    until: float | None = None
    text: str | None = None

    @classmethod
    def from_args(
        cls,
        experiment: str | None = None,
        host: str | None = None,
        statuses=(),
        since: str | None = None,
        until: str | None = None,
        on: str | None = None,
        text: str | None = None,
    ) -> "JobFilter":
        """From user input; dates are strings as `parse_time` reads them."""
        begin = parse_time(since) if since else None
        end = parse_time(until) if until else None
        if on:
            day_begin, day_end = day_range(on)
            begin = max(begin or day_begin, day_begin)
            end = min(end or day_end, day_end)
        return cls(
            experiment=experiment or None,
            host=host or None,
            statuses=set(statuses or ()),
            since=begin,
            until=end,
            text=text or None,
        )

    def empty(self) -> bool:
        return (
            self.experiment is None
            and self.host is None
            and not self.statuses
            and self.since is None
            and self.until is None
            and self.text is None
        )

    def matches(self, job: dict) -> bool:
        if self.experiment is not None and job["experiment"] != self.experiment:
            return False
        if self.host is not None and job["host"] != self.host:
            return False
        if self.statuses and job["status"] not in self.statuses:
            return False
        if self.since is not None or self.until is not None:
            start = job.get("start_time")
            if start is None:
                return False
            if self.since is not None and start < self.since:
                return False
            if self.until is not None and start >= self.until:
                return False
        if self.text is not None:
            needle = self.text.lower()
            if not any(
                needle in str(job.get(key, "")).lower()
                for key in ("id", "command", "full_command")
            ):
                return False
        return True

    def select(self, jobs: list[dict]) -> list[dict]:
        return [job for job in jobs if self.matches(job)]
