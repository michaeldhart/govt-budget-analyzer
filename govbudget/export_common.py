"""Small helpers shared by the site exporters (export_site.py, export_park.py)."""


def ids(s):
    return sorted(i for i in (s or "").split("|") if i)


def num(s):
    return int(s) if s not in ("", None) else None


def in_range(ex, fy):
    lo = int(ex["fy_from"]) if ex["fy_from"] else -10**9
    hi = int(ex["fy_to"]) if ex["fy_to"] else 10**9
    return lo <= fy <= hi
