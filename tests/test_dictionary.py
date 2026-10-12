import json

from analysis import dictionary


def test_load_dictionary_and_categories(tmp_path, monkeypatch):
    fixture = {
        "hashtags": [
            {
                "tag": "demo",
                "display_name": "#demo",
                "category": "uncategorised",
                "definition": "demo",
                "origin": "fixture",
                "severity": "low",
            }
        ],
        "categories": {"uncategorised": "Demo category"},
    }
    data_path = tmp_path / "dictionary.json"
    data_path.write_text(json.dumps(fixture))

    monkeypatch.setattr(dictionary, "DICT_PATH", data_path)

    loaded = dictionary.load_dictionary()
    cats = dictionary.load_categories()

    assert "demo" in loaded
    assert loaded["demo"]["display_name"] == "#demo"
    assert cats["uncategorised"] == "Demo category"
