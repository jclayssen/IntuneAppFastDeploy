"""Every text key used by the code must exist in every language file."""

import json
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "iafd"
LOCALES = {
    path.stem: json.loads(path.read_text("utf-8")) for path in (SRC / "locales").glob("*.json")
}
PREFIXES = (
    "error|warning|step|action|apps|app|stage|intent|target|rollout|wizard|review|form|arch"
    "|run_as|detection|mode|done|setup|login|footer|nav|inventory|removal|replace|notify"
)
NOT_KEYS = {"rollout.json", "app.exe", "app.js"}


def used_keys() -> set[str]:
    keys: set[str] = set()
    pattern = re.compile(rf"""["'`]((?:{PREFIXES})\.[a-z0-9_]+)["'`]""")
    for path in [*SRC.glob("*.py"), SRC / "static" / "app.js"]:
        keys.update(pattern.findall(path.read_text("utf-8")))
    return keys - NOT_KEYS


def test_english_and_french_are_present():
    assert {"en", "fr"} <= set(LOCALES)


def test_every_used_key_is_translated_everywhere():
    keys = used_keys()
    assert len(keys) > 100
    for code, texts in LOCALES.items():
        missing = sorted(keys - set(texts))
        assert not missing, f"{code}: {missing}"


def test_all_languages_have_the_same_keys():
    reference = set(LOCALES["en"])
    for code, texts in LOCALES.items():
        assert set(texts) == reference, f"{code}: {sorted(set(texts) ^ reference)}"
        assert texts["_name"]
