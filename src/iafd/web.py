"""The web server: a small JSON API plus the static single-page interface."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, appmodel, intunewin, publish
from .analyze import analyze
from .appmodel import FormError
from .auth import COOKIE, Auth, AuthError, Session
from .config import Settings
from .download import DownloadError, adopt_upload, download
from .graph import Graph, GraphError
from .jobs import Job, JobManager, describe_error
from .msi import MsiError
from .store import RolloutStore, StoreError

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

    for known in (FormError, StoreError, DownloadError, AuthError, GraphError, MsiError):
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
        return store.set(app_id, str(body.get("name", "")), body.get("pilot"), body.get("prod"))

    @app.post("/api/apps/{app_id}/promote")
    def promote(request: Request, app_id: str) -> dict:
        graph = make_graph(current(request))
        try:
            publish.promote(graph, store, app_id)
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

        def work(job: Job) -> None:
            job.set_step("step.package")
            package = ensure_package(job)
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
