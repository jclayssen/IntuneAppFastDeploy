"""A tiny in-memory stand-in for the Intune part of Microsoft Graph.

It only checks that the tool sends a coherent sequence of calls. It is not
proof that the real service accepts the same payloads.
"""

from __future__ import annotations

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
        self.fail_commit = False
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
            app.update(body)
            return httpx.Response(204)
        if rest == "" and method == "DELETE":
            if any(i["targetId"] == app_id for items in self.relationships.values() for i in items):
                return httpx.Response(
                    400, json={"error": {"code": "BadRequest", "message": "app is superseded"}}
                )
            del self.apps[app_id]
            return httpx.Response(204)
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
            self.files[app_id]["uploadState"] = (
                "commitFileFailed" if self.fail_commit else "commitFileSuccess"
            )
            return httpx.Response(200)
        return httpx.Response(404, json={"error": {"code": "NotImplemented", "message": path}})
