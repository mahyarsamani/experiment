import os
import re

from pathlib import Path
from typing import Dict, Union
from collections import OrderedDict
from ..helper.configurator import _get_project_config
from .stats import Stats, ScalarStat, HistogramStat, try_convert_numerical

line_patterns = OrderedDict(
    {
        # r"\S+::\w+.+": HistogramStat,
        r"\S+\.\w+\s[^\|]+": ScalarStat,
    }
)

ignore_patterns = [
    r"\S+::\w+.+",
    r"\(\w+/\w+\)",
    r"\(Unspecified\)",
    r".*avg.*|.*Avg.*|.*average.*|.*Average.*",
]


def process_stats_file(stats_file) -> Stats:
    stats = Stats()
    for line in stats_file.readlines():
        ignore = False
        for pattern in ignore_patterns:
            ignore |= not re.search(pattern, line) is None
        if ignore:
            continue
        for pattern in line_patterns:
            if re.match(pattern, line):
                stat_class = line_patterns[pattern]
                (
                    owner_group,
                    owner,
                    name,
                    desc,
                    value,
                ) = stat_class.parse_stat_line(line)
                stat_entry = stats.find(owner_group, name)
                if stat_entry is None:
                    stat_entry = stats.insert(
                        owner_group, name, stat_class(name, desc)
                    )
                stat_entry.add_to_container((owner, value))
                break
    return stats


def process_experiment(exp_name: str) -> Dict:
    def parse_path(path: Union[str, Path]):
        ret = {}
        for token in path.split("/"):
            name, value = token.split("_")
            value = try_convert_numerical(value)
            ret[name] = value
        return ret

    project_config = _get_project_config()
    exp_dir = os.path.join(
        project_config["gem5_out_base_dir"],
        project_config["project_name"],
        exp_name,
    )

    ret = []
    for subdir, _, files in os.walk(exp_dir):
        if "stats.txt" in files:
            rel_path = os.path.relpath(subdir, exp_dir)
            parameters = parse_path(rel_path)
            with open(os.path.join(subdir, "stats.txt"), "r") as stats_file:
                stats = process_stats_file(stats_file)
                stats.set_parameters(parameters)
                ret.append(stats)
    return ret
