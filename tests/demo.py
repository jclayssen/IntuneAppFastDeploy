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

    def __init__(self) -> None:
        # One session per server: nothing cached leaks from one test to the next.
        self._session = Session(
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
    old["supersededAppCount"] = 1
    current["description"] = "File archiver with a high compression ratio."
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

    # A few more rows so the list looks like a real tenant in screenshots.
    for name, version, publisher, file, assigned in (
        ("VLC media player", "3.0.20", "VideoLAN", "vlc-3.0.20-win64.msi", True),
        ("Mozilla Firefox ESR", "115.9.0", "Mozilla", "Firefox Setup 115.9.0esr.msi", False),
    ):
        app = intune.add_app(
            displayName=name,
            displayVersion=version,
            publisher=publisher,
            setupFilePath=file,
            installCommandLine=f'msiexec /i "{file}" /qn /norestart',
            uninstallCommandLine='msiexec /x "{00000000-0000-0000-0000-000000000000}" /qn',
            isAssigned=assigned,
            rules=[
                {
                    "@odata.type": appmodel.RULE_PRODUCT,
                    "ruleType": "detection",
                    "productCode": "{00000000-0000-0000-0000-000000000000}",
                    "productVersionOperator": "notConfigured",
                    "productVersion": None,
                }
            ],
        )
        if assigned:
            intune.assignments[app["id"]] = [
                {
                    "id": "a",
                    "intent": "available",
                    "settings": None,
                    "target": {"@odata.type": "#microsoft.graph.allLicensedUsersAssignmentTarget"},
                }
            ]


def seed_devices(intune: FakeIntune) -> None:
    zip23 = ("7-Zip 23.01 (x64 edition)", "23.01.00.0", "Igor Pavlov")
    zip19 = ("7-Zip 19.00 (x64 edition)", "19.00.00.0", "Igor Pavlov")
    vlc = ("VLC media player", "3.0.20", "VideoLAN")
    npp86 = ("Notepad++ (64-bit x64)", "8.6", "Notepad++ Team")
    npp79 = ("Notepad++ (64-bit x64)", "7.9.5", "Notepad++ Team")
    chrome = ("Google Chrome", "131.0.6778.86", "Google LLC")
    java = ("Java 8 Update 201 (64-bit)", "8.0.2010.9", "Oracle Corporation")
    winrar = ("WinRAR 5.91 (64-bit)", "5.91.0", "win.rar GmbH")
    teamviewer = ("TeamViewer", "15.20.6", "TeamViewer")
    zoom = ("Zoom Workplace", "6.2.11", "Zoom Video Communications, Inc.")
    weather = ("Microsoft.BingWeather", "4.53.52892.0", "Microsoft Corporation")
    webview = ("Microsoft Edge WebView2 Runtime", "131.0.2903.70", "Microsoft Corporation")
    fleet = [
        ("PC-ACCUEIL-01", "accueil@example.test", [zip23, vlc, chrome, weather]),
        ("PC-COMPTA-02", "compta@example.test", [zip23, chrome, java, winrar]),
        ("PC-COMPTA-03", "paie@example.test", [zip19, chrome, java]),
        ("PC-DIRECTION-01", "direction@example.test", [zip23, chrome, zoom, teamviewer]),
        ("PC-ATELIER-04", "atelier@example.test", [zip19, npp79, vlc, winrar]),
        ("PC-IT-01", "admin@example.test", [zip23, npp86, chrome, teamviewer, zoom, webview]),
        ("PC-IT-02", "support@example.test", [zip23, npp86, chrome, vlc, webview]),
        ("LAPTOP-RH-07", "rh@example.test", [zip23, chrome, zoom, weather]),
    ]
    for name, user, software in fleet:
        intune.add_device(name, user, software)


def build():
    intune = FakeIntune()
    seed(intune)
    seed_devices(intune)
    # WinRAR stands for an EXE whose uninstaller has no silent mode declared.
    intune.no_silent = {"WinRAR 5.91 (64-bit)"}
    root = Path(tempfile.mkdtemp(prefix="iafd-demo-"))
    settings = Settings(
        tenant_id="demo",
        client_id="demo",
        config_dir=root / "config",
        work_dir=root / "work",
        default_language="fr",
        enable_device_inventory=True,
        enable_remediations=True,
    )
    app = create_app(
        settings,
        graph_factory=lambda token: Graph(
            token, BASE, transport=intune.transport(), sleep=lambda _: None
        ),
        auth=DemoAuth(),
    )
    # Tests and the screenshot script reach the simulated tenant through these.
    app.state.intune = intune
    app.state.settings = settings

    def demo_sync() -> dict:
        """Pretend the devices checked in with Intune (demo only)."""
        intune.sync()
        return {"ok": True}

    # The interface is mounted on "/" and would swallow a route added after it.
    app.add_api_route("/demo/sync", demo_sync, methods=["POST"], include_in_schema=False)
    app.router.routes.insert(0, app.router.routes.pop())

    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=8080, log_level="warning")
