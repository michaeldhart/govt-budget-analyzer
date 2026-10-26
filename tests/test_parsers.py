"""Unit tests for the small parsing helpers the extractors and builders share. Characterization tests: they pin current behaviour
on the OCR quirks listed in CLAUDE.md, so consolidating the extractors (ROADMAP T5) cannot change how a number is read."""
import os
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, os.path.join(ROOT, "pipelines", "belvidere_il"))
import extract_audit  # noqa: E402
import extract_budget  # noqa: E402
from govbudget.entity import Entity  # noqa: E402


@pytest.mark.parametrize("tok,want", [
    ("1,234", 1234), ("(1,234)", -1234), ("{1,234}", -1234), ("0", 0), ("1,2OO", 1200), ("§,000", 5000),
    ("abc", None), ("-", None), ("", None), ("6,500:", None),
])
def test_budget_to_int(tok, want):
    assert extract_budget.to_int(tok) == want


@pytest.mark.parametrize("tok,want", [
    ("1,234", 1234), ("(1,234)", -1234), ("$5", 5), ("-12", -12), ("7,224 853", 7224853), ("297 ,036", 297036),
    ("abc", None), ("-", None),
])
def test_audit_to_int(tok, want):
    assert extract_audit.to_int(tok) == want


def test_numbers_trailing_columns_and_leftover_text():
    assert extract_budget.numbers("Salaries 01-5-110-5010 1,000 2,000 3,000") == ([1000, 2000, 3000], "Salaries 01-5-110-5010")


def test_numbers_negative_and_split_comma():
    assert extract_budget.numbers("Foo (1,445, 664) 0 7") == ([-1445664, 0, 7], "Foo")


def test_numbers_merge_rejoins_space_for_comma_only_on_retry():
    assert extract_budget.numbers("Foo 989 000 5,000 6,000")[0] == [989, 0, 5000, 6000]
    assert extract_budget.numbers("Foo 989 000 5,000 6,000", merge=True)[0] == [989000, 5000, 6000]


def test_numbers_junk_tokens_read_as_zero_only_when_asked():
    assert extract_budget.numbers("Foo 1 2 !")[0] == [1, 2]
    assert extract_budget.numbers("Foo 1 2 !", fill_to=3)[0] == [0, 1, 2]


@pytest.fixture(scope="module")
def E():
    return Entity.load("belvidere_il")


@pytest.mark.parametrize("printed,key", [
    ("01-4-110-4010", "110-4010"),
    ("01-5-330-4940", "330-4940"),       # street lighting reimbursement: the second segment (4 or 5) is ignored
    ("01-4-330-4940", "330-4940"),
    ("01-4-1104114", "110-4114"),         # the city's own missing-hyphen typo
    ("61-4-410-4900", "410-4900"),
])
def test_account_key(E, printed, key):
    assert E.account_key(printed) == key


def test_split_key():
    assert Entity.split_key("61:810-5010") == ("61", "810-5010")
    assert Entity.split_key("110-4010") == ("01", "110-4010")
