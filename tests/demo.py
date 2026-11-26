"""Run the interface against the in-memory fake Intune, with sign-in bypassed.

For development and screenshots only:  uv run python -m tests.demo
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import uvicorn

from iafd import appmodel
from iafd.auth import Session
from iafd.config import Settings
from iafd.graph import Graph
from iafd.web import create_app

from .fake_intune import BASE, FakeIntune


class DemoAuth:
    redirect_uri = "http://localhost:8080/auth/callback"
    _session = Session(
        "demo", "demo-user", "Demo Admin", "admin@example.test", "", time.time() + 1e9
    )

    def session(self, _):
        return self._session

    def token(self, _):
        return "test-token"

    def logout(self, _):
        pass


def seed(intune: FakeIntune) -> None:
    def msi(version: str, code: str) -> dict:
        return intune.add_app(
            displayName=f"7-Zip {version} (x64 edition)",
            displayVersion=f"{version}.00.0",
            publisher="Igor Pavlov",
            setupFilePath=f"7z{version.replace('.', '')}-x64.msi",
            installCommandLine=f'msiexec /i "7z{version.replace(".", "")}-x64.msi" /qn /norestart',
            uninstallCommandLine=f'msiexec /x "{code}" /qn /norestart',
            rules=[
                {
                    "@odata.type": appmodel.RULE_PRODUCT,
                    "ruleType": "detection",
                    "productCode": code,
                    "productVersionOperator": "notConfigured",
                    "productVersion": None,
                }
            ],
        )

    old = msi("22.01", "{23170F69-40C1-2702-2201-000001000000}")
    current = msi("23.01", "{23170F69-40C1-2702-2301-000001000000}")
    intune.relationships[current["id"]] = [
        {"@odata.type": appmodel.SUPERSEDENCE, "targetId": old["id"], "supersedenceType": "update"}
    ]
    intune.assignments[current["id"]] = [
        {
            "id": "a",
            "intent": "required",
            "settings": None,
            "target": {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"},
        }
    ]
    current["isAssigned"] = True
    intune.add_app(
        displayName="Notepad++",
        displayVersion="8.6.0",
        publisher="Notepad++ Team",
        setupFilePath="npp.8.6.0.Installer.x64.exe",
        installCommandLine='"npp.8.6.0.Installer.x64.exe" /S',
        uninstallCommandLine='"%ProgramFiles%\\Notepad++\\uninstall.exe" /S',
        rules=[
            {
                "@odata.type": appmodel.RULE_FILE,
                "ruleType": "detection",
                "path": "%ProgramFiles%\\Notepad++",
                "fileOrFolderName": "notepad++.exe",
                "check32BitOn64System": False,
                "operationType": "version",
                "operator": "greaterThanOrEqual",
                "comparisonValue": "8.6.0",
            }
        ],
    )


def build():
    intune = FakeIntune()
    seed(intune)
    root = Path(tempfile.mkdtemp(prefix="iafd-demo-"))
    settings = Settings(
        tenant_id="demo",
        client_id="demo",
        config_dir=root / "config",
        work_dir=root / "work",
        default_language="fr",
    )
    return create_app(
        settings,
        graph_factory=lambda token: Graph(
            token, BASE, transport=intune.transport(), sleep=lambda _: None
        ),
        auth=DemoAuth(),
    )


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=8080, log_level="warning")
