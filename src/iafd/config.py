"""Settings, read from environment variables (see ``docker-compose.yml``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    public_url: str = "http://localhost:8080"
    config_dir: Path = Path("/config")
    work_dir: Path = Path("/tmp/iafd")  # noqa: S108 - private to the container
    max_download_mb: int = 8192
    enable_group_picker: bool = False
    enable_device_inventory: bool = False
    default_language: str = "en"
    allowed_users: list[str] = field(default_factory=list)
    session_hours: int = 12
    keep_versions: int = 2
    authority_host: str = "https://login.microsoftonline.com"
    graph_url: str = "https://graph.microsoft.com/beta"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        return cls(
            tenant_id=env("TENANT_ID", "").strip(),
            client_id=env("CLIENT_ID", "").strip(),
            client_secret=env("CLIENT_SECRET", "").strip(),
            public_url=env("PUBLIC_URL", "http://localhost:8080").strip().rstrip("/"),
            config_dir=Path(env("CONFIG_DIR", "/config")),
            work_dir=Path(env("WORK_DIR", "/tmp/iafd")),  # noqa: S108
            max_download_mb=int(env("MAX_DOWNLOAD_MB", "8192")),
            enable_group_picker=_flag("ENABLE_GROUP_PICKER"),
            enable_device_inventory=_flag("ENABLE_DEVICE_INVENTORY"),
            default_language=env("DEFAULT_LANGUAGE", "en").strip().lower() or "en",
            allowed_users=[
                user.strip().lower() for user in env("ALLOWED_USERS", "").split(",") if user.strip()
            ],
            session_hours=int(env("SESSION_HOURS", "12")),
            authority_host=env("AUTHORITY_HOST", "https://login.microsoftonline.com").rstrip("/"),
            graph_url=env("GRAPH_URL", "https://graph.microsoft.com/beta").rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.tenant_id and self.client_id)

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def scopes(self) -> list[str]:
        resource = "/".join(self.graph_url.split("/")[:3])  # https://graph.microsoft.com
        scopes = [f"{resource}/DeviceManagementApps.ReadWrite.All"]
        if self.enable_group_picker:
            scopes.append(f"{resource}/GroupMember.Read.All")
        if self.enable_device_inventory:
            scopes.append(f"{resource}/DeviceManagementManagedDevices.Read.All")
        return scopes
