import os
import re

from pathlib import Path
from typing import Dict, Union
from collections import OrderedDict
from .stats import Stats, ScalarStat, HistogramStat

line_patterns = OrderedDict(
    {
        r"\S+::\w+.+": HistogramStat,
        r"\S+\.\w+\s[^\|]+": ScalarStat,
    }
)

ignore_patterns = [
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


def process_experiment_directory(exp_name: str) -> Dict:
    def get_project_prefix():
        cwd = os.path.abspath(os.getcwd())
        # TODO: Improve how to detect if in gem5 dir
        assert "gem5" in os.listdir(cwd)
        return os.path.basename(cwd)

    def parse_path(path: Union[str, Path]):
        def try_convert_numerical(value):
            ret = None
            try:
                ret = int(value)
            except:
                try:
                    ret = float(value)
                except:
                    ret = value
            assert not ret is None
            return ret

        project_prefix = get_project_prefix()

        _, post = str(path).split(project_prefix)
        tokens = post.split("/")
        experiment_prefix = tokens[0]
        other_tokens = tokens[1:]
        print(tokens)
        print(other_tokens)
        ret = {}
        for token in other_tokens:
            name, value = token.split("_")
            value = try_convert_numerical(value)
            ret[name] = value
        return ret

    for subdir, _, files in os.walk(exp_dir):
        if "stats.txt" in files:
            print(subdir)
            configuration = parse_path(subdir)
            print(configuration)
