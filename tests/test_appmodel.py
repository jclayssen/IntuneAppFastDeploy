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


@pytest.mark.parametrize(
    ("name", "old", "new", "expected"),
    [
        ("7-Zip 23.01 (x64 edition)", "23.01.00.0", "24.08.00.0", "7-Zip 24.08 (x64 edition)"),
        ("App 1.0", "1.0", "2.0", "App 2.0"),
        ("App", "1.0", "2.0", "App"),
        ("App 11.0.1", "1.0", "2.0", "App 11.0.1"),
        ("Tool 3.2.1", "3.2.1.0", "3.3.0.0", "Tool 3.3.0"),
        ("App 1.0", "", "2.0", "App 1.0"),
    ],
)
def test_rename_for_version(name, old, new, expected):
    assert appmodel.rename_for_version(name, old, new) == expected


def test_new_assignments_use_win32_settings_and_available_ones_follow_supersedence():
    required = appmodel.assignment_for({"type": "allDevices"}, "required")
    assert required["settings"]["@odata.type"] == "#microsoft.graph.win32LobAppAssignmentSettings"
    assert "autoUpdateSettings" not in required["settings"]
    available = appmodel.assignment_for({"type": "group", "id": "g"}, "available")
    assert available["target"]["groupId"] == "g"
    assert available["settings"]["autoUpdateSettings"]["autoUpdateSupersededAppsState"] == "enabled"


def test_clone_drops_read_only_and_script_references():
    existing = {
        "id": "a",
        "displayName": "App",
        "displayVersion": "1.0",
        "size": 5,
        "committedContentVersion": "3",
        "isAssigned": True,
        "publishingState": "published",
        "activeInstallScript": {"targetId": "s"},
        "detectionRules": [],
        "notes": "n",
        "setupFilePath": "setup-1.0.exe",
        "installCommandLine": '"setup-1.0.exe" /S',
        "uninstallCommandLine": "u",
        "rules": [file_rule()],
    }
    form = appmodel.propose_form(exe_analysis(), existing)
    body = appmodel.clone_app_body(existing, form, exe_analysis())
    assert not set(body) & appmodel.READ_ONLY
    assert body["notes"] == "n"


def test_unwritable_config_folder_is_reported_not_crashed(tmp_path, monkeypatch):
    blocker = tmp_path / "config"
    blocker.write_text("a file where the folder should be")
    assert RolloutStore(blocker).writable() is False

    store = RolloutStore(tmp_path / "ok")
    assert store.writable() is True

    def denied(*_):
        raise PermissionError("read-only")

    monkeypatch.setattr("iafd.store.os.replace", denied)
    with pytest.raises(StoreError, match="error.config_not_writable"):
        store.set("a", "App", None, None)


# --------------------------------------------------------------------------- install context


def updatable_app(**extra):
    return {
        "id": "a",
        "displayName": "App 1.0",
        "displayVersion": "1.0",
        "publisher": "Pub",
        "description": "Desc",
        "setupFilePath": "setup-1.0.exe",
        "installCommandLine": '"setup-1.0.exe" /S',
        "uninstallCommandLine": '"C:\\Program Files\\App\\uninstall.exe" /S',
        "rules": [file_rule()],
        **extra,
    }


def new_exe_form(**extra):
    form = appmodel.propose_form(exe_analysis(), None)
    form.update(
        publisher="Pub",
        uninstall_command="uninstall.exe /S",
        detection={
            "type": "file",
            "path": "C:\\App",
            "name": "app.exe",
            "operator": "version_ge",
            "value": "2.0",
        },
    )
    form.update(extra)
    return form


@pytest.mark.parametrize("run_as", ["system", "user"])
def test_new_app_sends_the_install_context(run_as):
    body = appmodel.new_app_body(new_exe_form(run_as=run_as), exe_analysis())
    assert body["installExperience"]["runAsAccount"] == run_as


def test_new_app_installs_as_system_when_nothing_is_chosen():
    form = new_exe_form()
    del form["run_as"]
    body = appmodel.new_app_body(form, exe_analysis())
    assert body["installExperience"] == {
        "runAsAccount": "system",
        "deviceRestartBehavior": "basedOnReturnCode",
    }


def test_unknown_install_context_is_refused():
    with pytest.raises(appmodel.FormError, match="error.bad_run_as"):
        appmodel.new_app_body(new_exe_form(run_as="administrator"), exe_analysis())
    existing = updatable_app()
    form = appmodel.propose_form(exe_analysis(), existing)
    form["run_as"] = "root"
    with pytest.raises(appmodel.FormError, match="error.bad_run_as"):
        appmodel.clone_app_body(existing, form, exe_analysis())
    with pytest.raises(appmodel.FormError, match="error.bad_run_as"):
        appmodel.inplace_patch(existing, form, exe_analysis(), "1")


def test_update_form_shows_the_install_context_of_the_current_app():
    user_app = updatable_app(
        installExperience={"runAsAccount": "user", "deviceRestartBehavior": "suppress"}
    )
    assert appmodel.propose_form(exe_analysis(), user_app)["run_as"] == "user"
    # An app whose install experience Graph does not return installs as system.
    assert appmodel.propose_form(exe_analysis(), updatable_app())["run_as"] == "system"


def test_update_keeps_the_install_context_unless_it_is_changed():
    experience = {
        "runAsAccount": "user",
        "deviceRestartBehavior": "suppress",
        "maxRunTimeInMinutes": 90,
    }
    existing = updatable_app(installExperience=experience)
    form = appmodel.propose_form(exe_analysis(), existing)

    # Untouched: the copy carries the same object, the in-place update does not send it.
    clone = appmodel.clone_app_body(existing, form, exe_analysis())
    assert clone["installExperience"] == experience
    assert "installExperience" not in appmodel.inplace_patch(existing, form, exe_analysis(), "1")
    # A form that says nothing about it changes nothing either.
    silent = {key: value for key, value in form.items() if key != "run_as"}
    clone = appmodel.clone_app_body(existing, silent, exe_analysis())
    assert clone["installExperience"] == experience
    assert "installExperience" not in appmodel.inplace_patch(existing, silent, exe_analysis(), "1")

    # Changed: only the account moves, the rest of the object is kept.
    form["run_as"] = "system"
    wanted = {**experience, "runAsAccount": "system"}
    assert appmodel.clone_app_body(existing, form, exe_analysis())["installExperience"] == wanted
    patch = appmodel.inplace_patch(existing, form, exe_analysis(), "1")
    assert patch["installExperience"] == wanted
    assert existing["installExperience"] == experience  # the app read from Intune is not modified


def test_install_context_of_an_app():
    assert appmodel.run_as_of(updatable_app()) == "system"
    assert appmodel.run_as_of(updatable_app(installExperience={"runAsAccount": "user"})) == "user"


# --------------------------------------------------------------------------- notifications


def test_stage_assignments_carry_the_notification_setting():
    stage = {"intent": "required", "targets": [{"type": "allDevices"}]}
    assert appmodel.stage_assignments(stage)[0]["settings"]["notifications"] == "showAll"
    for value in ("showAll", "showReboot", "hideAll"):
        settings = appmodel.stage_assignments(stage, value)[0]["settings"]
        assert settings["notifications"] == value
        assert settings["@odata.type"] == "#microsoft.graph.win32LobAppAssignmentSettings"


def test_assignment_description_tells_the_notification_setting():
    built = appmodel.assignment_for({"type": "allUsers"}, "required", "hideAll")
    assert appmodel.describe_assignment(built)["notifications"] == "hideAll"
    # Assignments made elsewhere may have no settings at all.
    bare = {"intent": "required", "target": built["target"], "settings": None}
    assert appmodel.describe_assignment(bare)["notifications"] is None


def test_store_keeps_the_notification_setting(tmp_path):
    store = RolloutStore(tmp_path)
    assert store.get("app-1")["notifications"] == "showAll"
    store.set("app-1", "App", None, None, "showReboot")
    assert store.get("app-1")["notifications"] == "showReboot"
    # Saving the groups without saying anything about notifications keeps the setting.
    store.set("app-1", "App", None, None)
    assert store.get("app-1")["notifications"] == "showReboot"
    # It follows the application to its next version, like the groups.
    store.move("app-1", "app-2", "App 2")
    assert store.get("app-2")["notifications"] == "showReboot"
    assert store.get("app-1")["notifications"] == "showAll"
    with pytest.raises(StoreError, match="error.bad_notifications"):
        store.set("app-2", "App", None, None, "silent")
    assert store.get("app-2")["notifications"] == "showReboot"
