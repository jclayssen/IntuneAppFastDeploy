import pytest
from fastapi.testclient import TestClient

from iafd import inventory
from iafd.config import Settings
from iafd.web import create_app

from .demo import DemoAuth, build


@pytest.fixture
def client():
    with TestClient(build()) as test_client:
        test_client.headers["X-IAFD"] = "1"
        yield test_client


def test_group_apps_merges_versions_and_ignores_other_platforms():
    groups = inventory.group_apps(
        [
            {"id": "a", "displayName": "7-Zip", "version": "9.20", "deviceCount": 1},
            {
                "id": "b",
                "displayName": "7-zip",
                "version": "23.01",
                "deviceCount": 4,
                "publisher": "Igor Pavlov",
                "platform": "windows",
            },
            {
                "id": "c",
                "displayName": "Outlook",
                "version": "4",
                "platform": "ios",
                "deviceCount": 9,
            },
            {"id": "d", "displayName": "", "version": "1"},
        ]
    )
    assert len(groups) == 1
    assert groups[0]["installs"] == 5 and groups[0]["publisher"] == "Igor Pavlov"
    # Newest first, and 23.01 is newer than 9.20 (numbers, not text).
    assert [entry["version"] for entry in groups[0]["versions"]] == ["23.01", "9.20"]


def test_search_needs_every_word_and_paginate_stays_in_range():
    items = [{"name": "Google Chrome", "publisher": "Google LLC"}, {"name": "VLC", "publisher": ""}]
    assert inventory.search(items, "chrome google", ("name", "publisher")) == items[:1]
    assert inventory.search(items, "chrome vlc", ("name", "publisher")) == []
    page = inventory.paginate(list(range(120)), 99, size=50)
    assert page["page"] == 3 and page["pages"] == 3 and page["items"] == list(range(100, 120))
    assert inventory.paginate([], 1)["pages"] == 1


@pytest.mark.parametrize("value", ["../x", "a/b", "a?b=c", "", "x" * 200, "a b"])
def test_identifiers_that_could_alter_a_graph_url_are_refused(value):
    with pytest.raises(ValueError):
        inventory.safe_id(value)


def test_inventory_is_off_unless_enabled(tmp_path):
    settings = Settings(tenant_id="t", client_id="c", config_dir=tmp_path, work_dir=tmp_path / "w")
    assert "ManagedDevices" not in " ".join(settings.scopes)
    with TestClient(create_app(settings, auth=DemoAuth())) as off:
        assert off.get("/api/config").json()["device_inventory"] is False
        response = off.get("/api/inventory/apps")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "error.inventory_disabled"
    enabled = Settings(enable_device_inventory=True)
    assert enabled.scopes[-1].endswith("/DeviceManagementManagedDevices.Read.All")


def test_software_list_search_and_devices(client):
    listing = client.get("/api/inventory/apps").json()
    names = [item["name"] for item in listing["items"]]
    assert "Outlook" not in names and "Google Chrome" in names
    assert names == sorted(names, key=str.casefold)
    notepad = next(item for item in listing["items"] if item["name"].startswith("Notepad++"))
    assert [entry["version"] for entry in notepad["versions"]] == ["8.6", "7.9.5"]
    assert notepad["installs"] == 3

    found = client.get("/api/inventory/apps", params={"q": "oracle"}).json()
    assert [item["name"] for item in found["items"]] == ["Java 8 Update 201 (64-bit)"]

    old = notepad["versions"][1]
    devices = client.get(f"/api/inventory/apps/{old['id']}/devices").json()["devices"]
    assert [device["name"] for device in devices] == ["PC-ATELIER-04"]
    assert devices[0]["user"] == "atelier@example.test"


def test_device_list_and_its_software(client):
    devices = client.get("/api/inventory/devices", params={"q": "compta"}).json()
    assert [device["name"] for device in devices["items"]] == ["PC-COMPTA-02", "PC-COMPTA-03"]
    detail = client.get(f"/api/inventory/devices/{devices['items'][1]['id']}").json()
    assert detail["device"]["name"] == "PC-COMPTA-03"
    assert [app["name"] for app in detail["apps"]] == [
        "7-Zip 19.00 (x64 edition)",
        "Google Chrome",
        "Java 8 Update 201 (64-bit)",
    ]


def test_bad_identifier_never_reaches_intune(client):
    response = client.get("/api/inventory/devices/a%3Fb")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "error.bad_id"


def test_the_inventory_is_cached_between_requests(client, monkeypatch):
    from iafd.graph import Graph

    calls = []
    original = Graph.detected_apps
    monkeypatch.setattr(Graph, "detected_apps", lambda self: calls.append(1) or original(self))
    client.get("/api/inventory/apps")
    client.get("/api/inventory/apps", params={"q": "zip"})
    assert len(calls) == 1
    client.get("/api/inventory/apps", params={"refresh": "true"})
    assert len(calls) == 2
