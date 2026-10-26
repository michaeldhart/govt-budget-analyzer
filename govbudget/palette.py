"""Design tokens shared by the PDF scripts and the site (govbudget/palette.json); category colors come from categories.csv."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "palette.json")


def tokens():
    with open(PATH) as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
