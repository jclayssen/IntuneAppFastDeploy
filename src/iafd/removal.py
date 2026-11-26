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
    """Carries a translation key for the UI, and sometimes a detail."""

    detail = ""


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


# --------------------------------------------------------------------------- inventory


def version_key(version: str) -> tuple[int, ...] | None:
    """Same reading of a version as the script: leading numbers, nothing else.

    "8.0.2010.9" -> (8, 0, 2010, 9); "1.2 beta" -> (1, 2, 0, 0); "beta" -> None.
    Read character by character: the text comes from the inventory, and a
    regular expression here would be one more thing to prove harmless.
    """
    text = (version or "").lstrip()
    parts: list[int] = []
    position = 0
    while len(parts) < 4:
        end = position
        while end < len(text) and text[end] in "0123456789":
            end += 1
        if end == position:
            break
        if end - position > 9:
            return None  # not a version number
        parts.append(int(text[position:end]))
        if end < len(text) and text[end] == ".":
            position = end + 1
        else:
            break
    if not parts:
        return None
    return tuple(parts + [0] * (4 - len(parts)))


def version_wanted(rule: dict, version: str) -> bool:
    """Mirror of ``Test-VersionWanted`` in the script, to count devices before publishing."""
    mode = rule.get("mode", "any")
    if mode == "any":
        return True
    if mode == "list":
        return version in rule.get("values", [])
    if mode == "below":
        installed, limit = version_key(version), version_key(str(rule.get("value", "")))
        return installed is not None and limit is not None and installed < limit
    return False


def matching_installs(groups: list[dict], config: dict) -> list[dict]:
    """Inventory rows a rule would act on: ``[{name, version, devices}]``."""
    wanted = {name.casefold() for name in config["names"]}
    rows = []
    for group in groups:
        if group["name"].casefold() not in wanted:
            continue
        for entry in group["versions"]:
            if version_wanted(config["versions"], entry["version"]):
                rows.append(
                    {
                        "name": group["name"],
                        "version": entry["version"],
                        "devices": entry["devices"],
                    }
                )
    return rows


# --------------------------------------------------------------------------- Intune objects

WIN32 = "#microsoft.graph.win32LobApp"
SCRIPT_RULE = "#microsoft.graph.win32LobAppPowerShellScriptRule"
NAME_PREFIX = "Removal - "


def encode_script(script: str) -> str:
    # Windows PowerShell 5.1 needs the byte order mark to read accents correctly.
    return base64.b64encode(script.encode("utf-8-sig")).decode("ascii")


def detection_rule(config: dict) -> dict:
    return {
        "@odata.type": SCRIPT_RULE,
        "ruleType": "detection",
        "enforceSignatureCheck": False,
        "runAs32Bit": False,
        "scriptContent": encode_script(render_script(config, "Detect")),
        "operationType": "notConfigured",
        "operator": "notConfigured",
        "comparisonValue": None,
    }


def content_fields(config: dict) -> dict:
    """What depends on the settings: sent when creating the app and when changing a rule."""
    config = clean_config(config)
    file_name = script_file_name(config)
    listed = ", ".join(config["names"])
    return {
        "description": (
            f"Removes this software from the devices: {listed}. "
            "Created by IntuneAppFastDeploy; assign it with the Uninstall intent only."
        )[:1000],
        "fileName": file_name.rsplit(".", 1)[0] + ".intunewin",
        "setupFilePath": file_name,
        # Installing does nothing: this application only exists to uninstall.
        "installCommandLine": "cmd.exe /c exit 0",
        "uninstallCommandLine": (
            "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f'-File "{file_name}" -Mode Uninstall'
        ),
        "installExperience": {
            "runAsAccount": "user" if config["kind"] == "user" else "system",
            "deviceRestartBehavior": "suppress",
        },
        "rules": [detection_rule(config)],
    }


def app_body(config: dict) -> dict:
    config = clean_config(config)
    body = {
        "@odata.type": WIN32,
        "displayName": (NAME_PREFIX + config["names"][0])[:200],
        "publisher": "IntuneAppFastDeploy",
        "displayVersion": "1",
        "isFeatured": False,
        "applicableArchitectures": "x86,x64",
        "minimumSupportedWindowsRelease": "1607",
        "returnCodes": [
            {"returnCode": 0, "type": "success"},
            {"returnCode": 3010, "type": "softReboot"},
            {"returnCode": 1641, "type": "hardReboot"},
        ],
    }
    body.update(content_fields(config))
    return body


# --------------------------------------------------------------------------- older versions

REPLACE_TEMPLATE = Path(__file__).parent / "scripts" / "Replace-Software.ps1"
REQUIREMENT_NAME = "IntuneAppFastDeploy: an older version is installed"
REPLACE_MODES = ("upgrade", "reinstall")


def clean_replace(raw: dict | None, new_version: str) -> dict | None:
    """Settings of "replace an older version", or ``None`` when it is not used.

    ``upgrade``: the new installer upgrades over the old version; the app is only
    offered to devices that have an older version. ``reinstall``: the old version
    is removed first, by the removal script shipped in the package.
    """
    if not raw or not raw.get("enabled"):
        return None
    mode = raw.get("mode", "upgrade")
    if mode not in REPLACE_MODES:
        raise RemovalError("error.replace_bad_mode")
    below = str(raw.get("below") or new_version or "").strip()
    config = clean_config(
        {
            "names": raw.get("names"),
            "kind": "machine",
            "versions": {"mode": "below", "value": below},
            "command": raw.get("command") if mode == "reinstall" else "",
        }
    )
    return {"mode": mode, "config": config}


def requirement_rule(config: dict) -> dict:
    """Intune only installs the app where the script answers "present"."""
    return {
        "@odata.type": SCRIPT_RULE,
        "ruleType": "requirement",
        "displayName": REQUIREMENT_NAME,
        "enforceSignatureCheck": False,
        "runAs32Bit": False,
        "runAsAccount": "system",
        "scriptContent": encode_script(render_script(config, "Requirement")),
        "operationType": "string",
        "operator": "equal",
        "comparisonValue": "present",
    }


def is_tool_requirement(rule: dict) -> bool:
    return rule.get("ruleType") == "requirement" and rule.get("displayName") == REQUIREMENT_NAME


def replace_script_name(config: dict) -> str:
    return f"Replace-{clean_config(config)['slug']}.ps1"


def render_replace_script(config: dict, install_command: str) -> str:
    """The wrapper used as install command: remove the old version, then install."""
    install_command = install_command.strip()
    if not install_command or "\n" in install_command or "\r" in install_command:
        raise RemovalError("error.replace_bad_install")
    payload = json.dumps(
        {"remove_script": script_file_name(config), "install_command": install_command},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    template = REPLACE_TEMPLATE.read_text(encoding="utf-8")
    if template.count("'@@CONFIG@@'") != 1:
        raise RuntimeError("the replacement script template is damaged")
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return template.replace("'@@CONFIG@@'", f"'{encoded}'")


def replace_install_command(config: dict) -> str:
    return (
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f'-File "{replace_script_name(config)}"'
    )


STATES = {
    "notInstalled": "removed",
    "installed": "pending",
    "pendingInstall": "pending",
    "uninstallFailed": "failed",
    "failed": "failed",
    "notApplicable": "not_applicable",
}


def status_rows(statuses: list[dict]) -> dict:
    """Per-device result of a removal, from the app's device statuses in Intune."""
    rows = []
    for status in statuses:
        code = status.get("errorCode") or 0
        rows.append(
            {
                "device": status.get("deviceName") or "",
                "user": status.get("userPrincipalName") or status.get("userName") or "",
                "state": STATES.get(status.get("installState", ""), "unknown"),
                "error": f"0x{code & 0xFFFFFFFF:08X}" if code else "",
                "last_sync": status.get("lastSyncDateTime") or "",
            }
        )
    rows.sort(key=lambda row: (row["state"] != "failed", row["device"].casefold()))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"rows": rows, "counts": counts}


# --------------------------------------------------------------------------- on one device

ON_DEMAND_PREFIX = "Removal on demand - "


def health_script_body(config: dict) -> dict:
    """An Intune remediation: its detection finds the software, its remediation removes it.

    It is never assigned: it only runs on the devices it is started on.
    """
    config = clean_config(config)
    listed = ", ".join(config["names"])
    return {
        "@odata.type": "#microsoft.graph.deviceHealthScript",
        "displayName": (ON_DEMAND_PREFIX + config["names"][0])[:200],
        "description": (
            f"Removes this software from a device, on demand: {listed}. "
            "Created by IntuneAppFastDeploy; do not assign it."
        )[:1000],
        "publisher": "IntuneAppFastDeploy",
        "runAsAccount": "user" if config["kind"] == "user" else "system",
        "enforceSignatureCheck": False,
        "runAs32Bit": False,
        "roleScopeTagIds": ["0"],
        "detectionScriptContent": encode_script(render_script(config, "RemediationDetect")),
        "remediationScriptContent": encode_script(render_script(config, "Uninstall")),
    }


def run_state(state: dict) -> str:
    """One word for what happened on a device (see the script's exit codes)."""
    detection = state.get("detectionState", "unknown")
    remediation = state.get("remediationState", "unknown")
    if remediation == "success":
        return "removed"
    if remediation in ("remediationFailed", "scriptError") or detection == "scriptError":
        return "failed"
    if detection == "success":
        return "absent"  # the detection found nothing to remove
    return "pending"


def run_rows(states: list[dict], devices: list[dict]) -> dict:
    """Result of an on-demand removal on each device it was started on."""
    by_device = {}
    for state in states:
        managed = state.get("managedDevice") or {}
        by_device[managed.get("id") or state.get("id", "")] = state
    rows = []
    for device in devices:
        state = by_device.get(device["id"])
        if state is None:
            rows.append(
                {
                    "device": device.get("name", ""),
                    "user": device.get("user", ""),
                    "state": "pending",
                    "error": "",
                    "output": "",
                    "last_sync": "",
                }
            )
            continue
        output = (
            state.get("remediationScriptError")
            or state.get("postRemediationDetectionScriptOutput")
            or state.get("preRemediationDetectionScriptOutput")
            or ""
        )
        rows.append(
            {
                "device": device.get("name", ""),
                "user": device.get("user", ""),
                "state": run_state(state),
                "error": "",
                "output": str(output)[:500],
                "last_sync": state.get("lastStateUpdateDateTime") or "",
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"rows": rows, "counts": counts}


def main(argv: list[str]) -> int:
    """``python -m iafd.removal <mode> <config.json> <out>`` - used by the Windows tests."""
    if len(argv) != 3:
        print("usage: python -m iafd.removal <mode> <config.json> <out.ps1>", file=sys.stderr)
        return 2
    mode, config_path, out_path = argv
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    if mode == "Package":
        # {"removal": <removal settings>, "install_command": "..."} -> both scripts of a
        # "remove the older version, then install" package, written into a folder.
        removal_config = clean_config(config["removal"])
        folder = Path(out_path)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / script_file_name(removal_config)).write_text(
            render_script(removal_config, "Uninstall"), encoding="utf-8-sig"
        )
        wrapper = folder / replace_script_name(removal_config)
        wrapper.write_text(
            render_replace_script(removal_config, config["install_command"]), encoding="utf-8-sig"
        )
        print(wrapper.name)
        return 0
    # Windows PowerShell 5.1 reads a script without a byte order mark as ANSI:
    # write one so names with accents survive.
    Path(out_path).write_text(render_script(config, mode), encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
