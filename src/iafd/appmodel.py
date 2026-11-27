"""Pure functions that build the Microsoft Graph payloads for Win32 apps.

Nothing here talks to the network, so every rule is unit tested.
"""

from __future__ import annotations

import copy
import re

from . import removal

WIN32 = "#microsoft.graph.win32LobApp"
ASSIGNMENT = "#microsoft.graph.mobileAppAssignment"
SUPERSEDENCE = "#microsoft.graph.mobileAppSupersedence"
RULE_PRODUCT = "#microsoft.graph.win32LobAppProductCodeRule"
RULE_FILE = "#microsoft.graph.win32LobAppFileSystemRule"
RULE_REGISTRY = "#microsoft.graph.win32LobAppRegistryRule"

# Properties Graph returns but refuses (or ignores) when creating an app.
READ_ONLY = {
    "@odata.context",
    "id",
    "createdDateTime",
    "lastModifiedDateTime",
    "uploadState",
    "publishingState",
    "isAssigned",
    "dependentAppCount",
    "supersedingAppCount",
    "supersededAppCount",
    "committedContentVersion",
    "size",
    "detectionRules",
    "requirementRules",
    # References to scripts stored on the original app: a copy cannot reuse them.
    "activeInstallScript",
    "activeUninstallScript",
}

DEFAULT_RETURN_CODES = [
    {"returnCode": 0, "type": "success"},
    {"returnCode": 1707, "type": "success"},
    {"returnCode": 3010, "type": "softReboot"},
    {"returnCode": 1641, "type": "hardReboot"},
    {"returnCode": 1618, "type": "retry"},
]

ARCHITECTURES = {"x64": "x64", "x86": "x86", "arm64": "arm64", "any": "x86,x64"}
RUN_AS = ("system", "user")  # runAsAccountType
DEFAULT_INSTALL_EXPERIENCE = {
    "runAsAccount": "system",
    "deviceRestartBehavior": "basedOnReturnCode",
}

_GUID = re.compile(r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}")
_LEGACY_TYPES = {
    "#microsoft.graph.win32LobAppProductCodeDetection": RULE_PRODUCT,
    "#microsoft.graph.win32LobAppFileSystemDetection": RULE_FILE,
    "#microsoft.graph.win32LobAppRegistryDetection": RULE_REGISTRY,
    "#microsoft.graph.win32LobAppFileSystemRequirement": RULE_FILE,
    "#microsoft.graph.win32LobAppRegistryRequirement": RULE_REGISTRY,
    "#microsoft.graph.win32LobAppPowerShellScriptDetection": (
        "#microsoft.graph.win32LobAppPowerShellScriptRule"
    ),
    "#microsoft.graph.win32LobAppPowerShellScriptRequirement": (
        "#microsoft.graph.win32LobAppPowerShellScriptRule"
    ),
}


class FormError(ValueError):
    """Carries a translation key for the UI."""


# --------------------------------------------------------------------------- reading


def app_version(app: dict) -> str:
    return app.get("displayVersion") or (app.get("msiInformation") or {}).get("productVersion", "")


def run_as_of(app: dict) -> str:
    """The account an app installs under; Intune's own default is the system."""
    return (app.get("installExperience") or {}).get("runAsAccount") or "system"


def summarize(app: dict) -> dict:
    return {
        "id": app.get("id"),
        "name": app.get("displayName", ""),
        "version": app_version(app),
        "publisher": app.get("publisher", ""),
        "assigned": bool(app.get("isAssigned")),
        "file": app.get("setupFilePath") or app.get("fileName") or "",
        "modified": app.get("lastModifiedDateTime", ""),
        "superseded_count": app.get("supersededAppCount", 0),
        "superseding_count": app.get("supersedingAppCount", 0),
    }


def _legacy_to_rule(rule: dict, rule_type: str) -> dict:
    converted = dict(rule)
    converted["@odata.type"] = _LEGACY_TYPES.get(
        rule.get("@odata.type", ""), rule.get("@odata.type")
    )
    converted["ruleType"] = rule_type
    if "detectionType" in converted:
        converted["operationType"] = converted.pop("detectionType")
    if "detectionValue" in converted:
        converted["comparisonValue"] = converted.pop("detectionValue")
    return converted


def app_rules(app: dict) -> list[dict]:
    """All rules of an app in the current ``rules`` format."""
    rules = app.get("rules") or []
    if rules:
        return copy.deepcopy(rules)
    converted = [_legacy_to_rule(rule, "detection") for rule in app.get("detectionRules") or []]
    converted += [
        _legacy_to_rule(rule, "requirement") for rule in app.get("requirementRules") or []
    ]
    return converted


def detection_model(rules: list[dict]) -> dict:
    """Describe the detection rules in the simple shape the form edits."""
    detection = [rule for rule in rules if rule.get("ruleType") == "detection"]
    if len(detection) != 1:
        return {"type": "keep", "count": len(detection)}
    rule = detection[0]
    kind = rule.get("@odata.type")
    operation, operator = rule.get("operationType"), rule.get("operator")
    if kind == RULE_PRODUCT:
        return {"type": "msi", "product_code": rule.get("productCode", "")}
    simple = None
    if operation == "exists":
        simple = "exists"
    elif operation == "version" and operator == "greaterThanOrEqual":
        simple = "version_ge"
    elif operation == "string" and operator == "equal" and kind == RULE_REGISTRY:
        simple = "string_eq"
    if simple is None:
        return {"type": "keep", "count": 1}
    common = {
        "operator": simple,
        "value": rule.get("comparisonValue") or "",
        "check32": bool(rule.get("check32BitOn64System")),
    }
    if kind == RULE_FILE:
        return {
            "type": "file",
            "path": rule.get("path", ""),
            "name": rule.get("fileOrFolderName", ""),
            **common,
        }
    if kind == RULE_REGISTRY:
        return {
            "type": "registry",
            "key": rule.get("keyPath", ""),
            "value_name": rule.get("valueName") or "",
            **common,
        }
    return {"type": "keep", "count": 1}


# --------------------------------------------------------------------------- writing


def detection_rules(detection: dict) -> list[dict]:
    """Build Graph rules from the form's detection model (not for ``keep``)."""
    kind = detection.get("type")
    if kind == "msi":
        code = (detection.get("product_code") or "").strip()
        if not _GUID.fullmatch(code):
            raise FormError("error.bad_product_code")
        return [
            {
                "@odata.type": RULE_PRODUCT,
                "ruleType": "detection",
                "productCode": code,
                "productVersionOperator": "notConfigured",
                "productVersion": None,
            }
        ]
    operator = detection.get("operator", "exists")
    value = (detection.get("value") or "").strip()
    mapping = {
        "exists": ("exists", "notConfigured", None),
        "version_ge": ("version", "greaterThanOrEqual", value),
        "string_eq": ("string", "equal", value),
    }
    if operator not in mapping or (operator == "string_eq" and kind != "registry"):
        raise FormError("error.bad_detection")
    operation, graph_operator, comparison = mapping[operator]
    if operator != "exists" and not value:
        raise FormError("error.detection_value")
    base = {
        "ruleType": "detection",
        "check32BitOn64System": bool(detection.get("check32")),
        "operationType": operation,
        "operator": graph_operator,
        "comparisonValue": comparison,
    }
    if kind == "file":
        path, name = (detection.get("path") or "").strip(), (detection.get("name") or "").strip()
        if not path or not name:
            raise FormError("error.detection_file")
        return [{"@odata.type": RULE_FILE, "path": path, "fileOrFolderName": name, **base}]
    if kind == "registry":
        key = (detection.get("key") or "").strip()
        if not key:
            raise FormError("error.detection_registry")
        value_name = (detection.get("value_name") or "").strip() or None
        return [{"@odata.type": RULE_REGISTRY, "keyPath": key, "valueName": value_name, **base}]
    raise FormError("error.bad_detection")


def bump_rules(
    rules: list[dict], old_version: str, new_version: str, new_product_code: str = ""
) -> tuple[list[dict], int]:
    """Keep existing detection rules but move them to the new version.

    Version comparisons equal to the old version and MSI product codes are
    updated. Returns the rules and how many were changed.
    """
    changed = 0
    result = copy.deepcopy(rules)
    for rule in result:
        if rule.get("ruleType") != "detection":
            continue
        if rule.get("@odata.type") == RULE_PRODUCT and new_product_code:
            if rule.get("productCode") != new_product_code:
                rule["productCode"] = new_product_code
                changed += 1
            if rule.get("productVersion") and rule["productVersion"] == old_version:
                rule["productVersion"] = new_version
        elif (
            old_version
            and new_version
            and rule.get("operationType") in ("version", "string")
            and rule.get("comparisonValue") == old_version
        ):
            rule["comparisonValue"] = new_version
            changed += 1
    return result, changed


def _require(form: dict, key: str, error: str) -> str:
    value = str(form.get(key) or "").strip()
    if not value:
        raise FormError(error)
    return value


def _msi_information(form: dict, analysis: dict) -> dict | None:
    if analysis.get("kind") != "msi":
        return None
    msi = analysis.get("msi") or {}
    return {
        "productCode": msi.get("product_code", ""),
        "productVersion": analysis.get("version", ""),
        "upgradeCode": msi.get("upgrade_code") or None,
        "requiresReboot": False,
        "packageType": msi.get("package_type", "perMachine"),
        "productName": analysis.get("product_name") or form.get("display_name", ""),
        "publisher": analysis.get("publisher") or form.get("publisher", ""),
    }


def intunewin_name(setup_file: str) -> str:
    return setup_file.rsplit(".", 1)[0] + ".intunewin"


def _replace(form: dict) -> dict | None:
    return removal.clean_replace(form.get("replace"), str(form.get("version") or ""))


def _content_fields(form: dict, analysis: dict) -> dict:
    """Fields that change with every new installer."""
    replace = _replace(form)
    setup_file = analysis["file_name"]
    install = _require(form, "install_command", "error.install_required")
    if replace and replace["mode"] == "reinstall":
        # The real install command runs inside the wrapper, after the old version is removed.
        removal.render_replace_script(replace["config"], install)
        setup_file = removal.replace_script_name(replace["config"])
        install = removal.replace_install_command(replace["config"])
    fields = {
        "displayName": _require(form, "display_name", "error.name_required"),
        "displayVersion": str(form.get("version") or "").strip(),
        "fileName": intunewin_name(analysis["file_name"]),
        "setupFilePath": setup_file,
        "installCommandLine": install,
        "uninstallCommandLine": _require(form, "uninstall_command", "error.uninstall_required"),
        "msiInformation": _msi_information(form, analysis),
    }
    return fields


def _rules_for(form: dict, analysis: dict, existing: dict | None) -> list[dict]:
    detection = form.get("detection") or {}
    previous = app_rules(existing) if existing else []
    requirements = [rule for rule in previous if rule.get("ruleType") != "detection"]
    if detection.get("type") == "keep":
        if not existing:
            raise FormError("error.bad_detection")
        bumped, _ = bump_rules(
            previous,
            app_version(existing),
            str(form.get("version") or ""),
            (analysis.get("msi") or {}).get("product_code", ""),
        )
        return _with_replace(bumped, form)
    return _with_replace(requirements + detection_rules(detection), form)


def _with_replace(rules: list[dict], form: dict) -> list[dict]:
    """Add (or renew) the rule that limits the app to devices with an older version."""
    rules = [rule for rule in rules if not removal.is_tool_requirement(rule)]
    replace = _replace(form)
    if replace:
        rules.append(removal.requirement_rule(replace["config"]))
    return rules


def _install_experience(form: dict, existing: dict | None) -> dict | None:
    """The install experience to send, or None when the form changes nothing.

    Graph replaces the whole object, so what the form does not cover is copied
    from the existing app.
    """
    current = (existing or {}).get("installExperience") or DEFAULT_INSTALL_EXPERIENCE
    wanted = dict(current)
    run_as = form.get("run_as")
    if run_as not in (None, ""):
        if run_as not in RUN_AS:
            raise FormError("error.bad_run_as")
        wanted["runAsAccount"] = run_as
    if existing is not None and wanted == current:
        return None
    return wanted


def new_app_body(form: dict, analysis: dict) -> dict:
    name = _require(form, "display_name", "error.name_required")
    body = {
        "@odata.type": WIN32,
        "description": str(form.get("description") or "").strip() or name,
        "publisher": _require(form, "publisher", "error.publisher_required"),
        "isFeatured": False,
        "installExperience": _install_experience(form, None),
        "applicableArchitectures": ARCHITECTURES.get(form.get("architecture", "any"), "x86,x64"),
        "minimumSupportedWindowsRelease": "1607",
        "returnCodes": copy.deepcopy(DEFAULT_RETURN_CODES),
        "rules": _rules_for(form, analysis, None),
    }
    body.update(_content_fields(form, analysis))
    return body


def clone_app_body(existing: dict, form: dict, analysis: dict) -> dict:
    """A new app that is a copy of ``existing`` with the new installer."""
    body = {key: copy.deepcopy(value) for key, value in existing.items() if key not in READ_ONLY}
    body["@odata.type"] = WIN32
    body["rules"] = _rules_for(form, analysis, existing)
    body.update(_content_fields(form, analysis))
    for key in ("description", "publisher"):
        value = str(form.get(key) or "").strip()
        if value:
            body[key] = value
    experience = _install_experience(form, existing)
    if experience is not None:
        body["installExperience"] = experience
    if not body.get("largeIcon"):
        body.pop("largeIcon", None)
    return body


def inplace_patch(existing: dict, form: dict, analysis: dict, content_version: str) -> dict:
    """Changes applied to the existing app once the new content is committed."""
    patch = {"@odata.type": WIN32, "committedContentVersion": content_version}
    patch.update(_content_fields(form, analysis))
    patch["rules"] = _rules_for(form, analysis, existing)
    for key in ("description", "publisher"):
        value = str(form.get(key) or "").strip()
        if value:
            patch[key] = value
    experience = _install_experience(form, existing)
    if experience is not None:
        patch["installExperience"] = experience
    return patch


# --------------------------------------------------------------------------- form


def rename_for_version(name: str, old_version: str, new_version: str) -> str:
    """Replace the old version inside a display name by the new one.

    "7-Zip 23.01" with versions 23.01.00.0 -> 24.08.00.0 becomes "7-Zip 24.08":
    the name may carry only the first parts of the version.
    """
    old_parts, new_parts = old_version.split("."), new_version.split(".")
    if not old_version or not new_version:
        return name
    for count in range(len(old_parts), 1, -1):
        short = ".".join(old_parts[:count])
        if count <= len(new_parts) and re.search(rf"(?<![\d.]){re.escape(short)}(?![\d.])", name):
            return re.sub(
                rf"(?<![\d.]){re.escape(short)}(?![\d.])", ".".join(new_parts[:count]), name
            )
    return name


def propose_form(analysis: dict, existing: dict | None) -> dict:
    """Pre-fill the review form from the installer and, on update, the current app."""
    version = analysis.get("version", "")
    file_name = analysis["file_name"]
    warnings: list[str] = []
    if existing is None:
        if analysis["kind"] == "msi":
            detection = {"type": "msi", "product_code": analysis["msi"].get("product_code", "")}
        else:
            detection = {
                "type": "file",
                "path": "",
                "name": "",
                "operator": "version_ge",
                "value": version,
                "check32": False,
            }
        return {
            "display_name": analysis.get("product_name") or file_name.rsplit(".", 1)[0],
            "description": "",
            "publisher": analysis.get("publisher", ""),
            "version": version,
            "install_command": analysis.get("install_command", ""),
            "uninstall_command": analysis.get("uninstall_command", ""),
            "architecture": analysis.get("architecture", "any"),
            "run_as": "user"
            if (analysis.get("msi") or {}).get("package_type") == "perUser"
            else "system",
            "detection": detection,
            "warnings": warnings,
        }

    old_version = app_version(existing)
    old_file = existing.get("setupFilePath") or ""
    name = existing.get("displayName", "")
    name = rename_for_version(name, old_version, version)

    install = existing.get("installCommandLine") or ""
    if old_file and old_file in install:
        install = install.replace(old_file, file_name)
    elif analysis["kind"] == "msi" and not install:
        install = analysis.get("install_command", "")
    elif old_file != file_name:
        warnings.append("warning.install_command_unchanged")

    uninstall = existing.get("uninstallCommandLine") or ""
    new_code = (analysis.get("msi") or {}).get("product_code", "")
    if new_code and _GUID.search(uninstall):
        uninstall = _GUID.sub(new_code, uninstall)
    elif new_code and not uninstall:
        uninstall = analysis.get("uninstall_command", "")

    detection = detection_model(app_rules(existing))
    if detection["type"] == "msi":
        if new_code:
            detection["product_code"] = new_code
        else:
            detection = {
                "type": "file",
                "path": "",
                "name": "",
                "operator": "version_ge",
                "value": version,
                "check32": False,
            }
            warnings.append("warning.detection_needs_review")
    elif detection["type"] in ("file", "registry"):
        if detection["operator"] == "version_ge" or (
            detection["operator"] == "string_eq" and detection["value"] == old_version
        ):
            detection["value"] = version
        elif detection["operator"] == "exists":
            warnings.append("warning.detection_exists_only")
    else:
        warnings.append("warning.detection_kept")

    return {
        "display_name": name,
        "description": existing.get("description", ""),
        "publisher": existing.get("publisher", ""),
        "version": version,
        "install_command": install,
        "uninstall_command": uninstall,
        "run_as": run_as_of(existing),
        "detection": detection,
        "warnings": warnings,
        "current_version": old_version,
    }


# --------------------------------------------------------------------------- assignments

_TARGETS = {
    "group": "#microsoft.graph.groupAssignmentTarget",
    "allDevices": "#microsoft.graph.allDevicesAssignmentTarget",
    "allUsers": "#microsoft.graph.allLicensedUsersAssignmentTarget",
}


def target_key(target: dict) -> str:
    """Identify what an assignment points at (Intune allows one per target)."""
    return (target.get("groupId") or target.get("@odata.type", "")).lower()


def assignment_for(target: dict, intent: str, notifications: str = "showAll") -> dict:
    graph_target: dict = {"@odata.type": _TARGETS[target["type"]]}
    if target["type"] == "group":
        graph_target["groupId"] = target["id"]
    settings: dict = {
        "@odata.type": "#microsoft.graph.win32LobAppAssignmentSettings",
        "notifications": notifications,
        "restartSettings": None,
        "installTimeSettings": None,
        "deliveryOptimizationPriority": "notConfigured",
    }
    if intent == "available":
        # Without this, people who installed an "available" app from the
        # Company Portal would stay on the superseded version.
        settings["autoUpdateSettings"] = {
            "@odata.type": "#microsoft.graph.win32LobAppAutoUpdateSettings",
            "autoUpdateSupersededAppsState": "enabled",
        }
    return {
        "@odata.type": ASSIGNMENT,
        "intent": intent,
        "target": graph_target,
        "settings": settings,
    }


def clean_assignment(assignment: dict) -> dict:
    """Strip what Graph adds when reading so an assignment can be sent again."""
    return {
        "@odata.type": ASSIGNMENT,
        "intent": assignment.get("intent", "required"),
        "target": copy.deepcopy(assignment.get("target") or {}),
        "settings": copy.deepcopy(assignment.get("settings")),
    }


def merge_assignments(current: list[dict], extra: list[dict]) -> list[dict]:
    """``current`` plus the ``extra`` ones whose target is not already assigned."""
    merged = [clean_assignment(item) for item in current]
    seen = {target_key(item["target"]) for item in merged}
    for item in extra:
        key = target_key(item["target"])
        if key not in seen:
            seen.add(key)
            merged.append(clean_assignment(item))
    return merged


def uninstall_assignments(targets: list[dict]) -> list[dict]:
    """Assignments that remove the app from these targets, without a toast on the device."""
    return [assignment_for(target, "uninstall", notifications="hideAll") for target in targets]


def with_uninstall(current: list[dict], targets: list[dict]) -> list[dict]:
    """Current assignments of an app, with ``targets`` switched to uninstall.

    A target can only carry one intent: what it had before is replaced. Passing
    no target removes every uninstall assignment and keeps the rest.
    """
    wanted = uninstall_assignments(targets)
    replaced = {target_key(item["target"]) for item in wanted}
    kept = [
        clean_assignment(item)
        for item in current
        if target_key(item.get("target") or {}) not in replaced
        and (targets or item.get("intent") != "uninstall")
    ]
    return kept + wanted


def stage_assignments(stage: dict, notifications: str = "showAll") -> list[dict]:
    """Assignments of a stage; ``notifications`` is what the people on the devices see."""
    return [
        assignment_for(target, stage["intent"], notifications)
        for target in stage.get("targets", [])
    ]


def stage_is_live(stage: dict, assignments: list[dict]) -> bool:
    """True when every target of the stage is assigned on the app."""
    wanted = {target_key(item["target"]) for item in stage_assignments(stage)}
    current = {target_key(item.get("target") or {}) for item in assignments}
    return bool(wanted) and wanted <= current


def describe_assignment(assignment: dict) -> dict:
    target = assignment.get("target") or {}
    kind = target.get("@odata.type", "")
    simple = next((key for key, value in _TARGETS.items() if value == kind), "other")
    if kind == "#microsoft.graph.exclusionGroupAssignmentTarget":
        simple = "exclusion"
    return {
        "intent": assignment.get("intent", ""),
        "type": simple,
        "id": target.get("groupId"),
        "notifications": (assignment.get("settings") or {}).get("notifications"),
    }


def older_versions(relationships: list[dict]) -> list[dict]:
    """Apps this app supersedes (its previous versions)."""
    return [
        item
        for item in relationships
        if item.get("@odata.type") == SUPERSEDENCE and item.get("targetType") == "child"
    ]


def outgoing_relationships(
    relationships: list[dict], without: set[str] | None = None
) -> list[dict]:
    """Relationships to send back through ``updateRelationships``.

    Only the ones this app owns (``child`` targets), minus the ids in ``without``.
    """
    without = without or set()
    result = []
    for item in relationships:
        if item.get("targetType") != "child" or item.get("targetId") in without:
            continue
        entry = {"@odata.type": item.get("@odata.type"), "targetId": item.get("targetId")}
        for key in ("supersedenceType", "dependencyType"):
            if item.get(key):
                entry[key] = item[key]
        result.append(entry)
    return result
