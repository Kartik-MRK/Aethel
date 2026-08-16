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
from fastapi.staticfiles import StaticFiles

from hub.api import router as api_router
from hub.config import HubConfig
from hub.log import AnchorStore, TransparencyLog
from hub.storage import HubStorage
from hub.views import router as views_router

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
    )

    static_dir = PACKAGE_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(api_router)
    app.include_router(views_router)

    return app


def main() -> None:
    """Entry point for `python -m hub`."""
    import uvicorn

    config = HubConfig()
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
    """
    return create_app()
