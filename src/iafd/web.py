"""The web server: a small JSON API plus the static single-page interface."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, appmodel, intunewin, inventory, protected, publish, removal
from .analyze import analyze
from .appmodel import FormError
from .auth import COOKIE, Auth, AuthError, Session
from .config import Settings
from .download import DownloadError, adopt_upload, download
from .graph import Graph, GraphError
from .jobs import Job, JobManager, describe_error
from .msi import MsiError
from .removal import RemovalError
from .store import RemovalStore, RolloutStore, StoreError

HERE = Path(__file__).parent
STATIC = HERE / "static"
LOCALES = HERE / "locales"
CSRF_HEADER = "x-iafd"


def available_languages() -> dict[str, str]:
    """Every ``locales/<code>.json`` file is a language; its ``_name`` is the label."""
    languages = {}
    for path in sorted(LOCALES.glob("*.json")):
        try:
            languages[path.stem] = json.loads(path.read_text(encoding="utf-8")).get(
                "_name", path.stem
            )
        except (OSError, json.JSONDecodeError):
            continue
    return languages


def create_app(
    settings: Settings | None = None,
    graph_factory: Callable[[Callable[[], str]], Graph] | None = None,
    auth: Auth | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    auth = auth or Auth(settings)
    store = RolloutStore(settings.config_dir)
    removals = RemovalStore(settings.config_dir)
    jobs = JobManager(settings.work_dir)
    app = FastAPI(title="IntuneAppFastDeploy", docs_url=None, redoc_url=None, openapi_url=None)

    def make_graph(session: Session) -> Graph:
        def token() -> str:
            return auth.token(session)

        if graph_factory:
            return graph_factory(token)
        return Graph(token, settings.graph_url)

    def current(request: Request) -> Session:
        session = auth.session(request.cookies.get(COOKIE))
        if session is None:
            raise HTTPException(401, {"code": "error.sign_in_required", "detail": ""})
        if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, {"code": "error.csrf", "detail": ""})
        return session

    def identifier(value: str) -> str:
        try:
            return inventory.safe_id(value)
        except ValueError as exc:
            raise HTTPException(400, {"code": str(exc), "detail": ""}) from exc

    def job_for(request: Request, job_id: str) -> tuple[Session, Job]:
        session = current(request)
        job = jobs.get(job_id, session.user_id)
        if job is None:
            raise HTTPException(404, {"code": "error.job_not_found", "detail": ""})
        return session, job

    @app.exception_handler(HTTPException)
    def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = (
            exc.detail if isinstance(exc.detail, dict) else {"code": "error.http", "detail": ""}
        )
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    def known_error(_: Request, exc: Exception) -> JSONResponse:
        error = describe_error(exc)
        status = 400
        if error["code"] == "error.session_expired":
            status = 401
        elif error["code"] in ("error.graph", "error.graph_forbidden"):
            status = 502
        return JSONResponse({"error": error}, status_code=status)

    for known in (
        FormError,
        StoreError,
        DownloadError,
        AuthError,
        GraphError,
        MsiError,
        RemovalError,
    ):
        app.add_exception_handler(known, known_error)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------------ public

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict:
        return {
            "version": __version__,
            "configured": settings.configured,
            "languages": available_languages(),
            "default_language": settings.default_language,
            "group_picker": settings.enable_group_picker,
            "device_inventory": settings.enable_device_inventory,
            "remediations": settings.remediations,
            "device_groups": settings.device_groups,
            "max_download_mb": settings.max_download_mb,
            "redirect_uri": auth.redirect_uri,
            "config_writable": store.writable(),
        }

    @app.get("/auth/login")
    def login() -> Response:
        try:
            return RedirectResponse(auth.begin_login(), status_code=302)
        except AuthError as exc:
            return RedirectResponse(f"/?login_error={exc.code}", status_code=302)

    @app.get("/auth/callback")
    def callback(request: Request) -> Response:
        try:
            session = auth.finish_login(dict(request.query_params))
        except AuthError as exc:
            return RedirectResponse(f"/?login_error={exc.code}", status_code=302)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            COOKIE,
            session.id,
            max_age=settings.session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookies,
        )
        return response

    @app.post("/auth/logout")
    def logout(request: Request) -> Response:
        current(request)
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    # ------------------------------------------------------------------ apps

    @app.get("/api/me")
    def me(request: Request) -> dict:
        session = current(request)
        return {"name": session.user_name, "login": session.login}

    @app.get("/api/apps")
    def list_apps(request: Request) -> dict:
        graph = make_graph(current(request))
        try:
            return {"apps": graph.list_apps()}
        finally:
            graph.close()

    def app_detail(graph: Graph, app_id: str) -> dict:
        app_data = graph.get_app(app_id)
        if app_data.get("@odata.type") not in (appmodel.WIN32, None):
            raise HTTPException(400, {"code": "error.not_win32", "detail": ""})
        assignments = graph.assignments(app_id)
        relationships = graph.relationships(app_id)
        rollout = store.get(app_id)
        names = {
            target["id"]: target.get("name", "")
            for stage in (rollout["pilot"], rollout["prod"])
            for target in stage["targets"]
            if target["type"] == "group"
        }
        described = []
        for assignment in assignments:
            item = appmodel.describe_assignment(assignment)
            if item["id"]:
                item["name"] = names.get(item["id"].lower(), "")
            described.append(item)
        older = appmodel.older_versions(relationships)
        # Production = the configured groups, or failing that whoever had the
        # previous version.
        production = appmodel.stage_assignments(rollout["prod"])
        if not production:
            for item in older:
                production += graph.assignments(item["targetId"])
        wanted = {appmodel.target_key(item.get("target") or {}) for item in production}
        assigned = {appmodel.target_key(item.get("target") or {}) for item in assignments}
        prod_live = bool(wanted) and wanted <= assigned
        pilot_live = appmodel.stage_is_live(rollout["pilot"], assignments)
        if prod_live:
            stage = "production"
        elif pilot_live:
            stage = "pilot"
        else:
            stage = "assigned" if assignments else "unassigned"
        summary = appmodel.summarize(app_data)
        summary.update(
            description=app_data.get("description", ""),
            install_command=app_data.get("installCommandLine", ""),
            uninstall_command=app_data.get("uninstallCommandLine", ""),
            run_as=appmodel.run_as_of(app_data),
            detection=appmodel.detection_model(appmodel.app_rules(app_data)),
        )
        newer = [
            item
            for item in relationships
            if item.get("@odata.type") == appmodel.SUPERSEDENCE
            and item.get("targetType") == "parent"
        ]
        return {
            "app": summary,
            "assignments": described,
            "older": [publish._old(item) for item in older],
            "newer": [publish._old(item) for item in newer],
            "rollout": rollout,
            "stage": stage,
            "can_promote": stage == "pilot" and bool(wanted),
        }

    @app.get("/api/apps/{app_id}")
    def get_app(request: Request, app_id: str) -> dict:
        graph = make_graph(current(request))
        try:
            return app_detail(graph, app_id)
        finally:
            graph.close()

    @app.put("/api/apps/{app_id}/rollout")
    def save_rollout(request: Request, app_id: str, body: dict = Body(...)) -> dict:  # noqa: B008
        current(request)
        return store.set(
            app_id,
            str(body.get("name", "")),
            body.get("pilot"),
            body.get("prod"),
            body.get("notifications"),
        )

    @app.post("/api/apps/{app_id}/promote")
    def promote(request: Request, app_id: str) -> dict:
        graph = make_graph(current(request))
        try:
            publish.promote(graph, store, app_id)
            return app_detail(graph, app_id)
        finally:
            graph.close()

    @app.post("/api/apps/{app_id}/uninstall")
    def app_uninstall(request: Request, app_id: str, body: dict = Body(...)) -> dict:  # noqa: B008
        """Remove an Intune-managed app from some targets (no target = stop removing)."""
        graph = make_graph(current(request))
        try:
            targets = publish.removal_targets(body.get("targets"))
            current_assignments = graph.assignments(identifier(app_id))
            graph.assign(app_id, appmodel.with_uninstall(current_assignments, targets))
            return app_detail(graph, app_id)
        finally:
            graph.close()

    @app.get("/api/groups")
    def groups(request: Request, q: str = "") -> dict:
        session = current(request)
        if not settings.enable_group_picker:
            raise HTTPException(404, {"code": "error.group_picker_disabled", "detail": ""})
        graph = make_graph(session)
        try:
            return {"groups": graph.search_groups(q[:100])}
        finally:
            graph.close()

    # ------------------------------------------------------------------ inventory

    INVENTORY_TTL = 300

    def inventory_session(request: Request) -> Session:
        session = current(request)
        if not settings.enable_device_inventory:
            raise HTTPException(404, {"code": "error.inventory_disabled", "detail": ""})
        return session

    def cached(session: Session, key: str, refresh: bool, load: Callable[[Graph], list]) -> dict:
        """Intune inventory is slow to read and changes once a day: keep it a few minutes."""
        entry = session.data.get(key)
        if refresh or entry is None or time.time() - entry["at"] > INVENTORY_TTL:
            graph = make_graph(session)
            try:
                entry = {"at": time.time(), "items": load(graph)}
            finally:
                graph.close()
            session.data[key] = entry
        return entry

    @app.get("/api/inventory/apps")
    def inventory_apps(request: Request, q: str = "", page: int = 1, refresh: bool = False) -> dict:
        session = inventory_session(request)
        entry = cached(
            session, "detected", refresh, lambda g: inventory.group_apps(g.detected_apps())
        )
        found = inventory.search(entry["items"], q[:100], ("name", "publisher"))
        result = inventory.paginate(found, page)
        extra = protected.load_extra(settings.config_dir)
        result["items"] = [
            {**item, "protected": protected.is_protected(item["name"], extra)}
            for item in result["items"]
        ]
        return {**result, "fetched_at": entry["at"]}

    @app.get("/api/inventory/apps/{detected_id}/devices")
    def inventory_app_devices(request: Request, detected_id: str) -> dict:
        session = inventory_session(request)
        graph = make_graph(session)
        try:
            devices = graph.detected_app_devices(identifier(detected_id))
        finally:
            graph.close()
        rows = [inventory.summarize_device(device) for device in devices]
        return {"devices": sorted(rows, key=lambda row: row["name"].casefold())}

    @app.get("/api/inventory/devices")
    def inventory_devices(
        request: Request, q: str = "", page: int = 1, refresh: bool = False
    ) -> dict:
        session = inventory_session(request)
        entry = cached(
            session,
            "devices",
            refresh,
            lambda g: sorted(
                (inventory.summarize_device(device) for device in g.managed_devices()),
                key=lambda row: row["name"].casefold(),
            ),
        )
        found = inventory.search(entry["items"], q[:100], ("name", "user", "model"))
        return {**inventory.paginate(found, page), "fetched_at": entry["at"]}

    @app.get("/api/inventory/devices/{device_id}")
    def inventory_device(request: Request, device_id: str) -> dict:
        session = inventory_session(request)
        graph = make_graph(session)
        try:
            device = graph.device_with_apps(identifier(device_id))
        finally:
            graph.close()
        extra = protected.load_extra(settings.config_dir)
        apps = inventory.device_apps(device.get("detectedApps") or [])
        return {
            "device": inventory.summarize_device(device),
            "apps": [
                {**app, "protected": protected.is_protected(app["name"], extra)} for app in apps
            ],
        }

    # ------------------------------------------------------------------ removals

    def removal_rule(rule_id: str) -> dict:
        rule = removals.get(identifier(rule_id))
        if rule is None:
            raise HTTPException(404, {"code": "error.removal_not_found", "detail": ""})
        return rule

    def editable_rule(rule_id: str) -> dict:
        rule = removal_rule(rule_id)
        if rule.get("mechanism") == "remediation":
            # An on-demand removal has no targets and is started again from a device.
            raise RemovalError("error.removal_not_editable")
        return rule

    def detected_groups(session: Session) -> list[dict]:
        return cached(
            session, "detected", False, lambda g: inventory.group_apps(g.detected_apps())
        )["items"]

    def removal_preview(session: Session, raw: dict) -> dict:
        config = removal.clean_config(raw)
        rows = removal.matching_installs(detected_groups(session), config)
        return {
            "config": config,
            "matches": rows,
            "installs": sum(row["devices"] for row in rows),
            "protected": protected.blocked(
                config["names"], protected.load_extra(settings.config_dir)
            ),
            "confirm": config["names"][0],
        }

    @app.post("/api/removals/preview")
    def removals_preview(request: Request, body: dict = Body(...)) -> dict:  # noqa: B008
        return removal_preview(inventory_session(request), body.get("config") or {})

    @app.get("/api/removals")
    def removals_list(request: Request) -> dict:
        inventory_session(request)
        return {"rules": removals.all()}

    @app.post("/api/removals")
    def removals_create(request: Request, body: dict = Body(...)) -> dict:  # noqa: B008
        session = inventory_session(request)
        raw = body.get("config") or {}
        # Validate everything before the job starts, so mistakes show at once.
        config = publish.check_removal(raw, settings.config_dir)
        targets = publish.removal_targets(body.get("targets"))
        if str(body.get("confirm", "")).strip().casefold() != config["names"][0].casefold():
            raise RemovalError("error.removal_confirm")
        job = jobs.create(session.user_id)

        def work(job: Job) -> None:
            graph = make_graph(session)
            try:
                rule = publish.publish_removal(
                    graph,
                    removals,
                    raw,
                    targets,
                    bool(body.get("permanent")),
                    session.login,
                    job.work_dir,
                    settings.config_dir,
                    job.set_step,
                    job.set_progress,
                )
            finally:
                graph.close()
            job.result = {"removal": {"id": rule["id"], "label": rule["label"]}}

        jobs.run(job, work, final_state="done", busy_state="running")
        return job.public()

    @app.post("/api/removals/ondemand")
    def removals_on_demand(request: Request, body: dict = Body(...)) -> dict:  # noqa: B008
        """Remove software from one device now (Intune remediation, run on demand)."""
        session = inventory_session(request)
        if not settings.remediations:
            raise HTTPException(404, {"code": "error.remediations_disabled", "detail": ""})
        raw = body.get("config") or {}
        config = publish.check_removal(raw, settings.config_dir)
        if str(body.get("confirm", "")).strip().casefold() != config["names"][0].casefold():
            raise RemovalError("error.removal_confirm")
        graph = make_graph(session)
        try:
            rule = publish.remove_on_device(
                graph,
                removals,
                raw,
                identifier(str(body.get("device_id", ""))),
                session.login,
                settings.config_dir,
            )
        finally:
            graph.close()
        return {"rule": rule}

    @app.get("/api/removals/{rule_id}")
    def removals_detail(request: Request, rule_id: str) -> dict:
        session = inventory_session(request)
        rule = removal_rule(rule_id)
        on_demand = rule.get("mechanism") == "remediation"
        graph = make_graph(session)
        try:
            if on_demand:
                states = graph.health_script_states(rule["script_id"])
                results = {"available": True, **removal.run_rows(states, rule.get("devices", []))}
            else:
                statuses = graph.device_statuses(rule["app_id"])
                results = {"available": True, **removal.status_rows(statuses)}
        except GraphError as exc:
            # Intune may refuse this report: the rule still works, show where to look instead.
            results = {"available": False, "detail": f"{exc.code}: {exc.message}"}
        finally:
            graph.close()
        rows = removal.matching_installs(detected_groups(session), rule["config"])
        portal = "https://intune.microsoft.com/#view/Microsoft_Intune_Apps/SettingsMenu/~/0/appId/"
        return {
            "rule": rule,
            "results": results,
            "remaining": sum(row["devices"] for row in rows),
            "portal_url": None if on_demand else portal + rule["app_id"],
        }

    def rule_devices(session: Session, graph: Graph, rule: dict) -> dict[str, dict]:
        """Managed devices that have the software of a rule, by managed device id."""
        wanted = {name.casefold() for name in rule["config"]["names"]}
        found: dict[str, dict] = {}
        for group in detected_groups(session):
            if group["name"].casefold() not in wanted:
                continue
            for entry in group["versions"]:
                if not removal.version_wanted(rule["config"]["versions"], entry["version"]):
                    continue
                for device in graph.detected_app_devices(entry["id"]):
                    found.setdefault(device["id"], {**device, "_version": entry["version"]})
        return found

    def device_groups_rule(request: Request, rule_id: str) -> tuple[Session, dict]:
        session = inventory_session(request)
        if not settings.device_groups:
            raise HTTPException(404, {"code": "error.device_groups_disabled", "detail": ""})
        return session, editable_rule(rule_id)

    @app.get("/api/removals/{rule_id}/devices")
    def removals_devices(request: Request, rule_id: str) -> dict:
        """Devices that have the software, to pick the ones the removal should reach."""
        session, rule = device_groups_rule(request, rule_id)
        graph = make_graph(session)
        try:
            found = rule_devices(session, graph, rule)
        finally:
            graph.close()
        chosen = {device["id"] for device in (rule.get("device_group") or {}).get("devices", [])}
        rows = [
            {
                **inventory.summarize_device(device),
                "version": device["_version"],
                "chosen": device["id"] in chosen,
            }
            for device in found.values()
        ]
        return {"devices": sorted(rows, key=lambda row: row["name"].casefold())}

    @app.post("/api/removals/{rule_id}/devices")
    def removals_pick_devices(request: Request, rule_id: str, body: dict = Body(...)) -> dict:  # noqa: B008
        session, rule = device_groups_rule(request, rule_id)
        asked = {identifier(str(value)) for value in body.get("device_ids") or []}
        if not asked:
            raise RemovalError("error.removal_no_device")
        graph = make_graph(session)
        try:
            # Only devices Intune itself reports as having the software can be picked.
            found = rule_devices(session, graph, rule)
            devices = [found[device_id] for device_id in asked if device_id in found]
            if len(devices) != len(asked):
                raise RemovalError("error.removal_unknown_device")
            rule, missing = publish.target_devices(graph, removals, rule, devices)
        finally:
            graph.close()
        return {"rule": rule, "missing": missing}

    @app.put("/api/removals/{rule_id}/targets")
    def removals_targets(request: Request, rule_id: str, body: dict = Body(...)) -> dict:  # noqa: B008
        session = inventory_session(request)
        rule = editable_rule(rule_id)
        graph = make_graph(session)
        try:
            return {"rule": publish.retarget_removal(graph, removals, rule, body.get("targets"))}
        finally:
            graph.close()

    @app.put("/api/removals/{rule_id}")
    def removals_update(request: Request, rule_id: str, body: dict = Body(...)) -> dict:  # noqa: B008
        session = inventory_session(request)
        rule = editable_rule(rule_id)
        raw = body.get("config") or {}
        config = publish.check_removal(raw, settings.config_dir)
        if str(body.get("confirm", "")).strip().casefold() != config["names"][0].casefold():
            raise RemovalError("error.removal_confirm")
        job = jobs.create(session.user_id)

        def work(job: Job) -> None:
            graph = make_graph(session)
            try:
                updated = publish.update_removal(
                    graph,
                    removals,
                    rule,
                    raw,
                    job.work_dir,
                    settings.config_dir,
                    job.set_step,
                    job.set_progress,
                )
            finally:
                graph.close()
            job.result = {"removal": {"id": updated["id"], "label": updated["label"]}}

        jobs.run(job, work, final_state="done", busy_state="running")
        return job.public()

    @app.post("/api/removals/{rule_id}/finish")
    def removals_finish(request: Request, rule_id: str) -> dict:
        session = inventory_session(request)
        rule = removal_rule(rule_id)
        graph = make_graph(session)
        try:
            publish.finish_removal(graph, removals, rule)
        finally:
            graph.close()
        return {"ok": True}

    # ------------------------------------------------------------------ jobs

    def start_analysis(
        session: Session,
        app_id: str,
        fetch: Callable[[Job], object],
        before: Callable[[Job], None] | None = None,
    ) -> Job:
        job = jobs.create(session.user_id)
        job.set_step("step.download")
        if before:
            # Runs inside the request: an uploaded file is gone once it returns.
            try:
                before(job)
            except Exception:
                jobs.remove(job)
                raise

        def work(job: Job) -> None:
            result = fetch(job)
            job.set_step("step.analyze")
            analysis = analyze(result).to_dict()
            job.data.update(path=result.path, analysis=analysis, app_id=app_id or None)
            existing = None
            older: list[dict] = []
            rollout = store.get(app_id) if app_id else store.get("")
            if app_id:
                graph = make_graph(session)
                try:
                    existing = graph.get_app(app_id)
                    older = [
                        publish._old(item)
                        for item in appmodel.older_versions(graph.relationships(app_id))
                    ]
                finally:
                    graph.close()
                job.data["existing"] = existing
            job.result = {
                "analysis": analysis,
                "form": appmodel.propose_form(analysis, existing),
                "existing": appmodel.summarize(existing) if existing else None,
                "older": older,
                "rollout": rollout,
            }

        jobs.run(job, work, final_state="ready", busy_state="running")
        return job

    @app.post("/api/analyze")
    def analyze_link(request: Request, body: dict = Body(...)) -> dict:  # noqa: B008
        session = current(request)
        url = str(body.get("url", "")).strip()
        if not url:
            raise DownloadError("error.url_required")
        limit = settings.max_download_mb * 1024 * 1024

        def fetch(job: Job):
            return download(url, job.work_dir, limit, job.set_progress)

        return start_analysis(session, str(body.get("app_id") or ""), fetch).public()

    @app.post("/api/analyze/upload")
    def analyze_upload(
        request: Request,
        file: UploadFile = File(...),  # noqa: B008
        app_id: str = Form(""),
    ) -> dict:
        session = current(request)
        limit = settings.max_download_mb * 1024 * 1024
        name = file.filename or "setup"

        def save(job: Job) -> None:
            temp = job.work_dir / "upload.part"
            with temp.open("wb") as out:
                shutil.copyfileobj(file.file, out, 1024 * 1024)
            if temp.stat().st_size > limit:
                raise DownloadError("error.too_large")

        def fetch(job: Job):
            return adopt_upload(job.work_dir / "upload.part", name, job.work_dir)

        return start_analysis(session, app_id, fetch, save).public()

    @app.get("/api/jobs/{job_id}")
    def job_status(request: Request, job_id: str) -> dict:
        return job_for(request, job_id)[1].public()

    @app.delete("/api/jobs/{job_id}")
    def job_delete(request: Request, job_id: str) -> dict:
        _, job = job_for(request, job_id)
        if job.state in ("running", "publishing"):
            raise HTTPException(409, {"code": "error.job_busy", "detail": ""})
        jobs.remove(job)
        return {"ok": True}

    def ensure_package(job: Job) -> intunewin.Package:
        if "package" not in job.data:
            job.data["package"] = intunewin.build_package(job.data["path"], job.work_dir / "pkg")
        return job.data["package"]

    def replacement(form: dict) -> dict | None:
        """Settings of "remove the older version first", validated, or None."""
        replace = removal.clean_replace(form.get("replace"), str(form.get("version") or ""))
        if replace and replace["mode"] == "reinstall":
            # The wrapper uninstalls software: the protected list applies.
            publish.check_removal(replace["config"], settings.config_dir)
            return replace
        return None

    def publish_package(job: Job, form: dict) -> intunewin.Package:
        """The package to send: the installer alone, or with the scripts that
        remove the older version before installing."""
        replace = replacement(form)
        if replace is None:
            return ensure_package(job)
        config = replace["config"]
        folder = job.work_dir / "replace"
        folder.mkdir(parents=True, exist_ok=True)
        remove = folder / removal.script_file_name(config)
        remove.write_text(removal.render_script(config, "Uninstall"), encoding="utf-8-sig")
        wrapper = folder / removal.replace_script_name(config)
        wrapper.write_text(
            removal.render_replace_script(config, str(form.get("install_command") or "")),
            encoding="utf-8-sig",
        )
        return intunewin.build_package(
            wrapper, job.work_dir / "pkg-replace", [job.data["path"], remove]
        )

    @app.get("/api/jobs/{job_id}/intunewin")
    def job_intunewin(request: Request, job_id: str) -> Response:
        _, job = job_for(request, job_id)
        if job.state != "ready":
            raise HTTPException(409, {"code": "error.job_not_ready", "detail": ""})
        analysis = job.data["analysis"]
        package = ensure_package(job)
        name = appmodel.intunewin_name(analysis["file_name"])
        out = intunewin.write_intunewin(
            package,
            job.work_dir / "download.intunewin",
            {
                **analysis.get("msi", {}),
                "version": analysis["version"],
                "publisher": analysis["publisher"],
            },
        )
        return FileResponse(out, filename=name, media_type="application/octet-stream")

    @app.post("/api/jobs/{job_id}/publish")
    def job_publish(request: Request, job_id: str, form: dict = Body(...)) -> dict:  # noqa: B008
        session, job = job_for(request, job_id)
        if job.state != "ready":
            raise HTTPException(409, {"code": "error.job_not_ready", "detail": ""})
        analysis = job.data["analysis"]
        existing = job.data.get("existing")
        mode = form.get("mode") if existing else "new"
        if mode not in ("new", "supersede", "inplace"):
            raise appmodel.FormError("error.bad_mode")
        # Fail fast on an incomplete form, before the long upload starts.
        if mode == "new":
            appmodel.new_app_body(form, analysis)
        elif mode == "supersede":
            appmodel.clone_app_body(existing, form, analysis)
        else:
            appmodel.inplace_patch(existing, form, analysis, "pending")
        if mode != "inplace":
            publish.check_rollout(form, new_app=mode == "new")
        replacement(form)

        def work(job: Job) -> None:
            job.set_step("step.package")
            package = publish_package(job, form)
            graph = make_graph(session)
            try:
                if mode == "new":
                    outcome = publish.publish_new(
                        graph, store, package, analysis, form, job.set_step, job.set_progress
                    )
                elif mode == "supersede":
                    outcome = publish.publish_supersede(
                        graph,
                        store,
                        package,
                        analysis,
                        form,
                        existing,
                        job.set_step,
                        job.set_progress,
                        settings.keep_versions,
                    )
                else:
                    outcome = publish.publish_inplace(
                        graph, package, analysis, form, existing, job.set_step, job.set_progress
                    )
            finally:
                graph.close()
            job.result = {**job.result, "outcome": {**outcome, "mode": mode}}

        job.steps.clear()
        jobs.run(job, work, final_state="done", busy_state="publishing")
        return job.public()

    # ------------------------------------------------------------------ static

    @app.get("/locales/{language}.json")
    def locale(language: str) -> Response:
        # The path served comes from the folder listing, never from the request.
        path = {item.stem: item for item in LOCALES.glob("*.json")}.get(language)
        if path is None:
            raise HTTPException(404, {"code": "error.http", "detail": ""})
        return FileResponse(path, media_type="application/json")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def main() -> None:
    import os

    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - inside a container
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
