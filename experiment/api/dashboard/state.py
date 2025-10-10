from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, TypedDict


@dataclass(frozen=True)
class Link:
    label: str
    href: str


@dataclass(frozen=True)
class JobView:
    job_id: str
    experiment: str
    status: str
    status_color: str
    host: str
    links: Tuple[Link, ...]


class State(TypedDict):
    title: str
    hosts: List[str]
    jobs: List[Dict]
    last_update_epoch: float
