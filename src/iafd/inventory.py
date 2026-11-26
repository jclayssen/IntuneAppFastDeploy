"""Software found on the devices, as reported by Intune ("discovered apps").

Pure functions: grouping, searching and paging of what Microsoft Graph returns.
Intune only knows the name, version, publisher and device count of each entry;
it does not say how the software was installed.
"""

from __future__ import annotations

import re

PAGE_SIZE = 50
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def safe_id(value: str) -> str:
    """Identifiers end up in Microsoft Graph URLs: refuse anything unexpected."""
    if not _ID.fullmatch(value or ""):
        raise ValueError("error.bad_id")
    return value


def version_key(version: str) -> tuple:
    """Sort key for versions: numeric parts compare as numbers."""
    parts = re.split(r"[.\-+_ ]", version or "")
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts if part)


def group_apps(detected: list[dict]) -> list[dict]:
    """One entry per software name, with the versions seen and their device counts."""
    groups: dict[str, dict] = {}
    for item in detected:
        if str(item.get("platform") or "windows").lower() != "windows":
            continue
        name = (item.get("displayName") or "").strip()
        if not name:
            continue
        group = groups.setdefault(
            name.casefold(), {"name": name, "publisher": "", "installs": 0, "versions": []}
        )
        if not group["publisher"] and item.get("publisher"):
            group["publisher"] = item["publisher"]
        devices = int(item.get("deviceCount") or 0)
        group["installs"] += devices
        group["versions"].append(
            {"id": item.get("id", ""), "version": item.get("version") or "", "devices": devices}
        )
    for group in groups.values():
        group["versions"].sort(key=lambda entry: version_key(entry["version"]), reverse=True)
    return sorted(groups.values(), key=lambda group: group["name"].casefold())


def summarize_device(device: dict) -> dict:
    return {
        "id": device.get("id", ""),
        "name": device.get("deviceName") or "",
        "user": device.get("userPrincipalName") or "",
        "os_version": device.get("osVersion") or "",
        "model": " ".join(
            part for part in (device.get("manufacturer"), device.get("model")) if part
        ),
        "last_sync": device.get("lastSyncDateTime") or "",
    }


def device_apps(detected: list[dict]) -> list[dict]:
    apps = [
        {
            "name": item.get("displayName") or "",
            "version": item.get("version") or "",
            "publisher": item.get("publisher") or "",
        }
        for item in detected
        if item.get("displayName")
    ]
    return sorted(apps, key=lambda app: (app["name"].casefold(), version_key(app["version"])))


def search(items: list[dict], query: str, fields: tuple[str, ...]) -> list[dict]:
    words = query.casefold().split()
    if not words:
        return items
    return [
        item
        for item in items
        if all(
            any(word in str(item.get(field, "")).casefold() for field in fields) for word in words
        )
    ]


def paginate(items: list[dict], page: int, size: int = PAGE_SIZE) -> dict:
    pages = max(1, -(-len(items) // size))
    page = min(max(1, page), pages)
    start = (page - 1) * size
    return {"items": items[start : start + size], "total": len(items), "page": page, "pages": pages}
