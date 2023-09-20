import re

from enum import Enum
from collections import OrderedDict
from typing import Union, List, Dict
from abc import ABCMeta, abstractmethod, abstractclassmethod


def extract_numerical_substrings(string):
    return re.findall(r"\d+", string)


def remove_numerical_characters(string):
    return re.sub(r"\d+", "", string)


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


class DataDigest(Enum):
    with_aggregate = 0
    without_aggregate = 1
    just_aggregate = 2

    def has_individual(self):
        return self in [
            DataDigest.with_aggregate,
            DataDigest.without_aggregate,
        ]

    def has_aggregate(self):
        return self in [
            DataDigest.with_aggregate,
            DataDigest.just_aggregate,
        ]


class StatType(Enum):
    categorical = 0
    numerical = 1

    def __str__(self):
        return self.name


class Stat(metaclass=ABCMeta):
    def __init__(self, name: str, desc: str) -> None:
        self._name = name
        self._desc = desc
        self._container = None
        self._aggregate_container = None
        self._type = None

    def post_process(self):
        self._aggregate()
        self._post_process()

    def kind(self) -> StatType:
        return self._type

    def desc(self) -> str:
        return self._desc

    def _class_name(self):
        return str(type(self)).rsplit(".", 1)[1]

    @abstractclassmethod
    def parse_stat_line(cls, line) -> tuple:
        raise NotImplementedError

    @abstractmethod
    def add_to_container(self, value: tuple) -> None:
        raise NotImplementedError

    @abstractmethod
    def next_data_point(
        self, include: DataDigest = DataDigest.with_aggregate
    ) -> tuple:
        raise NotImplementedError

    @abstractmethod
    def _aggregate(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def _post_process(self):
        raise NotImplementedError

    def __str__(self):
        return (
            f"{self._class_name()}(name: {self._name}, desc: {self._desc}, "
            f"container: {self._container}, "
            f"aggregate_container: {self._aggregate_container})"
        )


class ScalarStat(Stat):
    def __init__(self, name: str, desc: str) -> None:
        super().__init__(name, desc)
        self._container = dict()
        self._aggregate_container = dict()
        self._type = StatType.categorical

    @classmethod
    def parse_stat_line(cls, line):
        tokens = line.split()
        owner, name = tokens[0].rsplit(".", 1)
        value = float(tokens[1])
        desc = " ".join(tokens[3:])
        owner_group = remove_numerical_characters(owner)
        return (owner_group, owner, name, desc, value)

    def add_to_container(self, value: tuple) -> None:
        assert len(value) == 2
        assert isinstance(value, tuple)
        assert isinstance(value[0], str)
        assert isinstance(value[1], float)

        self._container[value[0]] = value[1]

    def next_data_point(
        self, include: DataDigest = DataDigest.with_aggregate
    ) -> tuple:
        items = []
        if include.has_individual():
            items += self._container.items()
        if include.has_aggregate():
            items += self._aggregate_container.items()
        for owner, value in items:
            yield (owner, value)

    def _aggregate(self) -> None:
        agg = 0
        for value in self._container.values():
            agg += value
        self._aggregate_container["aggregate"] = agg

    def _post_process(self):
        self._container = OrderedDict(
            sorted(
                self._container.items(),
                key=lambda x: tuple(
                    [int(num) for num in extract_numerical_substrings(x[0])]
                ),
            )
        )


class HistogramStat(Stat):
    def __init__(self, name: str, desc: str):
        super().__init__(name, desc)
        self._container = dict()
        self._aggregate_container = dict()
        self._type = StatType.numerical

    @classmethod
    def parse_stat_line(cls, line) -> tuple:
        print(line)
        tokens = line.split()
        owner, name = tokens[0].rsplit(".", 1)
        name, bucket = name.split("::")
        value = (bucket, float(tokens[1]))
        desc = " ".join(tokens[3:])
        owner_group = remove_numerical_characters(owner)
        return (owner_group, owner, name, desc, value)

    def add_to_container(self, value: tuple) -> None:
        assert len(value) == 2
        assert isinstance(value, tuple)
        assert isinstance(value[0], str)
        assert isinstance(value[1], tuple)

        print("================================")
        print(value[0], value[1][0], value[1][1])
        print("================================")

    def next_data_point(
        self, include: DataDigest = DataDigest.with_aggregate
    ) -> tuple:
        raise NotImplementedError

    def _aggregate(self) -> None:
        pass

    def _post_process(self):
        pass


class Stats:
    def __init__(self):
        self._container = {}
        self._parameters = {}

    def set_parameters(self, parameters: Dict) -> None:
        self._parameters = parameters

    def items(self):
        return self._container.items()

    def find(self, owner_group: str, name: str) -> Union[Stat, None]:
        if (owner_group, name) in self._container:
            return self._container[(owner_group, name)]
        else:
            return None

    def insert(self, owner_group: str, name: str, stat: Stat) -> Stat:
        self._container[(owner_group, name)] = stat
        return stat

    def query(self, pattern: str = r".") -> List:
        ret = []
        for owner_group, stat_name in self._container:
            if re.search(pattern, stat_name, re.IGNORECASE):
                ret.append((owner_group, stat_name))
            if re.search(pattern, owner_group, re.IGNORECASE):
                ret.append((owner_group, stat_name))
        return ret

    def post_process(self) -> None:
        for stat in self._container.values():
            stat.post_process()
