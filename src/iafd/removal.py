"""Removal of software from the devices: settings and the script that applies them.

Pure functions. The PowerShell script in ``scripts/Remove-Software.ps1`` is a
fixed template; the settings chosen in the tool are passed to it as base64
encoded JSON, so nothing typed by a person is ever assembled into code.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).parent / "scripts" / "Remove-Software.ps1"
MODES = ("Detect", "Requirement", "RemediationDetect", "Uninstall")
KINDS = ("machine", "user", "store")
MAX_NAMES = 50
_VERSION = re.compile(r"^\d+(\.\d+){0,3}$")


class RemovalError(ValueError):
    """Carries a translation key for the UI."""


def slug(text: str) -> str:
    """A short, file-name-safe label used for the log file on the devices."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return cleaned[:40] or "software"


def clean_config(raw: dict) -> dict:
    """Validate what the UI sends and return exactly what the script needs."""
    names: list[str] = []
    for name in raw.get("names") or []:
        name = str(name).strip()
        if name and name.casefold() not in {existing.casefold() for existing in names}:
            names.append(name)
    if not names:
        raise RemovalError("error.removal_no_name")
    if len(names) > MAX_NAMES or any(len(name) > 300 or "\x00" in name for name in names):
        raise RemovalError("error.removal_bad_name")

    kind = raw.get("kind", "machine")
    if kind not in KINDS:
        raise RemovalError("error.removal_bad_kind")

    versions = raw.get("versions") or {"mode": "any"}
    mode = versions.get("mode", "any")
    if mode == "any":
        rule: dict = {"mode": "any"}
    elif mode == "list":
        values = [
            str(value).strip() for value in versions.get("values") or [] if str(value).strip()
        ]
        if not values:
            raise RemovalError("error.removal_bad_versions")
        rule = {"mode": "list", "values": values}
    elif mode == "below":
        value = str(versions.get("value") or "").strip()
        if not _VERSION.fullmatch(value):
            raise RemovalError("error.removal_bad_versions")
        rule = {"mode": "below", "value": value}
    else:
        raise RemovalError("error.removal_bad_versions")

    command = str(raw.get("command") or "").strip()
    if kind == "store":
        command = ""  # Store apps are removed by Windows itself
    if len(command) > 1000 or "\n" in command or "\r" in command or "\x00" in command:
        raise RemovalError("error.removal_bad_command")

    return {
        "names": names,
        "kind": kind,
        "versions": rule,
        "command": command,
        "slug": slug(names[0]),
        "timeout_seconds": _seconds(raw.get("timeout_seconds"), 1800, 5, 7200),
        "settle_seconds": _seconds(raw.get("settle_seconds"), 120, 1, 600),
    }


def _seconds(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high) if number > 0 else default


def render_script(config: dict, mode: str) -> str:
    """The script for one mode, with the settings embedded as data."""
    if mode not in MODES:
        raise RemovalError("error.removal_bad_mode")
    payload = json.dumps(clean_config(config), ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("'@@CONFIG@@'") != 1 or template.count("'@@MODE@@'") != 1:
        raise RuntimeError("the removal script template is damaged")
    return template.replace("'@@CONFIG@@'", f"'{encoded}'").replace("'@@MODE@@'", f"'{mode}'")


def script_file_name(config: dict) -> str:
    return f"Remove-{clean_config(config)['slug']}.ps1"


def main(argv: list[str]) -> int:
    """``python -m iafd.removal <mode> <config.json> <out.ps1>`` - used by the Windows tests."""
    if len(argv) != 3:
        print("usage: python -m iafd.removal <mode> <config.json> <out.ps1>", file=sys.stderr)
        return 2
    mode, config_path, out_path = argv
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    # Windows PowerShell 5.1 reads a script without a byte order mark as ANSI:
    # write one so names with accents survive.
    Path(out_path).write_text(render_script(config, mode), encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
