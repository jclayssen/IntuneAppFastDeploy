import pytest

from iafd import appmodel
from iafd.store import RolloutStore, StoreError


def exe_analysis(version="2.0"):
    return {
        "kind": "exe",
        "file_name": "setup-2.0.exe",
        "version": version,
        "product_name": "App",
        "publisher": "Pub",
        "architecture": "any",
        "install_command": '"setup-2.0.exe" /S',
        "uninstall_command": "",
        "msi": {},
    }


def file_rule(operation="version", operator="greaterThanOrEqual", value="1.0"):
    return {
        "@odata.type": appmodel.RULE_FILE,
        "ruleType": "detection",
        "path": "C:\\Program Files\\App",
        "fileOrFolderName": "app.exe",
        "check32BitOn64System": False,
        "operationType": operation,
        "operator": operator,
        "comparisonValue": value,
    }


def test_exe_update_moves_version_detection_to_the_new_version():
    existing = {
        "id": "a",
        "displayName": "App",
        "displayVersion": "1.0",
        "setupFilePath": "setup-1.0.exe",
        "installCommandLine": '"setup-1.0.exe" /S',
        "uninstallCommandLine": '"C:\\Program Files\\App\\uninstall.exe" /S',
        "rules": [file_rule()],
    }
    form = appmodel.propose_form(exe_analysis(), existing)
    assert form["install_command"] == '"setup-2.0.exe" /S'
    assert form["uninstall_command"] == existing["uninstallCommandLine"]
    assert form["detection"]["type"] == "file" and form["detection"]["value"] == "2.0"
    body = appmodel.clone_app_body(existing, form, exe_analysis())
    assert body["rules"] == [file_rule(value="2.0")]
    assert "id" not in body and body["msiInformation"] is None


def test_detection_round_trips_through_the_form_model():
    for rule in (file_rule(), file_rule("exists", "notConfigured", None)):
        assert appmodel.detection_rules(appmodel.detection_model([rule])) == [rule]


def test_complex_detection_is_kept_and_bumped():
    script = {
        "@odata.type": "#microsoft.graph.win32LobAppPowerShellScriptRule",
        "ruleType": "detection",
        "scriptContent": "abc",
    }
    existing = {
        "id": "a",
        "displayName": "App",
        "displayVersion": "1.0",
        "setupFilePath": "setup-1.0.exe",
        "installCommandLine": '"setup-1.0.exe" /S',
        "uninstallCommandLine": "u",
        "rules": [file_rule(), script],
    }
    form = appmodel.propose_form(exe_analysis(), existing)
    assert form["detection"]["type"] == "keep"
    assert "warning.detection_kept" in form["warnings"]
    body = appmodel.clone_app_body(existing, form, exe_analysis())
    assert body["rules"] == [file_rule(value="2.0"), script]


def test_unusual_operator_is_not_silently_rewritten():
    assert appmodel.detection_model([file_rule(operator="equal")])["type"] == "keep"


def test_legacy_detection_rules_are_converted():
    app = {
        "detectionRules": [
            {
                "@odata.type": "#microsoft.graph.win32LobAppFileSystemDetection",
                "path": "C:\\App",
                "fileOrFolderName": "a.exe",
                "check32BitOn64System": False,
                "detectionType": "version",
                "operator": "greaterThanOrEqual",
                "detectionValue": "1.0",
            }
        ]
    }
    model = appmodel.detection_model(appmodel.app_rules(app))
    assert model["type"] == "file" and model["value"] == "1.0"


@pytest.mark.parametrize(
    ("detection", "error"),
    [
        ({"type": "msi", "product_code": "nope"}, "error.bad_product_code"),
        ({"type": "file", "path": "", "name": "a", "operator": "exists"}, "error.detection_file"),
        (
            {"type": "file", "path": "p", "name": "a", "operator": "version_ge", "value": ""},
            "error.detection_value",
        ),
        ({"type": "registry", "key": "", "operator": "exists"}, "error.detection_registry"),
        (
            {"type": "file", "path": "p", "name": "a", "operator": "string_eq", "value": "x"},
            "error.bad_detection",
        ),
    ],
)
def test_invalid_detection_is_refused(detection, error):
    with pytest.raises(appmodel.FormError, match=error):
        appmodel.detection_rules(detection)


def test_new_exe_app_body():
    form = appmodel.propose_form(exe_analysis(), None)
    form.update(
        uninstall_command="u /S",
        detection={
            "type": "registry",
            "key": "HKLM\\Software\\App",
            "value_name": "Version",
            "operator": "version_ge",
            "value": "2.0",
        },
    )
    body = appmodel.new_app_body(form, exe_analysis())
    assert body["applicableArchitectures"] == "x86,x64"
    assert body["description"] == "App"
    assert body["installExperience"]["runAsAccount"] == "system"
    assert body["rules"][0]["operationType"] == "version"


def test_merge_assignments_never_duplicates_a_target():
    group = {
        "intent": "required",
        "id": "1",
        "target": {"@odata.type": "#microsoft.graph.groupAssignmentTarget", "groupId": "G"},
    }
    extra = appmodel.stage_assignments(
        {"intent": "available", "targets": [{"type": "group", "id": "g"}, {"type": "allUsers"}]}
    )
    merged = appmodel.merge_assignments([group], extra)
    assert [item["intent"] for item in merged] == ["required", "required"][:1] + ["available"]
    assert all("id" not in item for item in merged)


def test_store_validates_and_persists(tmp_path):
    store = RolloutStore(tmp_path)
    group = "11111111-1111-1111-1111-111111111111"
    store.set(
        "a",
        "App",
        {
            "intent": "available",
            "targets": [
                {"type": "group", "id": group.upper(), "name": "Pilot"},
                {"type": "group", "id": group},
            ],
        },
        None,
    )
    reloaded = RolloutStore(tmp_path).get("a")
    assert reloaded["pilot"] == {
        "intent": "available",
        "targets": [{"type": "group", "id": group, "name": "Pilot"}],
    }
    with pytest.raises(StoreError):
        store.set("a", "App", {"targets": [{"type": "group", "id": "not-a-guid"}]}, None)
    assert RolloutStore(tmp_path).get("unknown")["prod"]["targets"] == []
