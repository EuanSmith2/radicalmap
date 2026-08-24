import json
from pathlib import Path

DICT_PATH = Path(__file__).parent.parent / "data" / "dictionary.json"


def load_dictionary() -> dict:
    data = json.loads(DICT_PATH.read_text())
    return {entry["tag"]: entry for entry in data["hashtags"]}


def load_categories() -> dict:
    data = json.loads(DICT_PATH.read_text())
    return data["categories"]
