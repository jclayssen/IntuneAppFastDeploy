"""The publishing workflows: new app, new version (supersedence), in-place update."""

from __future__ import annotations

from collections.abc import Callable

from . import appmodel
from .graph import Graph, GraphError
from .intunewin import Package
from .store import RolloutStore

Step = Callable[[str], None]
Progress = Callable[[int, int], None]


def _initial_assignments(
    graph: Graph, rollout: str, config: dict, previous_app_id: str | None
) -> list[dict]:
    """Who gets the freshly published app.

    ``pilot``: the pilot groups only. ``prod``: the production groups, or when
    none are configured the same assignments as the previous version.
    """
    if rollout == "pilot":
        return appmodel.stage_assignments(config["pilot"])
    if rollout == "prod":
        assignments = appmodel.stage_assignments(config["pilot"])
        production = appmodel.stage_assignments(config["prod"])
        if not production and previous_app_id:
            production = graph.assignments(previous_app_id)
        return appmodel.merge_assignments(assignments, production)
    return []


def publish_new(
    graph: Graph,
    store: RolloutStore,
    package: Package,
    analysis: dict,
    form: dict,
    step: Step,
    progress: Progress,
) -> dict:
    body = appmodel.new_app_body(form, analysis)
    step("step.create")
    app = graph.create_app(body)
    app_id = app["id"]
    try:
        step("step.upload")
        version = graph.upload_content(app_id, package, progress)
        step("step.commit")
        graph.patch_app(app_id, {"@odata.type": appmodel.WIN32, "committedContentVersion": version})
    except Exception:
        _discard(graph, app_id)
        raise
    config = store.set(app_id, body["displayName"], form.get("pilot"), form.get("prod"))
    assignments = _initial_assignments(graph, form.get("rollout", "none"), config, None)
    if assignments:
        step("step.assign")
        graph.assign(app_id, assignments)
    return {"app_id": app_id, "name": body["displayName"], "removed": []}


def publish_supersede(
    graph: Graph,
    store: RolloutStore,
    package: Package,
    analysis: dict,
    form: dict,
    current: dict,
    step: Step,
    progress: Progress,
    keep_versions: int = 2,
) -> dict:
    """Create a new app that supersedes ``current`` and keep at most two versions."""
    current_id = current["id"]
    body = appmodel.clone_app_body(current, form, analysis)
    current_relationships = graph.relationships(current_id)
    step("step.create")
    app = graph.create_app(body)
    app_id = app["id"]
    try:
        step("step.upload")
        version = graph.upload_content(app_id, package, progress)
        step("step.commit")
        graph.patch_app(app_id, {"@odata.type": appmodel.WIN32, "committedContentVersion": version})
        step("step.supersede")
        # The new app inherits the dependencies of the current one, not its
        # own supersedence links (those point at versions about to be removed).
        relationships = [
            item
            for item in appmodel.outgoing_relationships(current_relationships)
            if item["@odata.type"] != appmodel.SUPERSEDENCE
        ]
        relationships.append(
            {
                "@odata.type": appmodel.SUPERSEDENCE,
                "targetId": current_id,
                "supersedenceType": "replace"
                if form.get("supersedence_type") == "replace"
                else "update",
            }
        )
        graph.update_relationships(app_id, relationships)
    except Exception:
        _discard(graph, app_id)
        raise

    config = store.get(current_id)
    assignments = _initial_assignments(graph, form.get("rollout", "prod"), config, current_id)
    if assignments:
        step("step.assign")
        graph.assign(app_id, assignments)
    store.move(current_id, app_id, body["displayName"])

    removed: list[dict] = []
    failed: list[dict] = []
    if form.get("cleanup_old", True) and keep_versions <= 2:
        older = appmodel.older_versions(current_relationships)
        if older:
            step("step.cleanup")
            old_ids = {item["targetId"] for item in older}
            try:
                graph.update_relationships(
                    current_id, appmodel.outgoing_relationships(current_relationships, old_ids)
                )
            except GraphError as exc:
                failed = [_old(item, exc) for item in older]
                older = []
            for item in older:
                try:
                    graph.delete_app(item["targetId"])
                    store.delete(item["targetId"])
                    removed.append(_old(item))
                except GraphError as exc:
                    failed.append(_old(item, exc))
    return {
        "app_id": app_id,
        "name": body["displayName"],
        "removed": removed,
        "cleanup_failed": failed,
    }


def publish_inplace(
    graph: Graph,
    package: Package,
    analysis: dict,
    form: dict,
    current: dict,
    step: Step,
    progress: Progress,
) -> dict:
    """Replace the installer of the existing app. Assignments are untouched."""
    app_id = current["id"]
    # Validate the form before sending a single byte.
    appmodel.inplace_patch(current, form, analysis, "pending")
    step("step.upload")
    version = graph.upload_content(app_id, package, progress)
    step("step.commit")
    graph.patch_app(app_id, appmodel.inplace_patch(current, form, analysis, version))
    return {"app_id": app_id, "name": form.get("display_name", ""), "removed": []}


def promote(graph: Graph, store: RolloutStore, app_id: str) -> dict:
    """Add the production groups to an app that is in its pilot phase.

    Without configured production groups, the assignments of the previous
    version are used.
    """
    config = store.get(app_id)
    production = appmodel.stage_assignments(config["prod"])
    if not production:
        for item in appmodel.older_versions(graph.relationships(app_id)):
            production += graph.assignments(item["targetId"])
    if not production:
        raise appmodel.FormError("error.no_production_groups")
    merged = appmodel.merge_assignments(graph.assignments(app_id), production)
    graph.assign(app_id, merged)
    return {"assignments": len(merged)}


def _old(item: dict, error: Exception | None = None) -> dict:
    result = {
        "id": item.get("targetId"),
        "name": item.get("targetDisplayName", ""),
        "version": item.get("targetDisplayVersion", ""),
    }
    if error is not None:
        result["error"] = str(error)
    return result


def _discard(graph: Graph, app_id: str) -> None:
    """Remove a half-created app so a failed publish leaves nothing behind."""
    try:
        graph.delete_app(app_id)
    except Exception:  # noqa: BLE001, S110 - the original error matters more
        pass
