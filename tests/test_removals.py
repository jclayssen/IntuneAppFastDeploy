"""Removal rules: protected list, Intune payloads and the whole flow on the fake tenant."""

import base64
import io
import json
import re
import time
import zipfile

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

from iafd import appmodel, protected, removal

from .demo import build

GROUP = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def client():
    with TestClient(build()) as test_client:
        test_client.headers["X-IAFD"] = "1"
        yield test_client


def wait(client, job):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# --------------------------------------------------------------------------- protected list


@pytest.mark.parametrize(
    "name",
    [
        "Microsoft Intune Management Extension",
        "Company Portal",
        "Microsoft.CompanyPortal",
        "Microsoft Defender for Endpoint",
        "Microsoft Edge WebView2 Runtime",
        "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.40.33810",
        "Microsoft .NET Runtime - 8.0.6 (x64)",
        "Microsoft 365 Apps for enterprise - fr-fr",
        "Intel(R) Graphics Driver",
        "Realtek Audio Driver",
        "Microsoft.WindowsStore",
    ],
)
def test_essential_software_is_protected(name):
    assert protected.is_protected(name)


@pytest.mark.parametrize(
    "name",
    [
        "7-Zip 19.00 (x64 edition)",
        "Java 8 Update 201 (64-bit)",
        "WinRAR 5.91 (64-bit)",
        "Driver Booster 11",
        "Microsoft.BingWeather",
        "TeamViewer",
        "Zoom Workplace",
    ],
)
def test_ordinary_software_is_not_protected(name):
    assert not protected.is_protected(name)


def test_administrators_can_protect_more(tmp_path):
    (tmp_path / "protected.txt").write_text(
        "# our line-of-business tools\nContoso ERP\nre:^contoso .* agent$\nre:([broken\n", "utf-8"
    )
    extra = protected.load_extra(tmp_path)
    assert protected.blocked(
        ["contoso erp", "Contoso Backup Agent", "Contoso ERP Viewer"], extra
    ) == ["contoso erp", "Contoso Backup Agent"]
    assert protected.load_extra(tmp_path / "missing") == []


# --------------------------------------------------------------------------- pure functions


def test_matching_installs_follows_the_version_rule():
    groups = [
        {
            "name": "Notepad++ (64-bit x64)",
            "versions": [{"version": "8.6", "devices": 2}, {"version": "7.9.5", "devices": 1}],
        },
        {"name": "Notepad++ Plugin", "versions": [{"version": "1.0", "devices": 9}]},
    ]
    below = removal.clean_config(
        {"names": ["notepad++ (64-bit x64)"], "versions": {"mode": "below", "value": "8.0"}}
    )
    assert removal.matching_installs(groups, below) == [
        {"name": "Notepad++ (64-bit x64)", "version": "7.9.5", "devices": 1}
    ]
    everything = removal.clean_config({"names": ["Notepad++ (64-bit x64)"]})
    assert sum(row["devices"] for row in removal.matching_installs(groups, everything)) == 3
    assert not removal.version_wanted({"mode": "below", "value": "8.0"}, "unknown")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8.0.2010.9", (8, 0, 2010, 9)),
        ("  23.01", (23, 1, 0, 0)),
        ("1.2 beta", (1, 2, 0, 0)),
        ("1.2.3.4.5", (1, 2, 3, 4)),
        ("7", (7, 0, 0, 0)),
        ("1..2", (1, 0, 0, 0)),
        ("1.x", (1, 0, 0, 0)),
        ("beta", None),
        ("v1.0", None),
        ("", None),
        ("9" * 5000, None),
    ],
)
def test_version_key_reads_leading_numbers_only(text, expected):
    assert removal.version_key(text) == expected


def test_removal_application_only_knows_how_to_uninstall():
    body = removal.app_body({"names": ["Zoom Workplace"], "kind": "user"})
    assert body["displayName"] == "Removal - Zoom Workplace"
    assert body["installCommandLine"] == "cmd.exe /c exit 0"
    assert body["uninstallCommandLine"].endswith(
        '-File "Remove-zoom-workplace.ps1" -Mode Uninstall'
    )
    assert body["setupFilePath"] == "Remove-zoom-workplace.ps1"
    assert body["installExperience"]["runAsAccount"] == "user"
    rule = body["rules"][0]
    assert rule["ruleType"] == "detection" and rule["runAs32Bit"] is False
    script = base64.b64decode(rule["scriptContent"])
    assert script.startswith(b"\xef\xbb\xbf")
    assert "[string]$Mode = 'Detect'" in script.decode("utf-8-sig")
    assert removal.app_body({"names": ["VLC"]})["installExperience"]["runAsAccount"] == "system"


def test_uninstall_replaces_what_a_target_had_and_can_be_withdrawn():
    devices = {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"}
    group = {"@odata.type": "#microsoft.graph.groupAssignmentTarget", "groupId": GROUP}
    current = [
        {"id": "1", "intent": "required", "target": devices},
        {"id": "2", "intent": "available", "target": group},
    ]
    switched = appmodel.with_uninstall(current, [{"type": "group", "id": GROUP}])
    assert [(a["intent"], appmodel.target_key(a["target"])) for a in switched] == [
        ("required", "#microsoft.graph.alldevicesassignmenttarget"),
        ("uninstall", GROUP),
    ]
    assert switched[1]["settings"]["notifications"] == "hideAll"
    withdrawn = appmodel.with_uninstall(switched, [])
    assert [a["intent"] for a in withdrawn] == ["required"]


def test_status_rows_put_failures_first_and_count():
    result = removal.status_rows(
        [
            {"deviceName": "B", "installState": "notInstalled"},
            {"deviceName": "A", "installState": "installed"},
            {"deviceName": "C", "installState": "uninstallFailed", "errorCode": -2016345060},
            {"deviceName": "D", "installState": "somethingNew"},
        ]
    )
    assert [row["device"] for row in result["rows"]] == ["C", "A", "B", "D"]
    assert result["rows"][0]["error"] == "0x87D1041C"
    assert result["counts"] == {"failed": 1, "pending": 1, "removed": 1, "unknown": 1}


# --------------------------------------------------------------------------- the whole flow


def test_preview_counts_devices_and_flags_protected_software(client):
    preview = client.post(
        "/api/removals/preview", json={"config": {"names": ["google chrome"]}}
    ).json()
    assert preview["installs"] == 7 and preview["confirm"] == "google chrome"
    assert preview["protected"] == []
    flagged = client.post(
        "/api/removals/preview", json={"config": {"names": ["Microsoft Edge WebView2 Runtime"]}}
    ).json()
    assert flagged["protected"] == ["Microsoft Edge WebView2 Runtime"]


def test_guardrails_are_enforced_by_the_server(client):
    intune = client.app.state.intune
    before = len(intune.apps)
    protected_one = client.post(
        "/api/removals",
        json={"config": {"names": ["Microsoft Defender"]}, "confirm": "Microsoft Defender"},
    )
    assert protected_one.status_code == 400
    assert protected_one.json()["error"] == {
        "code": "error.removal_protected",
        "detail": "Microsoft Defender",
    }
    unconfirmed = client.post(
        "/api/removals",
        json={"config": {"names": ["Java 8 Update 201 (64-bit)"]}, "confirm": "java"},
    )
    assert unconfirmed.json()["error"]["code"] == "error.removal_confirm"
    bad_target = client.post(
        "/api/removals",
        json={
            "config": {"names": ["TeamViewer"]},
            "confirm": "TeamViewer",
            "targets": [{"type": "group", "id": "everyone"}],
        },
    )
    assert bad_target.json()["error"]["code"] == "error.bad_group_id"
    assert len(intune.apps) == before and client.get("/api/removals").json()["rules"] == []


def test_removal_from_creation_to_the_end(client):
    intune = client.app.state.intune
    name = "Java 8 Update 201 (64-bit)"
    job = wait(
        client,
        client.post(
            "/api/removals",
            json={
                "config": {"names": [name]},
                "confirm": name,
                "permanent": True,
                "targets": [{"type": "allDevices"}],
            },
        ).json(),
    )
    assert job["state"] == "done", job
    assert job["steps"] == [
        "step.package",
        "step.create",
        "step.upload",
        "step.commit",
        "step.assign",
    ]
    rule_id = job["result"]["removal"]["id"]

    # In Intune: one application, uninstall intent only, the script as content.
    app = intune.apps[rule_id]
    assert app["displayName"] == "Removal - " + name and app["committedContentVersion"] == "1"
    assert [a["intent"] for a in intune.assignments[rule_id]] == ["uninstall"]
    script = base64.b64decode(app["rules"][0]["scriptContent"]).decode("utf-8-sig")
    embedded = json.loads(base64.b64decode(re.search(r"ConfigBase64 = '([^']+)'", script).group(1)))
    assert embedded["names"] == [name]

    # Locally: the rule is in config/removals.json, with who created it.
    stored = json.loads((client.app.state.settings.config_dir / "removals.json").read_text("utf-8"))
    assert stored["rules"][rule_id]["created_by"] == "admin@example.test"
    assert stored["rules"][rule_id]["permanent"] is True

    # Before the devices check in: two devices pending.
    detail = client.get(f"/api/removals/{rule_id}").json()
    assert detail["results"]["counts"] == {"pending": 2} and detail["remaining"] == 2
    assert detail["portal_url"].endswith(rule_id)

    # After they check in: removed everywhere, nothing left in the inventory.
    intune.sync()
    client.get("/api/inventory/apps", params={"refresh": "true"})
    detail = client.get(f"/api/removals/{rule_id}").json()
    assert detail["results"]["counts"] == {"removed": 2} and detail["remaining"] == 0
    assert sorted(row["device"] for row in detail["results"]["rows"]) == [
        "PC-COMPTA-02",
        "PC-COMPTA-03",
    ]

    # Narrow the targets, then end the rule: its Intune application disappears.
    narrowed = client.put(
        f"/api/removals/{rule_id}/targets", json={"targets": [{"type": "group", "id": GROUP}]}
    ).json()
    assert narrowed["rule"]["targets"] == [{"type": "group", "id": GROUP, "name": ""}]
    assert intune.assignments[rule_id][0]["target"]["groupId"] == GROUP
    assert client.post(f"/api/removals/{rule_id}/finish").json() == {"ok": True}
    assert rule_id not in intune.apps and client.get("/api/removals").json()["rules"] == []
    assert client.get(f"/api/removals/{rule_id}").status_code == 404


def test_a_program_without_silent_uninstall_fails_until_a_command_is_given(client):
    intune = client.app.state.intune
    name = "WinRAR 5.91 (64-bit)"
    job = wait(
        client,
        client.post(
            "/api/removals",
            json={
                "config": {"names": [name]},
                "confirm": name,
                "targets": [{"type": "allDevices"}],
            },
        ).json(),
    )
    rule_id = job["result"]["removal"]["id"]
    intune.sync()
    detail = client.get(f"/api/removals/{rule_id}").json()
    assert detail["results"]["counts"] == {"failed": 2}
    assert detail["results"]["rows"][0]["error"].startswith("0x")

    updated = wait(
        client,
        client.put(
            f"/api/removals/{rule_id}",
            json={"config": {"names": [name], "command": "{UninstallString} /S"}, "confirm": name},
        ).json(),
    )
    assert updated["state"] == "done", updated
    assert intune.apps[rule_id]["committedContentVersion"] == "1"
    assert client.get(f"/api/removals/{rule_id}").json()["rule"]["config"]["command"] == (
        "{UninstallString} /S"
    )
    intune.sync()
    assert client.get(f"/api/removals/{rule_id}").json()["results"]["counts"] == {"removed": 2}


def test_results_degrade_gracefully_when_intune_refuses_the_report(client):
    name = "TeamViewer"
    job = wait(
        client,
        client.post("/api/removals", json={"config": {"names": [name]}, "confirm": name}).json(),
    )
    client.app.state.intune.fail_statuses = True
    detail = client.get(f"/api/removals/{job['result']['removal']['id']}").json()
    assert detail["results"]["available"] is False and "report" in detail["results"]["detail"]
    assert detail["remaining"] == 2


def test_an_intune_app_can_be_removed_from_a_group_and_the_removal_withdrawn(client):
    intune = client.app.state.intune
    detail = client.post(
        "/api/apps/app-2/uninstall", json={"targets": [{"type": "group", "id": GROUP}]}
    ).json()
    intents = sorted(item["intent"] for item in detail["assignments"])
    assert intents == ["required", "uninstall"]
    assert len(intune.assignments["app-2"]) == 2
    withdrawn = client.post("/api/apps/app-2/uninstall", json={"targets": []}).json()
    assert [item["intent"] for item in withdrawn["assignments"]] == ["required"]


def test_removals_need_the_inventory_option(tmp_path):
    from iafd.config import Settings
    from iafd.web import create_app

    from .demo import DemoAuth

    settings = Settings(tenant_id="t", client_id="c", config_dir=tmp_path, work_dir=tmp_path / "w")
    with TestClient(create_app(settings, auth=DemoAuth())) as off:
        off.headers["X-IAFD"] = "1"
        assert off.get("/api/removals").status_code == 404
        assert off.post("/api/removals", json={}).status_code == 404


# --------------------------------------------------------------------------- older versions


def exe_analysis():
    return {
        "kind": "exe",
        "file_name": "setup-2.0.exe",
        "version": "2.0",
        "product_name": "App",
        "publisher": "Pub",
        "architecture": "any",
        "install_command": '"setup-2.0.exe" /S',
        "uninstall_command": "",
        "msi": {},
    }


def replace_form(**replace):
    form = appmodel.propose_form(exe_analysis(), None)
    form.update(
        uninstall_command="u /S",
        detection={
            "type": "file",
            "path": "C:\\App",
            "name": "app.exe",
            "operator": "version_ge",
            "value": "2.0",
        },
    )
    form["replace"] = {"enabled": True, "names": ["Old App"], **replace}
    return form


def test_upgrade_only_where_an_older_version_is_present():
    body = appmodel.new_app_body(replace_form(), exe_analysis())
    assert body["installCommandLine"] == '"setup-2.0.exe" /S'  # the installer does the upgrade
    assert body["setupFilePath"] == "setup-2.0.exe"
    requirement = [rule for rule in body["rules"] if rule["ruleType"] == "requirement"]
    assert len(requirement) == 1 and requirement[0]["comparisonValue"] == "present"
    script = base64.b64decode(requirement[0]["scriptContent"]).decode("utf-8-sig")
    assert "[string]$Mode = 'Requirement'" in script
    embedded = json.loads(base64.b64decode(re.search(r"ConfigBase64 = '([^']+)'", script).group(1)))
    # Older than the version being published, unless another limit is given.
    assert embedded["versions"] == {"mode": "below", "value": "2.0"}
    custom = appmodel.new_app_body(replace_form(below="1.5"), exe_analysis())
    script = base64.b64decode(custom["rules"][-1]["scriptContent"]).decode("utf-8-sig")
    assert (
        json.loads(base64.b64decode(re.search(r"ConfigBase64 = '([^']+)'", script).group(1)))[
            "versions"
        ]["value"]
        == "1.5"
    )


def test_remove_first_wraps_the_install_command():
    body = appmodel.new_app_body(replace_form(mode="reinstall"), exe_analysis())
    assert body["setupFilePath"] == "Replace-old-app.ps1"
    assert body["installCommandLine"].endswith('-File "Replace-old-app.ps1"')
    wrapper = removal.render_replace_script(
        removal.clean_replace({"enabled": True, "names": ["Old App"], "mode": "reinstall"}, "2.0")[
            "config"
        ],
        '"setup-2.0.exe" /S',
    )
    payload = json.loads(base64.b64decode(re.search(r"ConfigBase64 = '([^']+)'", wrapper).group(1)))
    assert payload == {
        "remove_script": "Remove-old-app.ps1",
        "install_command": '"setup-2.0.exe" /S',
    }
    assert "setup-2.0.exe" not in wrapper  # typed text never becomes code


def test_replace_is_optional_and_validated():
    form = replace_form()
    form["replace"]["enabled"] = False
    assert all(
        r["ruleType"] == "detection" for r in appmodel.new_app_body(form, exe_analysis())["rules"]
    )
    with pytest.raises(removal.RemovalError, match="error.removal_no_name"):
        appmodel.new_app_body(replace_form(names=[]), exe_analysis())
    with pytest.raises(removal.RemovalError, match="error.replace_bad_mode"):
        appmodel.new_app_body(replace_form(mode="magic"), exe_analysis())
    with pytest.raises(removal.RemovalError, match="error.removal_bad_versions"):
        appmodel.new_app_body(replace_form(below="soon"), exe_analysis())


def test_a_new_version_renews_the_requirement_instead_of_stacking_it():
    first = appmodel.new_app_body(replace_form(), exe_analysis())
    existing = {**first, "id": "a", "displayVersion": "2.0"}
    analysis = {**exe_analysis(), "version": "3.0", "file_name": "setup-3.0.exe"}
    form = appmodel.propose_form(analysis, existing)
    form["replace"] = {"enabled": True, "names": ["Old App"]}
    renewed = appmodel.clone_app_body(existing, form, analysis)
    requirement = [rule for rule in renewed["rules"] if rule["ruleType"] == "requirement"]
    assert len(requirement) == 1
    script = base64.b64decode(requirement[0]["scriptContent"]).decode("utf-8-sig")
    assert (
        json.loads(base64.b64decode(re.search(r"ConfigBase64 = '([^']+)'", script).group(1)))[
            "versions"
        ]["value"]
        == "3.0"
    )
    # Without the option, the rule added by the tool is dropped rather than left stale.
    form["replace"] = {"enabled": False}
    assert all(
        r["ruleType"] == "detection"
        for r in appmodel.clone_app_body(existing, form, analysis)["rules"]
    )


def test_publishing_with_remove_first_ships_the_three_files(client, tmp_path):

    upload = client.post(
        "/api/analyze/upload", files={"file": ("setup-2.0.exe", b"MZ" + b"\x00" * 300)}, data={}
    )
    job = wait_ready(client, upload.json())
    form = job["result"]["form"]
    form.update(
        mode="new",
        publisher="Pub",
        uninstall_command="u /S",
        rollout="none",
        detection={
            "type": "file",
            "path": "C:\\App",
            "name": "app.exe",
            "operator": "version_ge",
            "value": "2.0",
        },
        replace={
            "enabled": True,
            "names": ["WinRAR 5.91 (64-bit)"],
            "mode": "reinstall",
            "below": "7.0",
            "command": "{UninstallString} /S",
        },
    )
    protected_first = {**form, "replace": {**form["replace"], "names": ["Microsoft Defender"]}}
    refused = client.post(f"/api/jobs/{job['id']}/publish", json=protected_first)
    assert refused.json()["error"]["code"] == "error.removal_protected"

    done = wait(client, client.post(f"/api/jobs/{job['id']}/publish", json=form).json())
    assert done["state"] == "done", done
    intune = client.app.state.intune
    app = intune.apps[done["result"]["outcome"]["app_id"]]
    assert app["setupFilePath"] == "Replace-winrar-5-91-64-bit.ps1"
    assert [rule["ruleType"] for rule in app["rules"]] == ["detection", "requirement"]

    # What was uploaded decrypts to the installer plus the two scripts.
    info = intune.encryption
    blob = intune.committed_blob
    decryptor = Cipher(
        algorithms.AES(base64.b64decode(info["encryptionKey"])),
        modes.CBC(base64.b64decode(info["initializationVector"])),
    ).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    plain = (
        unpadder.update(decryptor.update(blob[48:]) + decryptor.finalize()) + unpadder.finalize()
    )
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        assert sorted(archive.namelist()) == [
            "Remove-winrar-5-91-64-bit.ps1",
            "Replace-winrar-5-91-64-bit.ps1",
            "setup-2.0.exe",
        ]
        remove = archive.read("Remove-winrar-5-91-64-bit.ps1").decode("utf-8-sig")
    assert "[string]$Mode = 'Uninstall'" in remove


def wait_ready(client, job):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["state"] in ("ready", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_a_package_can_hold_several_files(tmp_path):
    from iafd import intunewin

    main, extra = tmp_path / "Replace-x.ps1", tmp_path / "setup.exe"
    main.write_text("wrapper", "utf-8")
    extra.write_bytes(b"MZ" + b"\x01" * 2000)
    package = intunewin.build_package(main, tmp_path / "pkg", [extra])
    assert package.setup_file == "Replace-x.ps1"
    out = intunewin.extract_intunewin(
        intunewin.write_intunewin(package, tmp_path / "x.intunewin"), tmp_path / "out"
    )
    assert sorted(p.name for p in out.iterdir()) == ["Replace-x.ps1", "setup.exe"]
    assert (out / "setup.exe").read_bytes() == extra.read_bytes()
    with pytest.raises(ValueError):
        intunewin.build_package(main, tmp_path / "pkg2", [tmp_path / "REPLACE-X.PS1"])
    assert zipfile.is_zipfile(tmp_path / "x.intunewin")
