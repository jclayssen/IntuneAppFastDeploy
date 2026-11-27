import os

import pytest

from iafd import appmodel, intunewin, publish
from iafd.graph import Graph, GraphError
from iafd.store import RolloutStore, StoreError

from .fake_intune import BASE, FakeIntune

GROUP_PILOT = "11111111-1111-1111-1111-111111111111"
GROUP_PROD = "22222222-2222-2222-2222-222222222222"
OLD_CODE = "{AAAAAAAA-0000-0000-0000-000000000001}"
NEW_CODE = "{AAAAAAAA-0000-0000-0000-000000000002}"


@pytest.fixture
def intune():
    return FakeIntune()


@pytest.fixture
def graph(intune):
    client = Graph(lambda: "test-token", BASE, transport=intune.transport(), sleep=lambda _: None)
    yield client
    client.close()


@pytest.fixture
def store(tmp_path):
    return RolloutStore(tmp_path / "config")


@pytest.fixture
def package(tmp_path):
    setup = tmp_path / "app-2.0.msi"
    setup.write_bytes(os.urandom(50_000))
    return intunewin.build_package(setup, tmp_path / "pkg")


def msi_analysis(version="2.0", code=NEW_CODE):
    return {
        "kind": "msi",
        "file_name": "app-2.0.msi",
        "version": version,
        "product_name": "App",
        "publisher": "Pub",
        "architecture": "x64",
        "install_command": 'msiexec /i "app-2.0.msi" /qn /norestart',
        "uninstall_command": f'msiexec /x "{code}" /qn /norestart',
        "msi": {"product_code": code, "upgrade_code": "", "package_type": "perMachine"},
    }


def existing_app(intune, version="1.0", code=OLD_CODE, **extra):
    return intune.add_app(
        displayName=f"App {version}",
        displayVersion=version,
        setupFilePath=f"app-{version}.msi",
        fileName=f"app-{version}.intunewin",
        installCommandLine=f'msiexec /i "app-{version}.msi" /qn /norestart',
        uninstallCommandLine=f'msiexec /x "{code}" /qn /norestart',
        notes="written by a colleague",
        rules=[
            {
                "@odata.type": appmodel.RULE_PRODUCT,
                "ruleType": "detection",
                "productCode": code,
                "productVersionOperator": "notConfigured",
                "productVersion": None,
            },
            {
                "@odata.type": appmodel.RULE_REGISTRY,
                "ruleType": "requirement",
                "keyPath": "HKLM\\X",
                "valueName": None,
                "operationType": "exists",
                "operator": "notConfigured",
                "comparisonValue": None,
                "check32BitOn64System": False,
            },
        ],
        **extra,
    )


def steps():
    seen = []
    return seen, seen.append, lambda done, total: None


def test_new_app_is_created_uploaded_and_assigned_to_the_pilot(intune, graph, store, package):
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    form.update(
        rollout="pilot",
        pilot={"intent": "required", "targets": [{"type": "group", "id": GROUP_PILOT}]},
        prod={"intent": "required", "targets": [{"type": "allDevices"}]},
    )
    seen, step, progress = steps()
    outcome = publish.publish_new(graph, store, package, analysis, form, step, progress)
    app = intune.apps[outcome["app_id"]]
    assert app["committedContentVersion"] == "1"
    assert app["setupFilePath"] == "app-2.0.msi"
    assert app["fileName"] == "app-2.0.intunewin"
    assert app["msiInformation"]["productCode"] == NEW_CODE
    assert app["rules"][0]["productCode"] == NEW_CODE
    assert intune.committed_blob == package.encrypted_path.read_bytes()
    targets = [a["target"].get("groupId") for a in intune.assignments[app["id"]]]
    assert targets == [GROUP_PILOT]
    assert seen == ["step.create", "step.upload", "step.commit", "step.assign"]
    assert store.get(app["id"])["prod"]["targets"] == [{"type": "allDevices"}]


def test_failed_upload_leaves_no_half_created_app(intune, graph, store, package):
    intune.fail_commit = True
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    seen, step, progress = steps()
    with pytest.raises(GraphError):
        publish.publish_new(graph, store, package, analysis, form, step, progress)
    assert intune.apps == {}


def test_supersede_clones_the_app_and_goes_to_pilot_first(intune, graph, store, package):
    current = existing_app(intune)
    intune.assignments[current["id"]] = [
        {
            "id": "x",
            "intent": "required",
            "source": "direct",
            "settings": None,
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": GROUP_PROD,
            },
        }
    ]
    store.set(
        current["id"],
        "App",
        {"intent": "required", "targets": [{"type": "group", "id": GROUP_PILOT}]},
        None,
    )
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    assert form["display_name"] == "App 2.0"
    assert form["install_command"] == 'msiexec /i "app-2.0.msi" /qn /norestart'
    assert NEW_CODE in form["uninstall_command"] and OLD_CODE not in form["uninstall_command"]
    assert form["detection"] == {"type": "msi", "product_code": NEW_CODE}
    form.update(mode="supersede", rollout="pilot")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    new = intune.apps[outcome["app_id"]]
    assert new["id"] != current["id"] and current["id"] in intune.apps
    assert new["notes"] == "written by a colleague"
    assert new["displayVersion"] == "2.0"
    kinds = sorted(rule["ruleType"] for rule in new["rules"])
    assert kinds == ["detection", "requirement"]
    assert intune.relationships[new["id"]] == [
        {
            "@odata.type": appmodel.SUPERSEDENCE,
            "targetId": current["id"],
            "supersedenceType": "update",
        }
    ]
    assert [a["target"]["groupId"] for a in intune.assignments[new["id"]]] == [GROUP_PILOT]
    assert outcome["removed"] == []
    # The pilot/production groups follow the application to its new version.
    assert store.get(new["id"])["pilot"]["targets"][0]["id"] == GROUP_PILOT
    assert store.get(current["id"])["pilot"]["targets"] == []

    # "Go to production": without configured groups, reuse the previous version's.
    publish.promote(graph, store, new["id"])
    groups = sorted(a["target"]["groupId"] for a in intune.assignments[new["id"]])
    assert groups == [GROUP_PILOT, GROUP_PROD]


def test_third_version_removes_the_oldest_and_keeps_two(intune, graph, store, package):
    oldest = existing_app(intune, "0.9", "{AAAAAAAA-0000-0000-0000-000000000000}")
    dependency = intune.add_app(displayName="Runtime")
    current = existing_app(intune)
    intune.relationships[current["id"]] = [
        {
            "@odata.type": appmodel.SUPERSEDENCE,
            "targetId": oldest["id"],
            "supersedenceType": "update",
        },
        {
            "@odata.type": "#microsoft.graph.mobileAppDependency",
            "targetId": dependency["id"],
            "dependencyType": "autoInstall",
        },
    ]
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="prod", cleanup_old=True)
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    assert oldest["id"] not in intune.apps
    assert [item["id"] for item in outcome["removed"]] == [oldest["id"]]
    assert set(intune.apps) == {dependency["id"], current["id"], outcome["app_id"]}
    # The current version keeps its dependency and loses only the old supersedence.
    assert intune.relationships[current["id"]] == [
        {
            "@odata.type": "#microsoft.graph.mobileAppDependency",
            "targetId": dependency["id"],
            "dependencyType": "autoInstall",
        }
    ]
    new_links = {item["@odata.type"] for item in intune.relationships[outcome["app_id"]]}
    assert new_links == {appmodel.SUPERSEDENCE, "#microsoft.graph.mobileAppDependency"}
    assert "step.cleanup" in seen


def test_cleanup_can_be_declined(intune, graph, store, package):
    oldest = existing_app(intune, "0.9", "{AAAAAAAA-0000-0000-0000-000000000000}")
    current = existing_app(intune)
    intune.relationships[current["id"]] = [
        {
            "@odata.type": appmodel.SUPERSEDENCE,
            "targetId": oldest["id"],
            "supersedenceType": "update",
        }
    ]
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="none", cleanup_old=False)
    seen, step, progress = steps()
    publish.publish_supersede(graph, store, package, analysis, form, current, step, progress)
    assert oldest["id"] in intune.apps


def test_inplace_update_keeps_the_same_app_and_assignments(intune, graph, package):
    current = existing_app(intune)
    intune.assignments[current["id"]] = [
        {
            "id": "x",
            "intent": "required",
            "target": {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"},
        }
    ]
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    seen, step, progress = steps()
    outcome = publish.publish_inplace(graph, package, analysis, form, current, step, progress)
    assert outcome["app_id"] == current["id"] and len(intune.apps) == 1
    app = intune.apps[current["id"]]
    assert app["committedContentVersion"] == "1"
    assert app["displayVersion"] == "2.0" and app["setupFilePath"] == "app-2.0.msi"
    assert len(intune.assignments[current["id"]]) == 1
    assert ("POST", f"/{current['id']}/assign") not in intune.calls


def test_incomplete_form_is_refused_before_anything_is_sent(intune, graph, store, package):
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    form["uninstall_command"] = ""
    seen, step, progress = steps()
    with pytest.raises(appmodel.FormError):
        publish.publish_new(graph, store, package, analysis, form, step, progress)
    assert intune.calls == []


# --------------------------------------------------------------------------- install context


def user_context():
    return {"runAsAccount": "user", "deviceRestartBehavior": "suppress", "maxRunTimeInMinutes": 90}


def test_new_app_is_created_in_the_chosen_install_context(intune, graph, store, package):
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    form["run_as"] = "user"
    seen, step, progress = steps()
    outcome = publish.publish_new(graph, store, package, analysis, form, step, progress)
    assert intune.apps[outcome["app_id"]]["installExperience"]["runAsAccount"] == "user"


def test_supersede_keeps_the_install_context_of_the_current_app(intune, graph, store, package):
    current = existing_app(intune, installExperience=user_context())
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    assert form["run_as"] == "user"
    form.update(mode="supersede", rollout="none")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    assert intune.apps[outcome["app_id"]]["installExperience"] == user_context()


def test_supersede_can_change_the_install_context(intune, graph, store, package):
    current = existing_app(intune, installExperience=user_context())
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="none", run_as="system")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    assert intune.apps[outcome["app_id"]]["installExperience"] == {
        **user_context(),
        "runAsAccount": "system",
    }
    assert intune.apps[current["id"]]["installExperience"] == user_context()


def test_inplace_update_leaves_the_install_context_alone(intune, graph, package):
    current = existing_app(intune, installExperience=user_context())
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    seen, step, progress = steps()
    publish.publish_inplace(graph, package, analysis, form, current, step, progress)
    assert intune.patches and all("installExperience" not in patch for patch in intune.patches)
    assert intune.apps[current["id"]]["installExperience"] == user_context()


def test_inplace_update_can_change_the_install_context(intune, graph, package):
    current = existing_app(intune, installExperience=user_context())
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form["run_as"] = "system"
    seen, step, progress = steps()
    publish.publish_inplace(graph, package, analysis, form, current, step, progress)
    assert intune.apps[current["id"]]["installExperience"] == {
        **user_context(),
        "runAsAccount": "system",
    }


# --------------------------------------------------------------------------- notifications


def notifications_of(intune, app_id):
    return {
        appmodel.target_key(item["target"]): (item.get("settings") or {}).get("notifications")
        for item in intune.assignments[app_id]
    }


def pilot_and_prod():
    return {
        "pilot": {"intent": "required", "targets": [{"type": "group", "id": GROUP_PILOT}]},
        "prod": {"intent": "required", "targets": [{"type": "group", "id": GROUP_PROD}]},
    }


def test_notifications_are_shown_by_default(intune, graph, store, package):
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    form.update(rollout="prod", **pilot_and_prod())
    seen, step, progress = steps()
    outcome = publish.publish_new(graph, store, package, analysis, form, step, progress)
    assert notifications_of(intune, outcome["app_id"]) == {
        GROUP_PILOT: "showAll",
        GROUP_PROD: "showAll",
    }
    assert store.get(outcome["app_id"])["notifications"] == "showAll"


def test_new_app_assignments_use_the_chosen_notifications(intune, graph, store, package):
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    form.update(rollout="pilot", notifications="hideAll", **pilot_and_prod())
    seen, step, progress = steps()
    outcome = publish.publish_new(graph, store, package, analysis, form, step, progress)
    app_id = outcome["app_id"]
    assert notifications_of(intune, app_id) == {GROUP_PILOT: "hideAll"}
    assert store.get(app_id)["notifications"] == "hideAll"

    # Going to production adds the production groups with the same setting.
    publish.promote(graph, store, app_id)
    assert notifications_of(intune, app_id) == {GROUP_PILOT: "hideAll", GROUP_PROD: "hideAll"}


def test_notifications_follow_the_app_to_its_next_version(intune, graph, store, package):
    current = existing_app(intune)
    store.set(current["id"], "App", **pilot_and_prod(), notifications="showReboot")
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="pilot")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    new_id = outcome["app_id"]
    assert notifications_of(intune, new_id) == {GROUP_PILOT: "showReboot"}
    assert store.get(new_id)["notifications"] == "showReboot"
    publish.promote(graph, store, new_id)
    assert notifications_of(intune, new_id) == {
        GROUP_PILOT: "showReboot",
        GROUP_PROD: "showReboot",
    }


def test_supersede_can_change_the_notifications(intune, graph, store, package):
    current = existing_app(intune)
    store.set(current["id"], "App", **pilot_and_prod(), notifications="showReboot")
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="prod", notifications="hideAll")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    new_id = outcome["app_id"]
    assert notifications_of(intune, new_id) == {GROUP_PILOT: "hideAll", GROUP_PROD: "hideAll"}
    assert store.get(new_id)["notifications"] == "hideAll"


def test_supersede_remembers_notifications_for_an_app_without_groups(intune, graph, store, package):
    current = existing_app(intune)
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="none", notifications="hideAll")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    assert store.get(outcome["app_id"])["notifications"] == "hideAll"


def test_assignments_copied_from_the_previous_version_keep_their_own_settings(
    intune, graph, store, package
):
    # No production group configured: the assignments of the previous version are
    # reused as they are, whatever someone set on them in the Intune portal.
    current = existing_app(intune)
    copied = appmodel.assignment_for({"type": "group", "id": GROUP_PROD}, "required", "showReboot")
    intune.assignments[current["id"]] = [{"id": "x", **copied}]
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, current)
    form.update(mode="supersede", rollout="prod", notifications="hideAll")
    seen, step, progress = steps()
    outcome = publish.publish_supersede(
        graph, store, package, analysis, form, current, step, progress
    )
    assert notifications_of(intune, outcome["app_id"]) == {GROUP_PROD: "showReboot"}


@pytest.mark.parametrize("mode", ["new", "supersede"])
def test_unknown_notifications_are_refused_before_anything_is_sent(
    intune, graph, store, package, mode
):
    analysis = msi_analysis()
    current = existing_app(intune) if mode == "supersede" else None
    form = appmodel.propose_form(analysis, current)
    form.update(rollout="none", notifications="quiet")
    seen, step, progress = steps()
    with pytest.raises(StoreError, match="error.bad_notifications"):
        if current:
            publish.publish_supersede(
                graph, store, package, analysis, form, current, step, progress
            )
        else:
            publish.publish_new(graph, store, package, analysis, form, step, progress)
    assert intune.calls == []


def test_badly_formed_group_is_refused_before_the_app_is_created(intune, graph, store, package):
    analysis = msi_analysis()
    form = appmodel.propose_form(analysis, None)
    form.update(rollout="pilot", pilot={"targets": [{"type": "group", "id": "nope"}]})
    seen, step, progress = steps()
    with pytest.raises(StoreError, match="error.bad_group_id"):
        publish.publish_new(graph, store, package, analysis, form, step, progress)
    assert intune.calls == []
