"""A tiny in-memory stand-in for the Intune part of Microsoft Graph.

It only checks that the tool sends a coherent sequence of calls. It is not
proof that the real service accepts the same payloads.
"""

from __future__ import annotations

import base64
import itertools
import json
import re

import httpx

BASE = "https://graph.test/beta"
STORAGE = "https://storage.test/container/blob?sig=abc"


class FakeIntune:
    def __init__(self) -> None:
        self.apps: dict[str, dict] = {}
        self.assignments: dict[str, list[dict]] = {}
        self.relationships: dict[str, list[dict]] = {}  # app id -> outgoing (child) links
        self.files: dict[str, dict] = {}
        self.blocks: dict[str, bytes] = {}
        self.committed_blob: bytes | None = None
        self.calls: list[tuple[str, str]] = []
        self.patches: list[dict] = []  # bodies of the PATCH requests on apps, in order
        self.fail_commit = False
        self.devices: dict[str, dict] = {}
        self.installed: dict[str, list[tuple[str, str, str]]] = {}  # device id -> software
        self.removal_results: dict[str, dict[str, str]] = {}  # app id -> device id -> state
        self.fail_statuses = False
        self.no_silent: set[str] = set()
        self.health_scripts: dict[str, dict] = {}
        self.health_runs: dict[str, dict[str, dict | None]] = {}
        self.remediations_unlicensed = False
        self.groups: dict[str, dict] = {}  # group id -> {name, members: set of object ids}
        self.directory: dict[str, str] = {}  # groups of the tenant found by the search: id -> name
        self.unregistered: set[str] = set()  # device ids without an Entra object
        self._ids = itertools.count(1)

    def add_app(self, **fields) -> dict:
        app_id = f"app-{next(self._ids)}"
        app = {
            "@odata.type": "#microsoft.graph.win32LobApp",
            "id": app_id,
            "displayName": "App",
            "publisher": "Pub",
            "description": "Desc",
            "isAssigned": False,
            "publishingState": "published",
            "rules": [],
            **fields,
        }
        self.apps[app_id] = app
        self.assignments[app_id] = []
        self.relationships[app_id] = []
        return app

    @staticmethod
    def _check_settings(body: dict) -> None:
        """Types and values the reference gives for the settings a form can change."""
        for prop in (
            "minimumFreeDiskSpaceInMB",
            "minimumMemoryInMB",
            "minimumNumberOfProcessors",
            "minimumCpuSpeedInMHz",
        ):
            value = body.get(prop)
            assert value is None or (type(value) is int and value > 0), (prop, value)
        if "allowAvailableUninstall" in body:
            assert isinstance(body["allowAvailableUninstall"], bool)
        if "minimumSupportedWindowsRelease" in body:
            assert isinstance(body["minimumSupportedWindowsRelease"], str)
        experience = body.get("installExperience")
        if experience is not None:
            assert experience["runAsAccount"] in ("system", "user"), experience
            assert experience.get("deviceRestartBehavior", "allow") in (
                "basedOnReturnCode",
                "allow",
                "suppress",
                "force",
            ), experience
            minutes = experience.get("maxRunTimeInMinutes", 60)
            assert type(minutes) is int and minutes > 0, experience

    def add_device(self, name: str, user: str, software: list[tuple[str, str, str]]) -> dict:
        """A Windows device with (name, version, publisher) entries installed on it."""
        number = len(self.devices) + 1
        device_id = f"dev-{number}"
        self.devices[device_id] = {
            "id": device_id,
            "azureADDeviceId": f"aaaaaaaa-0000-0000-0000-{number:012d}",
            "deviceName": name,
            "userPrincipalName": user,
            "operatingSystem": "Windows",
            "osVersion": "10.0.26100.2314",
            "manufacturer": "Contoso",
            "model": "Laptop 14",
            "lastSyncDateTime": "2026-11-25T08:15:00Z",
        }
        self.installed[device_id] = software
        return self.devices[device_id]

    # ----------------------------------------------------------------- removals

    def _removal_config(self, app: dict) -> dict | None:
        """Settings embedded in the detection script of a removal application."""
        for rule in app.get("rules", []):
            if rule.get("@odata.type", "").endswith("PowerShellScriptRule"):
                script = base64.b64decode(rule["scriptContent"]).decode("utf-8-sig")
                match = re.search(r"\$ConfigBase64 = '([A-Za-z0-9+/=]+)'", script)
                if match:
                    return json.loads(base64.b64decode(match.group(1)))
        return None

    def _targeted(self, app_id: str) -> bool:
        return any(a.get("intent") == "uninstall" for a in self.assignments.get(app_id, []))

    def _removal_matches(self, app_id: str) -> list[tuple[str, tuple[str, str, str]]]:
        config = self._removal_config(self.apps[app_id])
        if not config or not self._targeted(app_id):
            return []
        names = {name.casefold() for name in config["names"]}
        return [
            (device_id, entry)
            for device_id, software in self.installed.items()
            if self._reached(app_id, device_id)
            for entry in software
            if entry[0].casefold() in names
        ]

    def _reached(self, app_id: str, device_id: str) -> bool:
        """Is this device in one of the targets of the app's uninstall assignments?"""
        for assignment in self.assignments.get(app_id, []):
            if assignment.get("intent") != "uninstall":
                continue
            group = self.groups.get(assignment["target"].get("groupId", ""))
            # "All devices", "all users" and groups the fake knows nothing about reach everyone.
            if group is None or self._object_id(device_id) in group["members"]:
                return True
        return False

    def sync(self) -> None:
        """The devices check in: every targeted removal runs.

        Names listed in ``no_silent`` stand for an EXE without a silent
        uninstaller: they stay, unless the rule carries a command.
        """
        self._run_remediations()
        for app_id in list(self.apps):
            config = self._removal_config(self.apps[app_id])
            for device_id, entry in self._removal_matches(app_id):
                stubborn = entry[0] in self.no_silent and not config.get("command")
                self.removal_results.setdefault(app_id, {})[device_id] = (
                    "uninstallFailed" if stubborn else "notInstalled"
                )
                if not stubborn:
                    self.installed[device_id] = [e for e in self.installed[device_id] if e != entry]

    def _device_statuses(self, app_id: str) -> list[dict]:
        results = dict(self.removal_results.get(app_id, {}))
        for device_id, _ in self._removal_matches(app_id):
            results.setdefault(device_id, "installed")  # targeted, has not checked in yet
        return [
            {
                "id": f"{app_id}_{device_id}",
                "deviceName": self.devices[device_id]["deviceName"],
                "userPrincipalName": self.devices[device_id]["userPrincipalName"],
                "installState": state,
                "errorCode": -2016345060 if state == "uninstallFailed" else 0,
                "lastSyncDateTime": "2026-11-26T09:30:00Z",
            }
            for device_id, state in sorted(results.items())
        ]

    def _detected(self, device_id: str | None = None) -> list[dict]:
        rows: dict[tuple[str, str, str], list[str]] = {}
        for owner, software in self.installed.items():
            if device_id in (None, owner):
                for entry in software:
                    rows.setdefault(entry, []).append(owner)
        return [
            {
                "id": "det" + str(abs(hash(entry)) % 10**12),
                "displayName": entry[0],
                "version": entry[1],
                "publisher": entry[2],
                "platform": "windows",
                "deviceCount": len(owners),
                "_devices": owners,
            }
            for entry, owners in sorted(rows.items())
        ]

    # ----------------------------------------------------------------- Entra directory

    def _object_id(self, device_id: str) -> str:
        return "obj-" + device_id

    def _directory(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/beta")
        method = request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/devices" and method == "GET":
            wanted = re.fullmatch(r"deviceId eq '([0-9a-f-]{36})'", request.url.params["$filter"])
            assert wanted, "only an exact, well-formed identifier may be searched"
            found = [
                {"id": self._object_id(device_id)}
                for device_id, device in self.devices.items()
                if device["azureADDeviceId"] == wanted.group(1)
                and device_id not in self.unregistered
            ]
            return httpx.Response(200, json={"value": found})
        if path == "/groups" and method == "GET":
            # The only search the tool does: by the start of the name, 20 results, sorted.
            wanted = re.fullmatch(
                r"startswith\(displayName,'(.*)'\)",
                request.url.params.get("$filter", "startswith(displayName,'')"),
            )
            assert wanted, "groups are only searched by the start of their name"
            start = wanted.group(1).replace("''", "'").casefold()
            found = sorted(
                (
                    {"id": group_id, "displayName": name}
                    for group_id, name in self.directory.items()
                    if name.casefold().startswith(start)
                ),
                key=lambda group: group["displayName"].casefold(),
            )
            return httpx.Response(200, json={"value": found[: int(request.url.params["$top"])]})
        if path == "/groups" and method == "POST":
            assert body["securityEnabled"] is True and body["mailEnabled"] is False
            assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", body["mailNickname"])
            group_id = f"44444444-4444-4444-4444-{len(self.groups) + 1:012d}"
            self.groups[group_id] = {"name": body["displayName"], "members": set()}
            return httpx.Response(201, json={"id": group_id, **body})
        match = re.fullmatch(r"/groups/([^/]+)/members/\$ref", path)
        if match and method == "POST" and match.group(1) in self.groups:
            object_id = body["@odata.id"].rsplit("/", 1)[1]
            members = self.groups[match.group(1)]["members"]
            if object_id in members:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "Request_BadRequest",
                            "message": "One or more added object references already exist",
                        }
                    },
                )
            members.add(object_id)
            return httpx.Response(204)
        match = re.fullmatch(r"/groups/([^/]+)", path)
        if match and method == "DELETE" and self.groups.pop(match.group(1), None) is not None:
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})

    # ----------------------------------------------------------------- remediation scripts

    def _script_config(self, script: dict) -> dict:
        text = base64.b64decode(script["remediationScriptContent"]).decode("utf-8-sig")
        return json.loads(base64.b64decode(re.search(r"ConfigBase64 = '([^']+)'", text).group(1)))

    def _health_scripts(self, request: httpx.Request, path: str) -> httpx.Response | None:
        method = request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/deviceHealthScripts" and method == "POST":
            for field in ("displayName", "detectionScriptContent", "remediationScriptContent"):
                assert body[field], field
            assert "[string]$Mode = 'RemediationDetect'" in base64.b64decode(
                body["detectionScriptContent"]
            ).decode("utf-8-sig")
            script_id = f"hs-{len(self.health_scripts) + 1}"
            self.health_scripts[script_id] = {"id": script_id, **body}
            self.health_runs[script_id] = {}
            return httpx.Response(201, json=self.health_scripts[script_id])
        match = re.fullmatch(r"/deviceHealthScripts/([^/]+)", path)
        if match and method == "DELETE":
            if self.health_scripts.pop(match.group(1), None) is None:
                return httpx.Response(404, json={"error": {"code": "NotFound", "message": ""}})
            self.health_runs.pop(match.group(1), None)
            return httpx.Response(204)
        match = re.fullmatch(r"/managedDevices/([^/]+)/initiateOnDemandProactiveRemediation", path)
        if match and method == "POST":
            if (
                match.group(1) not in self.devices
                or body["scriptPolicyId"] not in self.health_scripts
            ):
                return httpx.Response(404, json={"error": {"code": "NotFound", "message": ""}})
            if self.remediations_unlicensed:
                return httpx.Response(
                    403, json={"error": {"code": "Forbidden", "message": "licence not confirmed"}}
                )
            self.health_runs[body["scriptPolicyId"]][match.group(1)] = None  # asked, not run yet
            return httpx.Response(204)
        match = re.fullmatch(r"/deviceHealthScripts/([^/]+)/deviceRunStates", path)
        if match and method == "GET":
            runs = self.health_runs.get(match.group(1), {})
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": f"{match.group(1)}:{device_id}",
                            **state,
                            "managedDevice": {
                                "id": device_id,
                                "deviceName": self.devices[device_id]["deviceName"],
                            },
                        }
                        for device_id, state in runs.items()
                        if state is not None
                    ]
                },
            )
        return None

    def _run_remediations(self) -> None:
        for script_id, runs in self.health_runs.items():
            config = self._script_config(self.health_scripts[script_id])
            names = {name.casefold() for name in config["names"]}
            for device_id, state in list(runs.items()):
                if state is not None:
                    continue
                found = [e for e in self.installed[device_id] if e[0].casefold() in names]
                stamp = {"lastStateUpdateDateTime": "2026-11-26T09:45:00Z"}
                if not found:
                    runs[device_id] = {
                        "detectionState": "success",
                        "remediationState": "skipped",
                        "preRemediationDetectionScriptOutput": "Not found",
                        **stamp,
                    }
                elif any(e[0] in self.no_silent for e in found) and not config.get("command"):
                    runs[device_id] = {
                        "detectionState": "fail",
                        "remediationState": "remediationFailed",
                        "remediationScriptError": f"{found[0][0]} {found[0][1]}: no silent "
                        "uninstall method, a command is needed",
                        **stamp,
                    }
                else:
                    self.installed[device_id] = [
                        e for e in self.installed[device_id] if e not in found
                    ]
                    runs[device_id] = {
                        "detectionState": "fail",
                        "remediationState": "success",
                        "postRemediationDetectionScriptOutput": "Not found",
                        **stamp,
                    }

    def _inventory(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/beta/deviceManagement")
        handled = self._health_scripts(request, path)
        if handled is not None:
            return handled
        assert request.method == "GET", "the inventory is read-only"
        public = lambda row: {k: v for k, v in row.items() if not k.startswith("_")}  # noqa: E731
        if path == "/detectedApps":
            rows = [public(row) for row in self._detected()]
            # An iPhone app proves that other platforms are filtered out.
            rows.append(
                {
                    "id": "ios1",
                    "displayName": "Outlook",
                    "version": "4.1",
                    "platform": "ios",
                    "deviceCount": 3,
                }
            )
            return httpx.Response(200, json={"value": rows})
        match = re.fullmatch(r"/detectedApps/([^/]+)/managedDevices", path)
        if match:
            row = next((r for r in self._detected() if r["id"] == match.group(1)), None)
            if row is None:
                return httpx.Response(404, json={"error": {"code": "NotFound", "message": ""}})
            return httpx.Response(200, json={"value": [self.devices[d] for d in row["_devices"]]})
        if path == "/managedDevices":
            assert "Windows" in request.url.params["$filter"]
            return httpx.Response(200, json={"value": list(self.devices.values())})
        match = re.fullmatch(r"/managedDevices/([^/]+)", path)
        if match and match.group(1) in self.devices:
            device = dict(self.devices[match.group(1)])
            if request.url.params.get("$expand") == "detectedApps":
                device["detectedApps"] = [public(row) for row in self._detected(match.group(1))]
            return httpx.Response(200, json=device)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _relationships_of(self, app_id: str) -> list[dict]:
        result = []
        for item in self.relationships.get(app_id, []):
            target = self.apps.get(item["targetId"], {})
            result.append(
                {
                    **item,
                    "targetType": "child",
                    "targetDisplayName": target.get("displayName", ""),
                    "targetDisplayVersion": target.get("displayVersion", ""),
                }
            )
        for other, items in self.relationships.items():
            for item in items:
                if item["targetId"] == app_id:
                    result.append({**item, "targetId": other, "targetType": "parent"})
        return result

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://storage.test/"):
            assert "authorization" not in request.headers
            if "comp=blocklist" in url:
                ids = re.findall(r"<Latest>([^<]+)</Latest>", request.content.decode())
                self.committed_blob = b"".join(self.blocks[i] for i in ids)
            else:
                self.blocks[request.url.params["blockid"]] = request.content
            return httpx.Response(201)

        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path.startswith(("/beta/groups", "/beta/devices")):
            self.calls.append((request.method, request.url.path.removeprefix("/beta")))
            return self._directory(request)
        if request.url.path.startswith("/beta/deviceManagement/"):
            self.calls.append((request.method, request.url.path.removeprefix("/beta")))
            return self._inventory(request)
        path = request.url.path.removeprefix("/beta/deviceAppManagement/mobileApps")
        self.calls.append((request.method, path))
        body = json.loads(request.content) if request.content else {}
        method = request.method

        if path == "" and method == "GET":
            return httpx.Response(200, json={"value": list(self.apps.values())})
        if path == "" and method == "POST":
            for forbidden in ("id", "committedContentVersion", "isAssigned", "detectionRules"):
                assert forbidden not in body, forbidden
            assert body["displayName"] and body["publisher"] and body["description"]
            assert body["rules"], "an app needs a detection rule"
            self._check_settings(body)
            app = self.add_app(**body)
            return httpx.Response(201, json=app)

        match = re.fullmatch(r"/([^/]+)(/.*)?", path)
        app_id, rest = match.group(1), match.group(2) or ""
        if app_id not in self.apps:
            return httpx.Response(404, json={"error": {"code": "NotFound", "message": app_id}})
        app = self.apps[app_id]

        if rest == "" and method == "GET":
            return httpx.Response(200, json=app)
        if rest == "" and method == "PATCH":
            self.patches.append(body)
            self._check_settings(body)
            app.update(body)
            return httpx.Response(204)
        if rest == "" and method == "DELETE":
            if any(i["targetId"] == app_id for items in self.relationships.values() for i in items):
                return httpx.Response(
                    400, json={"error": {"code": "BadRequest", "message": "app is superseded"}}
                )
            del self.apps[app_id]
            return httpx.Response(204)
        if rest == "/deviceStatuses":
            if self.fail_statuses:
                return httpx.Response(
                    400, json={"error": {"code": "BadRequest", "message": "report not available"}}
                )
            return httpx.Response(200, json={"value": self._device_statuses(app_id)})
        if rest == "/assignments":
            return httpx.Response(200, json={"value": self.assignments[app_id]})
        if rest == "/assign":
            keys = [
                (a["target"].get("groupId") or a["target"]["@odata.type"])
                for a in body["mobileAppAssignments"]
            ]
            assert len(keys) == len(set(keys)), "duplicate assignment target"
            self.assignments[app_id] = [
                {"id": f"as-{n}", **a} for n, a in enumerate(body["mobileAppAssignments"])
            ]
            app["isAssigned"] = bool(keys)
            return httpx.Response(204)
        if rest == "/relationships":
            return httpx.Response(200, json={"value": self._relationships_of(app_id)})
        if rest == "/updateRelationships":
            for item in body["relationships"]:
                assert item["targetId"] in self.apps
            self.relationships[app_id] = body["relationships"]
            return httpx.Response(204)

        content = "/microsoft.graph.win32LobApp/contentVersions"
        if rest == content and method == "POST":
            return httpx.Response(201, json={"id": "1"})
        if rest == f"{content}/1/files" and method == "POST":
            assert body["sizeEncrypted"] > body["size"] > 0
            self.files[app_id] = {
                "id": "file-1",
                "uploadState": "azureStorageUriRequestSuccess",
                "azureStorageUri": STORAGE,
                **body,
            }
            return httpx.Response(201, json=self.files[app_id])
        if rest == f"{content}/1/files/file-1" and method == "GET":
            return httpx.Response(200, json=self.files[app_id])
        if rest == f"{content}/1/files/file-1/commit":
            info = body["fileEncryptionInfo"]
            assert info.pop("@odata.type") == "microsoft.graph.fileEncryptionInfo"
            assert set(info) == {
                "encryptionKey",
                "macKey",
                "initializationVector",
                "mac",
                "profileIdentifier",
                "fileDigest",
                "fileDigestAlgorithm",
            }
            self.encryption = body["fileEncryptionInfo"]
            self.files[app_id]["uploadState"] = (
                "commitFileFailed" if self.fail_commit else "commitFileSuccess"
            )
            return httpx.Response(200)
        return httpx.Response(404, json={"error": {"code": "NotImplemented", "message": path}})
