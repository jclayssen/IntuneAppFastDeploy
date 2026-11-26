"""Pilot / production groups per application, kept in a local JSON file.

The file lives in the configuration folder mounted next to ``docker-compose.yml``
(``config/rollout.json``). Nothing is written to Intune or sent anywhere else.

Shape::

    {"apps": {"<intune app id>": {
        "name": "7-Zip",
        "pilot": {"intent": "required", "targets": [{"type": "group", "id": "...", "name": "..."}]},
        "prod":  {"intent": "required", "targets": [{"type": "allDevices"}]}
    }}}
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

TARGET_TYPES = ("group", "allDevices", "allUsers")
INTENTS = ("required", "available")
_GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


class StoreError(ValueError):
    pass


def clean_stage(stage: dict | None) -> dict:
    """Validate one stage (pilot or production) coming from the UI."""
    stage = stage or {}
    intent = stage.get("intent", "required")
    if intent not in INTENTS:
        raise StoreError("error.bad_intent")
    targets: list[dict] = []
    seen: set[str] = set()
    for target in stage.get("targets") or []:
        kind = target.get("type")
        if kind not in TARGET_TYPES:
            raise StoreError("error.bad_target")
        if kind == "group":
            group_id = str(target.get("id", "")).strip().lower()
            if not _GUID.match(group_id):
                raise StoreError("error.bad_group_id")
            key = group_id
            cleaned = {"type": "group", "id": group_id, "name": str(target.get("name", ""))[:200]}
        else:
            key = kind
            cleaned = {"type": kind}
        if key not in seen:
            seen.add(key)
            targets.append(cleaned)
    return {"intent": intent, "targets": targets}


class RolloutStore:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "rollout.json"
        self._lock = threading.Lock()

    def writable(self) -> bool:
        """Can the configuration folder be written? (wrong owner on Linux, usually)"""
        probe = self.path.parent / ".write-test"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError:
            return False
        return True

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"apps": {}}
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError("error.config_unreadable") from exc
        data.setdefault("apps", {})
        return data

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            raise StoreError("error.config_not_writable") from exc

    def get(self, app_id: str) -> dict:
        with self._lock:
            record = self._read()["apps"].get(app_id) or {}
        return {
            "name": record.get("name", ""),
            "pilot": record.get("pilot") or {"intent": "required", "targets": []},
            "prod": record.get("prod") or {"intent": "required", "targets": []},
        }

    def set(self, app_id: str, name: str, pilot: dict | None, prod: dict | None) -> dict:
        record = {"name": name, "pilot": clean_stage(pilot), "prod": clean_stage(prod)}
        with self._lock:
            data = self._read()
            data["apps"][app_id] = record
            self._write(data)
        return record

    def move(self, old_id: str, new_id: str, name: str | None = None) -> None:
        """A new version replaced ``old_id``: carry its groups over to ``new_id``."""
        with self._lock:
            data = self._read()
            record = data["apps"].pop(old_id, None)
            if record is None:
                return
            if name:
                record["name"] = name
            data["apps"][new_id] = record
            self._write(data)

    def delete(self, app_id: str) -> None:
        with self._lock:
            data = self._read()
            if data["apps"].pop(app_id, None) is not None:
                self._write(data)
