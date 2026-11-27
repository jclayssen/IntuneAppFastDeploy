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


# --------------------------------------------------------------------------- advanced settings

ADVANCED_DEFAULTS = {
    "disk_space_mb": None,
    "memory_mb": None,
    "processors": None,
    "cpu_speed_mhz": None,
    "windows_release": "1607",
    "restart_behavior": "basedOnReturnCode",
    "max_run_time": None,
    "allow_uninstall": False,
}
ADVANCED_PROPERTIES = (
    "minimumFreeDiskSpaceInMB",
    "minimumMemoryInMB",
    "minimumNumberOfProcessors",
    "minimumCpuSpeedInMHz",
    "allowAvailableUninstall",
)


def tuned_app(**extra):
    """An existing app on which someone set every advanced setting."""
    return updatable_app(
        minimumFreeDiskSpaceInMB=500,
        minimumMemoryInMB=4096,
        minimumNumberOfProcessors=2,
        minimumCpuSpeedInMHz=1800,
        minimumSupportedWindowsRelease="21H1",
        allowAvailableUninstall=True,
        installExperience={
            "runAsAccount": "user",
            "deviceRestartBehavior": "suppress",
            "maxRunTimeInMinutes": 90,
        },
        **extra,
    )


def test_new_app_form_proposes_the_defaults_of_the_tool():
    assert appmodel.propose_form(exe_analysis(), None)["advanced"] == ADVANCED_DEFAULTS


def test_new_app_is_unchanged_when_the_advanced_section_is_left_alone():
    form = new_exe_form()
    assert form["advanced"] == ADVANCED_DEFAULTS
    untouched = appmodel.new_app_body(form, exe_analysis())
    without = appmodel.new_app_body(
        {key: value for key, value in form.items() if key != "advanced"}, exe_analysis()
    )
    assert untouched == without
    assert untouched["minimumSupportedWindowsRelease"] == "1607"
    assert untouched["installExperience"] == {
        "runAsAccount": "system",
        "deviceRestartBehavior": "basedOnReturnCode",
    }
    for prop in ADVANCED_PROPERTIES:
        assert prop not in untouched, prop


def test_new_app_carries_the_advanced_settings():
    form = new_exe_form(
        advanced={
            "disk_space_mb": "500",  # as typed in a text field
            "memory_mb": 4096,
            "processors": 2,
            "cpu_speed_mhz": " 1800 ",
            "windows_release": "Windows11_23H2",
            "restart_behavior": "suppress",
            "max_run_time": 120,
            "allow_uninstall": True,
        }
    )
    body = appmodel.new_app_body(form, exe_analysis())
    assert body["minimumFreeDiskSpaceInMB"] == 500
    assert body["minimumMemoryInMB"] == 4096
    assert body["minimumNumberOfProcessors"] == 2
    assert body["minimumCpuSpeedInMHz"] == 1800
    assert body["minimumSupportedWindowsRelease"] == "Windows11_23H2"
    assert body["allowAvailableUninstall"] is True
    assert body["installExperience"] == {
        "runAsAccount": "system",
        "deviceRestartBehavior": "suppress",
        "maxRunTimeInMinutes": 120,
    }


def test_only_the_filled_advanced_fields_are_sent():
    form = new_exe_form(advanced={"memory_mb": "2048", "disk_space_mb": ""})
    body = appmodel.new_app_body(form, exe_analysis())
    assert body["minimumMemoryInMB"] == 2048
    assert "minimumFreeDiskSpaceInMB" not in body
    assert body["minimumSupportedWindowsRelease"] == "1607"


@pytest.mark.parametrize(
    ("advanced", "error", "detail"),
    [
        ({"disk_space_mb": -5}, "error.advanced_number", "minimumFreeDiskSpaceInMB"),
        ({"disk_space_mb": 0}, "error.advanced_number", "minimumFreeDiskSpaceInMB"),
        ({"memory_mb": "many"}, "error.advanced_number", "minimumMemoryInMB"),
        ({"memory_mb": "-1"}, "error.advanced_number", "minimumMemoryInMB"),
        ({"processors": 1.5}, "error.advanced_number", "minimumNumberOfProcessors"),
        ({"processors": True}, "error.advanced_number", "minimumNumberOfProcessors"),
        ({"cpu_speed_mhz": [1800]}, "error.advanced_number", "minimumCpuSpeedInMHz"),
        ({"cpu_speed_mhz": 2**31}, "error.advanced_number", "minimumCpuSpeedInMHz"),
        ({"max_run_time": 0}, "error.advanced_number", "maxRunTimeInMinutes"),
        ({"max_run_time": 1441}, "error.advanced_number", "maxRunTimeInMinutes"),
        ({"restart_behavior": "never"}, "error.bad_restart_behavior", ""),
        ({"restart_behavior": ["force"]}, "error.bad_restart_behavior", ""),
        ({"windows_release": "Windows12"}, "error.bad_windows_release", ""),
        ({"windows_release": 1607}, "error.bad_windows_release", ""),
        ({"allow_uninstall": "yes"}, "error.bad_advanced", ""),
        ("everything", "error.bad_advanced", ""),
    ],
)
def test_invalid_advanced_settings_are_refused_on_the_server(advanced, error, detail):
    existing = tuned_app()
    update = {**appmodel.propose_form(exe_analysis(), existing), "advanced": advanced}
    attempts = (
        lambda: appmodel.new_app_body(new_exe_form(advanced=advanced), exe_analysis()),
        lambda: appmodel.clone_app_body(existing, update, exe_analysis()),
        lambda: appmodel.inplace_patch(existing, update, exe_analysis(), "1"),
    )
    for attempt in attempts:
        with pytest.raises(appmodel.FormError, match=error) as caught:
            attempt()
        assert caught.value.detail == detail


def test_update_form_is_filled_with_the_settings_read_from_intune():
    assert appmodel.propose_form(exe_analysis(), tuned_app())["advanced"] == {
        "disk_space_mb": 500,
        "memory_mb": 4096,
        "processors": 2,
        "cpu_speed_mhz": 1800,
        "windows_release": "21H1",
        "restart_behavior": "suppress",
        "max_run_time": 90,
        "allow_uninstall": True,
    }
    # An app that says nothing: empty fields, and no Windows release is proposed for it.
    assert appmodel.propose_form(exe_analysis(), updatable_app())["advanced"] == {
        **ADVANCED_DEFAULTS,
        "windows_release": "",
    }


def test_update_left_alone_keeps_every_advanced_setting():
    existing = tuned_app()
    form = appmodel.propose_form(exe_analysis(), existing)
    silent = {key: value for key, value in form.items() if key != "advanced"}

    clone = appmodel.clone_app_body(existing, form, exe_analysis())
    assert clone == appmodel.clone_app_body(existing, silent, exe_analysis())
    assert clone["minimumFreeDiskSpaceInMB"] == 500 and clone["minimumMemoryInMB"] == 4096
    assert clone["minimumSupportedWindowsRelease"] == "21H1"
    assert clone["allowAvailableUninstall"] is True
    assert clone["installExperience"] == existing["installExperience"]

    # The in-place update sends none of these settings: Intune keeps what it has.
    patch = appmodel.inplace_patch(existing, form, exe_analysis(), "1")
    assert patch == appmodel.inplace_patch(existing, silent, exe_analysis(), "1")
    assert "installExperience" not in patch
    assert "minimumSupportedWindowsRelease" not in patch
    for prop in ADVANCED_PROPERTIES:
        assert prop not in patch, prop


def test_update_changes_only_the_advanced_settings_that_were_modified():
    existing = tuned_app()
    form = appmodel.propose_form(exe_analysis(), existing)
    form["advanced"].update(memory_mb="8192", restart_behavior="force", disk_space_mb="")

    patch = appmodel.inplace_patch(existing, form, exe_analysis(), "1")
    assert patch["minimumMemoryInMB"] == 8192
    assert patch["minimumFreeDiskSpaceInMB"] is None  # emptied: the requirement is removed
    assert patch["installExperience"] == {
        "runAsAccount": "user",
        "deviceRestartBehavior": "force",
        "maxRunTimeInMinutes": 90,
    }
    for prop in ("minimumNumberOfProcessors", "minimumCpuSpeedInMHz", "allowAvailableUninstall"):
        assert prop not in patch, prop
    assert "minimumSupportedWindowsRelease" not in patch

    clone = appmodel.clone_app_body(existing, form, exe_analysis())
    assert clone["minimumMemoryInMB"] == 8192 and clone["minimumFreeDiskSpaceInMB"] is None
    assert clone["minimumNumberOfProcessors"] == 2 and clone["minimumCpuSpeedInMHz"] == 1800
    assert clone["installExperience"]["deviceRestartBehavior"] == "force"
    assert clone["installExperience"]["runAsAccount"] == "user"
    assert existing["minimumMemoryInMB"] == 4096  # the app read from Intune is not modified
    assert existing["installExperience"]["deviceRestartBehavior"] == "suppress"


def test_update_can_switch_the_other_advanced_settings():
    existing = tuned_app()
    form = appmodel.propose_form(exe_analysis(), existing)
    form["advanced"].update(
        windows_release="Windows11_22H2", allow_uninstall=False, max_run_time="30"
    )
    patch = appmodel.inplace_patch(existing, form, exe_analysis(), "1")
    assert patch["minimumSupportedWindowsRelease"] == "Windows11_22H2"
    assert patch["allowAvailableUninstall"] is False
    assert patch["installExperience"]["maxRunTimeInMinutes"] == 30


def test_emptied_install_time_goes_back_to_the_default_of_intune():
    existing = tuned_app()
    form = appmodel.propose_form(exe_analysis(), existing)
    form["advanced"]["max_run_time"] = ""
    patch = appmodel.inplace_patch(existing, form, exe_analysis(), "1")
    assert patch["installExperience"]["maxRunTimeInMinutes"] == 60
    # Nothing to send when the app is already at the default, or never said anything.
    for experience in ({"runAsAccount": "system", "maxRunTimeInMinutes": 60}, None):
        usual = updatable_app(installExperience=experience)
        form = appmodel.propose_form(exe_analysis(), usual)
        form["advanced"]["max_run_time"] = ""
        assert "installExperience" not in appmodel.inplace_patch(usual, form, exe_analysis(), "1")


def test_a_windows_release_the_tool_does_not_know_is_kept_but_cannot_be_chosen():
    existing = {**tuned_app(), "minimumSupportedWindowsRelease": "Windows11_99H9"}
    form = appmodel.propose_form(exe_analysis(), existing)
    assert form["advanced"]["windows_release"] == "Windows11_99H9"
    clone = appmodel.clone_app_body(existing, form, exe_analysis())
    assert clone["minimumSupportedWindowsRelease"] == "Windows11_99H9"
    assert "minimumSupportedWindowsRelease" not in appmodel.inplace_patch(
        existing, form, exe_analysis(), "1"
    )
    form["advanced"]["windows_release"] = "Windows11_98H8"
    with pytest.raises(appmodel.FormError, match="error.bad_windows_release"):
        appmodel.clone_app_body(existing, form, exe_analysis())
