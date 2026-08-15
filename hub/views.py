"""Dashboard views — server-rendered HTML.

Server-rendered on purpose. The dashboard's job is to make provenance legible,
and a page whose values are already in the HTML can be read with View Source,
screenshotted, and printed. A client-side app would put a JavaScript bundle
between the reader and the data for no gain here.

Chart data is prepared in Python and handed to the template as plain numbers.
The template draws inline SVG — no chart library, so nothing to load from a CDN
(which the demo network may not reach) and no version to keep current.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aethel.core.errors import AethelError
from hub.api import get_anchors, get_config, get_log, get_storage
from hub.config import HubConfig
from hub.log import AnchorStore, TransparencyLog
from hub.storage import HubStorage

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _accuracy_of(commit: dict) -> float | None:
    """Pull an accuracy metric out of a commit's training info.

    Tolerates several key spellings because the metrics pipeline is still being
    built. Returns None when there is genuinely no number — the dashboard then
    shows an em dash rather than inventing a zero, which would read as "this
    model scored 0%" instead of "not measured".
    """
    metrics = (commit.get("training_info") or {}).get("metrics") or {}

    for key in ("eval_accuracy", "accuracy", "eval_acc"):
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _f1_of(commit: dict) -> float | None:
    metrics = (commit.get("training_info") or {}).get("metrics") or {}
    for key in ("eval_f1_macro", "eval_macro_f1", "macro_f1", "eval_f1"):
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _shorten(value: str | None, size: int = 12) -> str:
    return (value or "")[:size]


#: Chart geometry. Fixed so the SVG viewBox is stable and the template stays
#: free of arithmetic. The container is sized to include the x-axis band --
#: fixing a height that excludes axis labels is what produces a card with its
#: own tiny scrollbar.
CHART = {
    "width": 860,
    "height": 260,
    "pad_left": 46,
    "pad_right": 22,
    "pad_top": 18,
    "pad_bottom": 34,
}


def _chart_geometry(points: list[dict]) -> dict:
    """Scale accuracy points into SVG coordinates.

    The y-axis spans the full 0-100%%, not the data's own min-max. A truncated
    axis would exaggerate a two-point difference into a dramatic climb; for a
    proportion, the honest domain is the whole domain. Gridlines every 25%% give
    the reader a scale to judge against.
    """
    left = CHART["pad_left"]
    right = CHART["width"] - CHART["pad_right"]
    top = CHART["pad_top"]
    bottom = CHART["height"] - CHART["pad_bottom"]

    plot_width = right - left
    plot_height = bottom - top

    count = len(points)
    if count == 0:
        return {"points": [], "path": "", "area": "", "grid": [], "left": left,
                "right": right, "top": top, "bottom": bottom}

    def x_at(index: int) -> float:
        if count == 1:
            return left + plot_width / 2
        return left + (plot_width * index / (count - 1))

    def y_at(value: float) -> float:
        clamped = min(max(value, 0.0), 1.0)
        return bottom - (plot_height * clamped)

    placed = []
    for index, point in enumerate(points):
        placed.append(
            {
                **point,
                "px": round(x_at(index), 2),
                "py": round(y_at(point["value"]), 2),
                "pct": round(point["value"] * 100, 1),
            }
        )

    path = " ".join(
        f"{'M' if i == 0 else 'L'}{p['px']},{p['py']}" for i, p in enumerate(placed)
    )

    area = (
        f"M{placed[0]['px']},{bottom} "
        + " ".join(f"L{p['px']},{p['py']}" for p in placed)
        + f" L{placed[-1]['px']},{bottom} Z"
    )

    grid = [
        {"value": fraction, "y": round(y_at(fraction), 2), "label": f"{int(fraction * 100)}%"}
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]

    return {
        "points": placed,
        "path": path,
        "area": area,
        "grid": grid,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
    }


def _build_series(commits: list[dict]) -> dict:
    """Turn commits into a chronological accuracy series for the chart.

    Oldest-first, because the chart's job is "did quality improve over the
    project's life" and time must run left to right.

    Commits without a metric are skipped rather than plotted as zero. A gap in
    a line is honest; a dive to zero is a lie.
    """
    ordered = sorted(commits, key=lambda c: c.get("timestamp", ""))

    points = []
    for index, commit in enumerate(ordered):
        accuracy = _accuracy_of(commit)
        if accuracy is None:
            continue
        points.append(
            {
                "x": index,
                "value": accuracy,
                "hash": _shorten(commit.get("hash")),
                "message": commit.get("message", ""),
                "timestamp": (commit.get("timestamp") or "")[:19].replace("T", " "),
            }
        )

    return {
        "points": points,
        "geometry": _chart_geometry(points),
        "chart": CHART,
        "total_commits": len(ordered),
        "measured": len(points),
        "unmeasured": len(ordered) - len(points),
    }


def _dag_rows(commits: list[dict]) -> list[dict]:
    """Rows for the history table, newest first, with parent links."""
    ordered = sorted(commits, key=lambda c: c.get("timestamp", ""), reverse=True)

    rows = []
    for commit in ordered:
        rows.append(
            {
                "hash": commit.get("hash", ""),
                "short": _shorten(commit.get("hash")),
                "parent_short": _shorten(commit.get("parent_hash")) or None,
                "message": commit.get("message", ""),
                "author": commit.get("author", "—"),
                "timestamp": (commit.get("timestamp") or "")[:19].replace("T", " "),
                "accuracy": _accuracy_of(commit),
                "f1": _f1_of(commit),
                "adapter_blob": _shorten(commit.get("adapter_blob")),
                "is_root": not commit.get("parent_hash"),
            }
        )
    return rows


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    storage: Annotated[HubStorage, Depends(get_storage)],
    log: Annotated[TransparencyLog, Depends(get_log)],
) -> HTMLResponse:
    """Landing page: every repository the Hub knows about."""
    repos = []
    for record in storage.repos().values():
        commit_count = 0
        latest_accuracy = None

        for tip in record.branches.values():
            try:
                history = storage.commit_history(tip, limit=200)
            except AethelError:
                continue
            commit_count = max(commit_count, len(history))
            if history and latest_accuracy is None:
                latest_accuracy = _accuracy_of(history[0])

        repos.append(
            {
                "name": record.name,
                "branch_count": len(record.branches),
                "branches": sorted(record.branches),
                "commit_count": commit_count,
                "latest_accuracy": latest_accuracy,
                "updated_at": (record.updated_at or "")[:19].replace("T", " "),
            }
        )

    counts = storage.object_counts()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "repos": sorted(repos, key=lambda r: r["name"]),
            "counts": counts,
            "log_size": log.size(),
            "log_root": log.root(),
        },
    )


@router.get("/r/{repo_name}", response_class=HTMLResponse)
async def repo_detail(
    repo_name: str,
    request: Request,
    storage: Annotated[HubStorage, Depends(get_storage)],
    log: Annotated[TransparencyLog, Depends(get_log)],
) -> HTMLResponse:
    """One repository: stat tiles, accuracy trend, and the commit history."""
    record = storage.repo(repo_name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No repository '{repo_name}'.")

    seen: set[str] = set()
    commits: list[dict] = []

    for tip in record.branches.values():
        try:
            for commit in storage.commit_history(tip, limit=200):
                if commit["hash"] not in seen:
                    seen.add(commit["hash"])
                    commits.append(commit)
        except AethelError:
            continue

    series = _build_series(commits)
    rows = _dag_rows(commits)

    accuracies = [p["value"] for p in series["points"]]
    best = max(accuracies) if accuracies else None
    latest = accuracies[-1] if accuracies else None

    # Distinct base models across the history: the "one frozen base, many
    # patches" claim, as a number.
    bases = {commit.get("base") for commit in commits if commit.get("base")}

    logged = {entry.commit_hash for entry in log.entries()}

    return templates.TemplateResponse(
        request=request,
        name="repo.html",
        context={
            "repo": record.to_dict(),
            "commits": rows,
            "series": series,
            "stats": {
                "versions": len(commits),
                "branches": len(record.branches),
                "bases": len(bases),
                "latest_accuracy": latest,
                "best_accuracy": best,
            },
            "logged": logged,
            "log_size": log.size(),
            "log_root": log.root(),
        },
    )


@router.get("/c/{commit_hash}", response_class=HTMLResponse)
async def commit_detail(
    commit_hash: str,
    request: Request,
    storage: Annotated[HubStorage, Depends(get_storage)],
    log: Annotated[TransparencyLog, Depends(get_log)],
    anchors: Annotated[AnchorStore, Depends(get_anchors)],
) -> HTMLResponse:
    """One commit: metadata, files, base reference, and its inclusion proof."""
    try:
        commit = storage.read_commit(commit_hash)
    except AethelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    files: dict[str, str] = {}
    if commit.get("tree"):
        try:
            files = storage.read_tree(commit["tree"])
        except AethelError:
            files = {}

    base = None
    if commit.get("base"):
        try:
            base = storage.read_base(commit["base"])
        except AethelError:
            base = None

    proof = log.inclusion_proof(commit_hash)
    latest_anchor = anchors.latest()

    # Verify the proof here so the page states a checked result rather than
    # displaying an unverified claim. The authoritative check is still the
    # client-side one against the on-chain root.
    proof_valid = None
    if proof:
        proof_valid = TransparencyLog.verify(
            proof["commit_hash"],
            proof["proof"],
            proof["root"],
        )

    return templates.TemplateResponse(
        request=request,
        name="commit.html",
        context={
            "commit_hash": commit_hash.lower(),
            "commit": commit,
            "files": files,
            "base": base,
            "accuracy": _accuracy_of(commit),
            "f1": _f1_of(commit),
            "proof": proof,
            "proof_valid": proof_valid,
            "anchor": latest_anchor,
        },
    )


@router.get("/ops", response_class=HTMLResponse)
async def ops(
    request: Request,
    config: Annotated[HubConfig, Depends(get_config)],
    storage: Annotated[HubStorage, Depends(get_storage)],
    log: Annotated[TransparencyLog, Depends(get_log)],
    anchors: Annotated[AnchorStore, Depends(get_anchors)],
) -> HTMLResponse:
    """Operations board — which subsystems are healthy, and why.

    Reuses the /api/v1/health logic rather than duplicating it, so the page and
    the endpoint can never disagree about the state of the system.
    """
    from hub.api import health

    payload = await health(request, config, storage, log, anchors)
    import json

    data = json.loads(bytes(payload.body).decode("utf-8"))

    return templates.TemplateResponse(
        request=request,
        name="ops.html",
        context={
            "overall": data["status"],
            "checks": data["checks"],
            "counts": data.get("counts", {}),
            "version": {
                "hub": request.app.state.hub_version,
                "git_sha": request.app.state.git_sha,
                "started_at": request.app.state.started_at[:19].replace("T", " "),
                "data_dir": str(config.data_dir),
            },
            "log_size": log.size(),
            "log_root": log.root(),
        },
    )
