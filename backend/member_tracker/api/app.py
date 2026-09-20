"""app.py — FastAPI application factory, DI seams, and admin API-key guard.

This is the foundation of the HTTP layer (task 11.1). It follows the
``backend/main.py`` conventions referenced in design.md: a FastAPI app, a
startup ``init_db()`` hook, an injected :class:`Clock`, and an API-key guard on
administrative endpoints. It intentionally provides **only wiring** — the
individual member/dashboard/query/recommendation route handlers live in their
own tasks (11.2, 11.3, 15.5, 19.1) and are registered into the app the factory
builds.

Design decisions realized here:

- **App factory (`create_app`).** Building the app inside a function (rather than
  a module-level singleton) lets tests construct isolated apps against a
  temporary DB and a :class:`FixedClock`, and lets deployment override the DB
  path / API key without import-time side effects. Later route tasks include
  their routers via :func:`create_app`'s ``routers`` argument or by importing the
  dependency seams below.

- **Injected Clock (never the wall clock).** The app stores a :class:`Clock` in
  ``app.state`` and exposes it via the :func:`get_clock` dependency. Production
  defaults to :class:`SystemClock`; tests pass a :class:`FixedClock` so
  date-dependent behavior (age-out windows, future-dated attendance) is
  reproducible. No route reads ``date.today()`` directly.

- **Repository / SnapshotService seams.** :func:`get_repository` and
  :func:`get_snapshot_service` construct the I/O adapters bound to the app's DB
  path and injected clock. Route tasks depend on these via ``Depends`` so they
  never hard-code construction and tests can override them with
  ``app.dependency_overrides``.

- **Startup ``init_db`` hook (Req 1.5 / persistence foundation).** On startup the
  app calls ``init_db(db_path)`` so the schema + CONFIG seed exist before any
  request is served, mirroring ``backend/main.py``'s ``@app.on_event("startup")``
  hook.

- **API-key guard on admin endpoints.** :func:`require_api_key` is a FastAPI
  dependency comparing the ``X-API-Key`` header against the app's configured
  key; administrative routes (threshold config, snapshots, export) attach it via
  ``Depends``. The key is read from the ``MEMBER_TRACKER_API_KEY`` env var at
  factory time (overridable per-app for tests).

An unguarded ``GET /api/health`` route is included as a liveness check and a
smoke target that needs no API key.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables from a local .env file if present (e.g. the
# backend's GEMINI_API_KEY). python-dotenv is a declared dependency; if it is
# somehow missing we degrade gracefully and rely on the real environment.
try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv

    load_dotenv(override=True)  # .env wins over empty/placeholder vars from start.sh
except ImportError:  # pragma: no cover
    pass

from member_tracker.core.clock import Clock, SystemClock
from member_tracker.io.database import init_db
from member_tracker.io.repository import Repository
from member_tracker.io.snapshot_service import SnapshotService

# Default admin API key; overridable via env for real deployments and via the
# ``api_key`` factory argument for tests. Mirrors backend/main.py's env-backed key.
DEFAULT_API_KEY = os.getenv("MEMBER_TRACKER_API_KEY", "member-tracker-dev-key")

# Header name carrying the admin API key (Req: API-key-guarded admin endpoints).
API_KEY_HEADER = "X-API-Key"


# ---------------------------------------------------------------------------
# Dependency seams — resolve shared services from app.state.
#
# These are the reusable injection points for the later route tasks. A handler
# declares e.g. ``repo: Repository = Depends(get_repository)`` and gets an
# adapter bound to the app's DB path + injected clock. Tests can swap any of
# them via ``app.dependency_overrides[get_clock] = lambda: FixedClock(...)``.
# ---------------------------------------------------------------------------


def get_clock(request: Request) -> Clock:
    """Return the app's injected :class:`Clock` (default :class:`SystemClock`)."""
    return request.app.state.clock


def get_db_path(request: Request) -> Optional[str]:
    """Return the app's configured SQLite path (``None`` ⇒ database.py default)."""
    return request.app.state.db_path


def get_repository(request: Request) -> Repository:
    """Construct a :class:`Repository` bound to the app's DB path + clock.

    The Repository is cheap to construct (it opens connections per operation), so
    a fresh instance per request is fine and keeps request handling stateless.
    """
    return Repository(
        db_path=request.app.state.db_path,
        clock=request.app.state.clock,
    )


def get_snapshot_service(request: Request) -> SnapshotService:
    """Construct a :class:`SnapshotService` bound to the app's DB path + clock."""
    return SnapshotService(
        db_path=request.app.state.db_path,
        clock=request.app.state.clock,
    )


def require_api_key(
    request: Request,
    x_api_key: str = Header(default="", alias=API_KEY_HEADER),
) -> None:
    """FastAPI dependency guarding administrative endpoints.

    Compares the ``X-API-Key`` header against the app's configured key. A missing
    or mismatched key yields ``401 Unauthorized`` before the handler runs.
    Administrative routes (threshold config, snapshot creation/listing/viewing,
    export) attach this via ``Depends(require_api_key)``.
    """
    expected = request.app.state.api_key
    if not x_api_key or not _constant_time_equals(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings without leaking length/content via timing.

    Uses :func:`secrets.compare_digest` so the API-key check is not a timing
    oracle. Falls back to a plain compare only if the inputs are not both ``str``.
    """
    import secrets

    try:
        return secrets.compare_digest(a, b)
    except TypeError:  # pragma: no cover - inputs are always str here
        return a == b


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    db_path: Optional[str] = None,
    clock: Optional[Clock] = None,
    api_key: Optional[str] = None,
    routers: Optional[Iterable[object]] = None,
    init_database: bool = True,
) -> FastAPI:
    """Construct and configure the Member_Tracker FastAPI app.

    Args:
        db_path: SQLite path for the Repository / SnapshotService. ``None`` uses
            ``io/database.py``'s default (env-backed) path. Tests pass a temp DB.
        clock: The injected :class:`Clock`. Defaults to :class:`SystemClock`;
            tests pass a :class:`FixedClock` for reproducible date behavior.
        api_key: Admin API key the guard compares against. Defaults to
            :data:`DEFAULT_API_KEY` (env-backed).
        routers: Optional iterable of ``APIRouter`` instances to include. Later
            route tasks (11.2, 11.3, 15.5, 19.1) pass their routers here (or call
            ``app.include_router`` after construction). Non-``APIRouter`` items
            are ignored so this stays forgiving during incremental wiring.
        init_database: When ``True`` (default) a startup hook calls
            ``init_db(db_path)`` so schema + CONFIG seed exist before serving.

    Returns:
        A configured :class:`FastAPI` app with shared services on ``app.state``,
        the admin API-key guard available via :func:`require_api_key`, and an
        unguarded ``GET /api/health`` liveness route.
    """
    app = FastAPI(
        title="Smart Member Growth Tracker",
        description=(
            "Centralized system of record for a JCI chapter's member journey: "
            "member records, age-out alerts, attendance milestones, health "
            "scores, handover snapshots/export, and natural-language querying."
        ),
        version="1.0.0",
    )

    # CORS — the React dashboard is served from a different origin (the Vite dev
    # server on http://localhost:5176) than this API (http://localhost:8000).
    # Without these headers the browser blocks every cross-origin /api/* fetch
    # ("Load failed"), even though the endpoints respond fine to curl. Allow the
    # common local dev origins (and any localhost/127.0.0.1 port via regex) so
    # the dashboard works out of the box. Override with MEMBER_TRACKER_CORS_ORIGINS
    # (comma-separated) for other deployments.
    _cors_origins_env = os.getenv("MEMBER_TRACKER_CORS_ORIGINS", "").strip()
    _cors_origins = (
        [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
        if _cors_origins_env
        else []
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared, per-app configuration resolved by the dependency seams above.
    app.state.db_path = db_path
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.api_key = api_key if api_key is not None else DEFAULT_API_KEY

    # Startup: ensure the schema + CONFIG defaults exist (Req 1.5 / persistence
    # foundation), mirroring backend/main.py's startup init_db hook.
    if init_database:

        @app.on_event("startup")
        def _startup_init_db() -> None:  # pragma: no cover - exercised via TestClient
            init_db(app.state.db_path)

    # Unguarded liveness/smoke route — needs no API key.
    @app.get("/api/health")
    def health_check() -> dict:
        return {"status": "ok", "service": "member-tracker", "version": "1.0.0"}

    # Register the known route modules that exist. This is done here (rather than
    # only via the ``routers=`` seam) so a plain ``create_app()`` / the
    # module-level ``app`` are fully wired by default.
    #
    # Ordering/robustness rationale:
    #   * routes_members (task 11.2) is a hard dependency of this app and is
    #     registered unconditionally — if it is missing, that is a real error.
    #   * sibling route modules that land in parallel tasks (e.g. routes_dashboard
    #     from task 11.3) are included best-effort inside a try/except ImportError
    #     so app creation never breaks just because a sibling module has not
    #     landed yet. Each is included at most once.
    _register_default_routers(app)

    # Register any routers explicitly provided by the caller (tests / later
    # tasks). Kept forgiving: only actual APIRouter instances are included so
    # partial wiring never crashes.
    if routers:
        from fastapi import APIRouter

        for router in routers:
            if isinstance(router, APIRouter):
                app.include_router(router)

    # Optional demo mode: when MEMBER_TRACKER_DEMO_SEARCH is truthy, inject an
    # offline deterministic search backend so the Wellington Retention Agent
    # produces a real (template-mode) recommendation without a live search API
    # or LLM. This only overrides the get_search_backend DI seam; the pure
    # verify_recommendation guarantee is unchanged (see demo_search_backend.py).
    # Never on by default — production/tests keep the not-configured backend.
    if os.getenv("MEMBER_TRACKER_DEMO_SEARCH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            from member_tracker.api.routes_recommendations import get_search_backend
            from member_tracker.io.demo_search_backend import DemoSearchBackend

            _demo_backend = DemoSearchBackend()
            app.dependency_overrides[get_search_backend] = lambda: _demo_backend
        except ImportError:
            # Recommendations router not present yet — nothing to override.
            pass

    # ---------------------------------------------------------------------
    # Static frontend (single-service deploy).
    #
    # When a built React bundle is present (Vite ``dist/``), serve it from this
    # same app so the whole product runs on ONE public URL (e.g. on Render):
    #   * ``/assets/*`` and other build files are served directly.
    #   * any non-``/api`` path falls back to ``index.html`` so client-side
    #     routing / deep links work (SPA fallback).
    # The API (``/api/*``) and docs (``/docs``) are registered above and take
    # precedence. If no build is present (pure local dev with the Vite server),
    # this is skipped entirely. The location is overridable via
    # MEMBER_TRACKER_FRONTEND_DIST.
    # ---------------------------------------------------------------------
    _dist_env = os.getenv("MEMBER_TRACKER_FRONTEND_DIST", "").strip()
    _dist_dir = _dist_env or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "frontend",
        "dist",
    )
    if os.path.isdir(_dist_dir) and os.path.isfile(
        os.path.join(_dist_dir, "index.html")
    ):
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        _assets_dir = os.path.join(_dist_dir, "assets")
        if os.path.isdir(_assets_dir):
            app.mount(
                "/assets", StaticFiles(directory=_assets_dir), name="assets"
            )

        _index_path = os.path.join(_dist_dir, "index.html")

        @app.get("/", include_in_schema=False)
        def _spa_root():  # pragma: no cover - static serving
            return FileResponse(_index_path)

        @app.get("/{full_path:path}", include_in_schema=False)
        def _spa_fallback(full_path: str):  # pragma: no cover - static serving
            # Never shadow the API or docs.
            if full_path.startswith("api/") or full_path in (
                "docs",
                "redoc",
                "openapi.json",
            ):
                from fastapi import HTTPException

                raise HTTPException(status_code=404)
            # Serve a real static file when it exists (favicon, etc.), else the
            # SPA entry point so client-side routes resolve.
            candidate = os.path.join(_dist_dir, full_path)
            if full_path and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(_index_path)

    return app


def _register_default_routers(app: FastAPI) -> None:
    """Include the built-in route modules onto ``app``.

    ``routes_members`` (task 11.2) is required and registered unconditionally.
    Sibling modules that may not exist yet (``routes_dashboard`` from task 11.3,
    and later ``routes_query`` / ``routes_recommendations``) are included
    best-effort so a missing sibling never breaks app creation.
    """
    # Required: the member/age-out/attendance routes.
    from member_tracker.api.routes_members import router as members_router

    app.include_router(members_router)

    # Best-effort siblings — present only once their task has landed.
    for module_name, attr in (
        ("member_tracker.api.routes_dashboard", "router"),
        ("member_tracker.api.routes_query", "router"),
        ("member_tracker.api.routes_recommendations", "router"),
        ("member_tracker.api.routes_gamification", "router"),
        ("member_tracker.api.routes_ask", "router"),
        ("member_tracker.api.routes_events", "router"),
        ("member_tracker.api.routes_activities", "router"),
        ("member_tracker.api.routes_identity", "router"),
        ("member_tracker.api.routes_applications", "router"),
        ("member_tracker.api.routes_member_auth", "router"),
        ("member_tracker.api.routes_trivia", "router"),
    ):
        try:
            module = __import__(module_name, fromlist=[attr])
        except ImportError:
            continue
        sibling_router = getattr(module, attr, None)
        if sibling_router is not None:
            app.include_router(sibling_router)


# A module-level default app instance for ``uvicorn member_tracker.api.app:app``.
# Route tasks may include their routers onto this instance as they land.
app = create_app()


__all__ = [
    "DEFAULT_API_KEY",
    "API_KEY_HEADER",
    "create_app",
    "app",
    "get_clock",
    "get_db_path",
    "get_repository",
    "get_snapshot_service",
    "require_api_key",
]
