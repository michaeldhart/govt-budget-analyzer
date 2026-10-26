import csv
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ENTITY = "belvidere_il"


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def data_dir():
    return os.path.join(ROOT, "data", ENTITY)


@pytest.fixture(scope="session")
def table(data_dir):
    cache = {}

    def get(name):
        if name not in cache:
            cache[name] = read(os.path.join(data_dir, name + ".csv"))
        return cache[name]
    return get


@pytest.fixture(scope="session")
def entity():
    from govbudget.entity import Entity
    return Entity.load(ENTITY)
