"""One government body: its config, mapping tables and directory layout."""
import csv
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


class Entity:
    def __init__(self, entity_id):
        self.id = entity_id
        self.config_dir = os.path.join(ROOT, "entities", entity_id)
        self.data_dir = os.path.join(ROOT, "data", entity_id)
        self.sources_dir = os.path.join(ROOT, "sources", entity_id)
        self.output_dir = os.path.join(ROOT, "output", entity_id)
        with open(os.path.join(self.config_dir, "entity.json")) as f:
            self.config = json.load(f)
        assert self.config["entity_id"] == entity_id, "entity.json entity_id must match its directory name"
        self.name = self.config["name"]
        self.fy_start_month = self.config["fy_start_month"]

    @classmethod
    def load(cls, entity_id):
        return cls(entity_id)

    # --- paths -----------------------------------------------------------------------------------
    def config_path(self, name):
        return os.path.join(self.config_dir, name)

    def data_path(self, *parts):
        return os.path.join(self.data_dir, *parts)

    def source_path(self, *parts):
        return os.path.join(self.sources_dir, *parts)

    def output_path(self, *parts):
        return os.path.join(self.output_dir, *parts)

    # --- config tables ---------------------------------------------------------------------------
    def table(self, name):
        return read_csv(self.config_path(name))

    def categories(self, scheme=None):
        rows = self.table("categories.csv")
        return [r for r in rows if scheme is None or r["scheme"] == scheme]

    def account_map(self):
        """(flow, account_key) -> mapping row."""
        return {(r["flow"], r["account_key"]): r for r in self.table("account_map.csv")}

    def exceptions(self):
        return self.table("exceptions.csv")

    # --- account keys ----------------------------------------------------------------------------
    def account_key(self, account):
        """Stable cross-year key for a printed account number, per this entity's account format."""
        a = account
        for bad, good in self.config["account_format"].get("typo_fixes", {}).items():
            a = a.replace(bad, good)
        parts = a.split("-")
        return "-".join(parts[i] for i in self.config["account_format"]["key_segments"])

    def sha(self, path):
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
