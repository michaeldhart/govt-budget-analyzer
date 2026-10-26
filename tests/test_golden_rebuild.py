"""Rebuild every derived table into a scratch copy of the repo and require it to equal the committed tables.

A deliberate change to a number means regenerating the committed CSVs, so it shows up in the diff of the commit.
Run with `pytest -m slow` (or just `pytest`); deselect with `-m "not slow"`.
"""
import os
import shutil
import subprocess

import pytest

from conftest import ENTITY, ROOT, read

pytestmark = pytest.mark.slow
COPY = ("govbudget", "entities", "pipelines")


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rebuild")
    for d in COPY:
        shutil.copytree(os.path.join(ROOT, d), tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "data"), tmp / "data", ignore=shutil.ignore_patterns("ocr"))
    src = os.path.join(ROOT, "sources")
    if os.path.isdir(src):
        os.symlink(src, tmp / "sources")  # builders hash the source PDFs
    r = subprocess.run(["sh", str(tmp / "pipelines" / ENTITY / "rebuild_all.sh")], capture_output=True, text=True, cwd=tmp)
    assert r.returncode == 0, f"rebuild_all.sh failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    return tmp / "data" / ENTITY


def committed_tables():
    d = os.path.join(ROOT, "data", ENTITY)
    return sorted(f for f in os.listdir(d) if f.endswith(".csv"))


@pytest.mark.parametrize("name", committed_tables())
def test_table_unchanged_by_rebuild(rebuilt, name):
    old = read(os.path.join(ROOT, "data", ENTITY, name))
    new = read(str(rebuilt / name))
    key = lambda r: tuple(sorted(r.items()))
    if old and new:
        assert list(old[0]) == list(new[0]), f"{name}: columns changed"
    a, b = sorted(map(key, old)), sorted(map(key, new))
    if a != b:
        sa, sb = set(a), set(b)
        gone = [dict(x) for x in a if x not in sb][:3]
        added = [dict(x) for x in b if x not in sa][:3]
        pytest.fail(f"{name}: {len(old)} -> {len(new)} rows; {len(sa - sb)} rows removed, {len(sb - sa)} added.\n"
                    f"first removed: {gone}\nfirst added: {added}")
