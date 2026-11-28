"""The HTTP layer, with sign-in bypassed and Intune replaced by the fake."""

import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from iafd.config import Settings
from iafd.web import create_app

from .demo import build


@pytest.fixture
def client():
    with TestClient(build()) as test_client:
        test_client.headers["X-IAFD"] = "1"
        yield test_client


def wait(client, job_id, states):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {job['state']}")


def test_unauthenticated_requests_are_refused(tmp_path):
    settings = Settings(tenant_id="t", client_id="c", config_dir=tmp_path, work_dir=tmp_path / "w")
    with TestClient(create_app(settings)) as anonymous:
        assert anonymous.get("/api/config").json()["configured"] is True
        for path in ("/api/me", "/api/apps", "/api/jobs/x", "/api/groups"):
            response = anonymous.get(path)
            assert response.status_code == 401, path
            assert response.json()["error"]["code"] == "error.sign_in_required"
        assert (
            anonymous.post("/api/analyze", json={"url": "https://x.test/a.exe"}).status_code == 401
        )
        assert anonymous.get("/").status_code == 200
        assert "frame-ancestors" in anonymous.get("/").headers["content-security-policy"]


def test_writes_need_the_anti_csrf_header(client):
    del client.headers["X-IAFD"]
    response = client.put("/api/apps/app-2/rollout", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "error.csrf"


def test_app_list_and_detail(client):
    apps = client.get("/api/apps").json()["apps"]
    assert [app["name"] for app in apps][0].startswith("7-Zip 22.01")
    detail = client.get("/api/apps/app-2").json()
    assert detail["stage"] == "assigned"
    assert detail["older"][0]["id"] == "app-1"
    assert detail["app"]["detection"]["type"] == "msi"


def test_rollout_groups_are_validated(client):
    bad = {"pilot": {"targets": [{"type": "group", "id": "nope"}]}}
    response = client.put("/api/apps/app-2/rollout", json=bad)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "error.bad_group_id"


def test_upload_review_and_publish(client, tmp_path):
    exe = b"MZ" + b"\\x00" * 4096
    response = client.post(
        "/api/analyze/upload",
        files={"file": ("npp.8.7.0.Installer.x64.exe", exe)},
        data={"app_id": "app-3"},
    )
    assert response.status_code == 200, response.text
    job = wait(client, response.json()["id"], {"ready", "error"})
    assert job["state"] == "ready", job
    form = job["result"]["form"]
    assert form["install_command"] == '"npp.8.7.0.Installer.x64.exe" /S'
    assert "path" not in job["result"] and "existing" in job["result"]

    package = client.get(f"/api/jobs/{job['id']}/intunewin")
    assert package.status_code == 200 and package.content[:2] == b"PK"

    form.update(mode="supersede", rollout="prod", version="8.7.0")
    form["detection"]["value"] = "8.7.0"
    started = client.post(f"/api/jobs/{job['id']}/publish", json=form)
    assert started.status_code == 200, started.text
    done = wait(client, job["id"], {"done", "ready"})
    assert done["state"] == "done", done
    new_id = done["result"]["outcome"]["app_id"]
    detail = client.get(f"/api/apps/{new_id}").json()
    assert detail["app"]["version"] == "8.7.0"
    assert detail["older"][0]["id"] == "app-3"


def test_incomplete_form_is_refused_with_a_translatable_error(client):
    response = client.post(
        "/api/analyze/upload", files={"file": ("new.exe", b"MZ" + b"\\x00" * 100)}, data={}
    )
    job = wait(client, response.json()["id"], {"ready", "error"})
    form = job["result"]["form"]
    refused = client.post(f"/api/jobs/{job['id']}/publish", json=form)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "error.publisher_required"


def test_a_web_page_is_not_an_installer(client, monkeypatch):
    from iafd import web

    def fake_download(url, dest, limit, progress=None):
        from iafd.download import download

        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>"))
        return download(url, dest, limit, progress, transport=transport)

    monkeypatch.setattr(web, "download", fake_download)
    response = client.post("/api/analyze", json={"url": "https://example.test/download"})
    job = wait(client, response.json()["id"], {"ready", "error"})
    assert job["state"] == "error"
    assert job["error"]["code"] == "error.not_installer"


def test_language_files_cannot_be_used_to_read_other_files(client):
    assert client.get("/locales/fr.json").json()["_name"] == "Français"
    for name in ("..%2F..%2Fconfig", "..%2Fstatic%2Fapp", "nope", "%2Fetc%2Fpasswd"):
        assert client.get(f"/locales/{name}.json").status_code == 404, name


def test_sign_out_button_is_hidden_until_someone_signs_in(tmp_path):
    settings = Settings(tenant_id="t", client_id="c", config_dir=tmp_path, work_dir=tmp_path / "w")
    with TestClient(create_app(settings)) as anonymous:
        page = anonymous.get("/").text
        css = anonymous.get("/style.css").text
    # The page ships the button hidden; the interface only reveals it once /api/me answers.
    assert re.search(r'<button id="logout"[^>]*\shidden[\s>]', page)
    # "button { display: ... }" beats the browser's own rule for the hidden attribute:
    # without this rule the attribute has no effect and the button shows on the sign-in screen.
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", css)


def test_app_detail_tells_the_install_context_and_the_notification_setting(client):
    detail = client.get("/api/apps/app-2").json()
    assert detail["app"]["run_as"] == "system"
    assert detail["rollout"]["notifications"] == "showAll"
    assert detail["assignments"][0]["notifications"] is None  # made outside the tool

    client.app.state.intune.apps["app-2"]["installExperience"] = {"runAsAccount": "user"}
    assert client.get("/api/apps/app-2").json()["app"]["run_as"] == "user"


def test_notification_setting_is_saved_with_the_groups_and_validated(client):
    quiet = {"name": "7-Zip", "notifications": "hideAll"}
    saved = client.put("/api/apps/app-2/rollout", json=quiet)
    assert saved.status_code == 200, saved.text
    assert client.get("/api/apps/app-2").json()["rollout"]["notifications"] == "hideAll"
    # Saving the groups alone does not reset it.
    client.put("/api/apps/app-2/rollout", json={"name": "7-Zip"})
    assert client.get("/api/apps/app-2").json()["rollout"]["notifications"] == "hideAll"

    refused = client.put("/api/apps/app-2/rollout", json={"notifications": "nothing"})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "error.bad_notifications"


def test_review_form_of_an_update_carries_the_current_settings(client):
    intune = client.app.state.intune
    intune.apps["app-3"]["installExperience"] = {"runAsAccount": "user"}
    client.put("/api/apps/app-3/rollout", json={"name": "Notepad++", "notifications": "showReboot"})
    response = client.post(
        "/api/analyze/upload",
        files={"file": ("npp.8.7.0.Installer.x64.exe", b"MZ" + b"\\x00" * 4096)},
        data={"app_id": "app-3"},
    )
    job = wait(client, response.json()["id"], {"ready", "error"})
    assert job["state"] == "ready", job
    assert job["result"]["form"]["run_as"] == "user"
    assert job["result"]["rollout"]["notifications"] == "showReboot"

    form = job["result"]["form"]
    form.update(mode="supersede", rollout="none", version="8.7.0", notifications="loud")
    form["detection"]["value"] = "8.7.0"
    apps_before = len(intune.apps)
    refused = client.post(f"/api/jobs/{job['id']}/publish", json=form)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "error.bad_notifications"
    assert len(intune.apps) == apps_before  # nothing was created


def test_advanced_settings_of_the_current_app_reach_the_review_screen(client):
    intune = client.app.state.intune
    intune.apps["app-3"].update(
        minimumMemoryInMB=4096,
        minimumSupportedWindowsRelease="21H1",
        installExperience={"runAsAccount": "system", "maxRunTimeInMinutes": 90},
    )
    assert "Windows11_23H2" in client.get("/api/config").json()["windows_releases"]
    assert client.get("/api/apps/app-3").json()["app"]["advanced"]["memory_mb"] == 4096

    response = client.post(
        "/api/analyze/upload",
        files={"file": ("npp.8.7.0.Installer.x64.exe", b"MZ" + b"\\x00" * 4096)},
        data={"app_id": "app-3"},
    )
    job = wait(client, response.json()["id"], {"ready", "error"})
    form = job["result"]["form"]
    assert form["advanced"] == {
        "disk_space_mb": None,
        "memory_mb": 4096,
        "processors": None,
        "cpu_speed_mhz": None,
        "windows_release": "21H1",
        "restart_behavior": "basedOnReturnCode",
        "max_run_time": 90,
        "allow_uninstall": False,
    }

    form.update(mode="inplace", version="8.7.0")
    form["detection"]["value"] = "8.7.0"
    form["advanced"]["processors"] = "two"
    refused = client.post(f"/api/jobs/{job['id']}/publish", json=form)
    assert refused.status_code == 400
    assert refused.json()["error"] == {
        "code": "error.advanced_number",
        "detail": "minimumNumberOfProcessors",
    }

    form["advanced"].update(processors="2", disk_space_mb="750")
    started = client.post(f"/api/jobs/{job['id']}/publish", json=form)
    assert started.status_code == 200, started.text
    done = wait(client, job["id"], {"done", "ready"})
    assert done["state"] == "done", done
    app = intune.apps["app-3"]
    assert app["minimumNumberOfProcessors"] == 2 and app["minimumFreeDiskSpaceInMB"] == 750
    assert app["minimumMemoryInMB"] == 4096 and app["minimumSupportedWindowsRelease"] == "21H1"
    assert app["installExperience"] == {"runAsAccount": "system", "maxRunTimeInMinutes": 90}
