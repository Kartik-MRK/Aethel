"""Hub application factory.

Wiring only: build config, open storage, mount the API and the dashboard.
Keeping this thin means the app can be constructed in a test with a temporary
data directory and no environment mutation, which is what makes the Hub
testable without a running server.
"""

import os
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hub.api import router as api_router
from hub.config import HubConfig
from hub.errors import register_error_handlers
from hub.log import AnchorStore, TransparencyLog
from hub.security import SecurityHeadersMiddleware
from hub.storage import HubStorage
from hub.views import router as views_router
from hub.views import templates

HUB_VERSION = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent


def _git_sha() -> str:
    """Short SHA of the running checkout, for the version endpoint.

    Best-effort: a deployment from a tarball has no git metadata, and that is
    not an error. Returning "unknown" is more useful than raising.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PACKAGE_DIR.parent,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def create_app(config: HubConfig | None = None) -> FastAPI:
    """Build the Hub application.

    Pass an explicit `config` in tests to point at a temporary directory;
    production reads the environment.
    """
    config = config or HubConfig()
    config.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        app.state.storage = HubStorage(config.data_dir)
        app.state.log = TransparencyLog(config.log_path)
        app.state.anchors = AnchorStore(config.anchors_path)
        app.state.hub_version = HUB_VERSION
        app.state.git_sha = _git_sha()
        app.state.started_at = datetime.now(timezone.utc).isoformat()
        yield

    app = FastAPI(
        title="Aethel Hub",
        description=(
            "Registry and transparency log for LoRA adapter provenance. "
            "Clients verify every download against its content hash, so this "
            "server is never a trusted party."
        ),
        version=HUB_VERSION,
        lifespan=lifespan,
        # Swagger UI and ReDoc are off. Both load their JavaScript and CSS from a
        # CDN, which the demo network may not reach and which the
        # Content-Security-Policy would have to name -- and the policy naming no
        # external origin is a property worth more than a generated page. The
        # reference lives at /api, written by hand; see hub/apidocs.py for why.
        #
        # /openapi.json stays. It is same-origin JSON with no assets to fetch, so
        # it costs nothing and a client generator can still use it.
        docs_url=None,
        redoc_url=None,
    )

    static_dir = PACKAGE_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Browsers request /favicon.ico unprompted whatever the page declares, so
    # without this the log fills with 404s that are ours, not a visitor's. Served
    # as SVG under the .ico name deliberately: the extension is a convention from
    # a format nobody authors any more, and the response's content type is what
    # decides how it is parsed. Every browser that asks for this path also
    # renders SVG.
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")

    # Added before the routers so it wraps every response, including the ones
    # StaticFiles and the error handlers produce.
    app.add_middleware(SecurityHeadersMiddleware)

    # After the middleware, before the routers: a 404 for a path no router claims
    # is raised by Starlette itself, so the handler has to be installed on the app
    # rather than on any one router.
    register_error_handlers(app, templates)

    app.include_router(api_router)
    app.include_router(views_router)

    return app


def main() -> None:
    """Entry point for `python -m hub`."""
    import uvicorn

    config = HubConfig.from_environment()
    uvicorn.run(
        "hub.app:build",
        factory=True,
        host=config.host,
        port=config.port,
        reload=bool(os.environ.get("AETHEL_HUB_RELOAD")),
    )


def build() -> FastAPI:
    """Factory reference for uvicorn's `--factory` mode.

    The server is started through a factory rather than a module-level `app`
    object so that importing this module never opens a data directory. A test
    that wants an app calls `create_app(config)` with a temporary path, and
    nothing it does can touch a real Hub's store.

    Reads the `.env` again here rather than relying on `main()` having done it:
    under `--reload` uvicorn re-imports this module in a fresh worker process,
    which never ran `main()` at all.
    """
    return create_app(HubConfig.from_environment())
