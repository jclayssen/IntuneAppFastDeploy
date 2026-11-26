"""Microsoft Graph calls for Intune Win32 apps.

Every call uses the signed-in administrator's own token (delegated access):
this tool can never do more than that person can do in the Intune portal.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import httpx

from . import appmodel
from .intunewin import Package

BLOCK_SIZE = 6 * 1024 * 1024
SAS_RENEW_SECONDS = 7 * 60
WIN32_FILTER = "isof('microsoft.graph.win32LobApp')"
CONTENT = "microsoft.graph.win32LobApp/contentVersions"


class GraphError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class Graph:
    def __init__(
        self,
        token: Callable[[], str],
        base_url: str = "https://graph.microsoft.com/beta",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._sleep = sleep
        self._client = httpx.Client(timeout=httpx.Timeout(30, read=120), transport=transport)
        # Azure Storage must never receive the Graph token.
        self._storage = httpx.Client(timeout=httpx.Timeout(30, write=300), transport=transport)

    def close(self) -> None:
        self._client.close()
        self._storage.close()

    # ----------------------------------------------------------------- plumbing

    def request(self, method: str, path: str, json: dict | None = None, params=None) -> dict:
        url = path if path.startswith("https://") else f"{self._base}{path}"
        for attempt in range(5):
            response = self._client.request(
                method,
                url,
                json=json,
                params=params,
                headers={"Authorization": f"Bearer {self._token()}"},
            )
            if response.status_code in (429, 502, 503, 504) and attempt < 4:
                delay = float(response.headers.get("retry-after") or 2 * (attempt + 1))
                self._sleep(min(delay, 60))
                continue
            break
        if response.status_code >= 400:
            code, message = str(response.status_code), response.text[:500]
            try:
                error = response.json().get("error", {})
                code, message = error.get("code", code), error.get("message", message)
            except ValueError:
                pass
            raise GraphError(response.status_code, code, message)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def get_all(self, path: str, params: dict | None = None) -> list[dict]:
        items: list[dict] = []
        page = self.request("GET", path, params=params)
        while True:
            items.extend(page.get("value", []))
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return items
            page = self.request("GET", next_link)

    # ----------------------------------------------------------------- apps

    def list_apps(self) -> list[dict]:
        apps = self.get_all("/deviceAppManagement/mobileApps", {"$filter": WIN32_FILTER})
        return sorted(
            (appmodel.summarize(app) for app in apps), key=lambda app: app["name"].lower()
        )

    def get_app(self, app_id: str) -> dict:
        return self.request("GET", f"/deviceAppManagement/mobileApps/{app_id}")

    def create_app(self, body: dict) -> dict:
        return self.request("POST", "/deviceAppManagement/mobileApps", json=body)

    def patch_app(self, app_id: str, body: dict) -> None:
        self.request("PATCH", f"/deviceAppManagement/mobileApps/{app_id}", json=body)

    def delete_app(self, app_id: str) -> None:
        self.request("DELETE", f"/deviceAppManagement/mobileApps/{app_id}")

    def assignments(self, app_id: str) -> list[dict]:
        return self.get_all(f"/deviceAppManagement/mobileApps/{app_id}/assignments")

    def assign(self, app_id: str, assignments: list[dict]) -> None:
        """Replace all the assignments of an app."""
        self.request(
            "POST",
            f"/deviceAppManagement/mobileApps/{app_id}/assign",
            json={"mobileAppAssignments": assignments},
        )

    def relationships(self, app_id: str) -> list[dict]:
        return self.get_all(f"/deviceAppManagement/mobileApps/{app_id}/relationships")

    def update_relationships(self, app_id: str, relationships: list[dict]) -> None:
        self.request(
            "POST",
            f"/deviceAppManagement/mobileApps/{app_id}/updateRelationships",
            json={"relationships": relationships},
        )

    def search_groups(self, query: str) -> list[dict]:
        query = query.replace("'", "''").strip()
        params = {"$select": "id,displayName", "$top": "20", "$orderby": "displayName"}
        if query:
            params["$filter"] = f"startswith(displayName,'{query}')"
        result = self.request("GET", "/groups", params=params)
        return [{"id": g["id"], "name": g.get("displayName", "")} for g in result.get("value", [])]

    def group_name(self, group_id: str) -> str:
        result = self.request("GET", f"/groups/{group_id}", params={"$select": "displayName"})
        return result.get("displayName", "")

    # ----------------------------------------------------------------- device inventory

    def detected_apps(self) -> list[dict]:
        """Every software entry Intune discovered on the devices (all platforms)."""
        path = "/deviceManagement/detectedApps"
        try:
            # Large pages when the service accepts them: a tenant has thousands of rows.
            return self.get_all(path, {"$top": "500"})
        except GraphError as exc:
            if exc.status != 400:
                raise
            return self.get_all(path)

    def detected_app_devices(self, detected_id: str) -> list[dict]:
        return self.get_all(f"/deviceManagement/detectedApps/{detected_id}/managedDevices")

    def managed_devices(self) -> list[dict]:
        return self.get_all(
            "/deviceManagement/managedDevices",
            {
                "$filter": "operatingSystem eq 'Windows'",
                "$select": "id,deviceName,userPrincipalName,osVersion,lastSyncDateTime,"
                "manufacturer,model",
            },
        )

    def device_with_apps(self, device_id: str) -> dict:
        return self.request(
            "GET",
            f"/deviceManagement/managedDevices/{device_id}",
            params={"$expand": "detectedApps"},
        )

    # ----------------------------------------------------------------- content upload

    def _wait_file(self, file_url: str, wanted: str) -> dict:
        """Poll a content file until Intune reports ``<wanted>Success``."""
        for _ in range(120):
            info = self.request("GET", file_url)
            state = info.get("uploadState", "")
            if state == f"{wanted}Success":
                return info
            if state.endswith("Failed") or state.endswith("TimedOut"):
                raise GraphError(500, state, f"Intune reported {state}")
            self._sleep(5)
        raise GraphError(504, "timeout", f"Timed out waiting for {wanted}")

    def upload_content(
        self,
        app_id: str,
        package: Package,
        progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Send the encrypted package to Intune. Returns the content version id.

        The version still has to be committed on the app (``committedContentVersion``).
        """
        base = f"/deviceAppManagement/mobileApps/{app_id}/{CONTENT}"
        version = self.request("POST", base, json={})["id"]
        file_entry = self.request(
            "POST",
            f"{base}/{version}/files",
            json={
                "@odata.type": "#microsoft.graph.mobileAppContentFile",
                "name": "IntunePackage.intunewin",
                "size": package.unencrypted_size,
                "sizeEncrypted": package.encrypted_size,
                "manifest": None,
                "isDependency": False,
            },
        )
        file_url = f"{base}/{version}/files/{file_entry['id']}"
        sas_uri = self._wait_file(file_url, "azureStorageUriRequest")["azureStorageUri"]
        self._upload_blocks(package.encrypted_path, sas_uri, file_url, progress)
        self.request(
            "POST",
            f"{file_url}/commit",
            json={
                "fileEncryptionInfo": {
                    "@odata.type": "microsoft.graph.fileEncryptionInfo",
                    **package.encryption_info(),
                }
            },
        )
        self._wait_file(file_url, "commitFile")
        return version

    def _upload_blocks(
        self,
        path: Path,
        sas_uri: str,
        file_url: str,
        progress: Callable[[int, int], None] | None,
    ) -> None:
        total = path.stat().st_size
        block_ids: list[str] = []
        sent = 0
        started = time.monotonic()
        with path.open("rb") as handle:
            index = 0
            while chunk := handle.read(BLOCK_SIZE):
                if time.monotonic() - started > SAS_RENEW_SECONDS:
                    self.request("POST", f"{file_url}/renewUpload", json={})
                    sas_uri = self._wait_file(file_url, "azureStorageUriRenewal")["azureStorageUri"]
                    started = time.monotonic()
                block_id = base64.b64encode(f"{index:06d}".encode()).decode()
                self._put_storage(
                    f"{sas_uri}&comp=block&blockid={quote(block_id, safe='')}",
                    chunk,
                    {"x-ms-blob-type": "BlockBlob"},
                )
                block_ids.append(block_id)
                index += 1
                sent += len(chunk)
                if progress:
                    progress(sent, total)
        body = (
            '<?xml version="1.0" encoding="utf-8"?><BlockList>'
            + "".join(f"<Latest>{block_id}</Latest>" for block_id in block_ids)
            + "</BlockList>"
        )
        self._put_storage(
            f"{sas_uri}&comp=blocklist", body.encode(), {"Content-Type": "application/xml"}
        )

    def _put_storage(self, url: str, content: bytes, headers: dict) -> None:
        for attempt in range(4):
            try:
                response = self._storage.put(url, content=content, headers=headers)
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                self._sleep(2 * (attempt + 1))
                continue
            if response.status_code < 300:
                return
            if response.status_code < 500 or attempt == 3:
                raise GraphError(response.status_code, "storage", response.text[:300])
            self._sleep(2 * (attempt + 1))
