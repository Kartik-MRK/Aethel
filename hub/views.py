"""Dashboard views — server-rendered HTML.

Server-rendered on purpose. The dashboard's job is to make provenance legible,
and a page whose values are already in the HTML can be read with View Source,
screenshotted, and printed. A client-side app would put a JavaScript bundle
between the reader and the data for no gain here.

Chart data is prepared in Python and handed to the template as plain numbers.
The template draws inline SVG — no chart library, so nothing to load from a CDN
(which the demo network may not reach) and no version to keep current.
"""

import math
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aethel.core.aggregator import hash_leaf, hash_node
from aethel.core.errors import AethelError
from hub.api import get_anchors, get_config, get_log, get_storage
from hub.config import HubConfig
from hub.log import AnchorStore, TransparencyLog
from hub.storage import HubStorage

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _is_metric(value: object) -> bool:
    """Whether a metrics-dict value is a number this dashboard can display.

    The `bool` exclusion is not pedantry: `bool` subclasses `int`, so a flag
    that found its way into the metrics dict would satisfy a plain numeric check
    and `float(True)` is `1.0` -- a perfect 100% accuracy, plotted on the chart
    and printed in the table, with nothing raising anywhere. The metrics dict is
    written by the trainer, so this side has to be the strict one.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        if _is_metric(value):
            return float(value)
    return None


def _f1_of(commit: dict) -> float | None:
    metrics = (commit.get("training_info") or {}).get("metrics") or {}
    for key in ("eval_f1_macro", "eval_macro_f1", "macro_f1", "eval_f1"):
        value = metrics.get(key)
        if _is_metric(value):
            return float(value)
    return None


def _shorten(value: str | None, size: int = 12) -> str:
    return (value or "")[:size]


def _repo_commits(storage: HubStorage, record) -> list[dict]:
    """Every distinct commit reachable from any branch of a repository.

    Shared by the index and the repository page so the two cannot disagree about
    how many versions a repository has. Counting each branch and taking the
    largest undercounts as soon as branches diverge: branches of 3 and 4 commits
    sharing 2 ancestors are 5 distinct versions, not 4 -- and one page saying 4
    while the other says 5 for the same repository is the kind of detail that
    costs a reader their trust in every other number on the site.

    A branch whose tip cannot be read is skipped rather than failing the page.
    The other branches are still legible, and a broken object store is /ops's
    story to tell, not a 500 on the landing page.
    """
    seen: set[str] = set()
    commits: list[dict] = []

    for tip in record.branches.values():
        try:
            history = storage.commit_history(tip, limit=200)
        except AethelError:
            continue

        for commit in history:
            if commit["hash"] not in seen:
                seen.add(commit["hash"])
                commits.append(commit)

    return commits


def _latest_accuracy(commits: list[dict]) -> float | None:
    """Accuracy of the newest *measured* commit, by timestamp.

    Newest measured rather than simply newest: an unmeasured commit on top must
    not blank the figure, because the most recent thing actually known is still
    worth showing. Ordered by timestamp because branch iteration order is not
    chronological -- reading whichever branch a dict happened to yield first
    would make the number depend on insertion order.
    """
    for commit in sorted(commits, key=lambda c: c.get("timestamp", ""), reverse=True):
        accuracy = _accuracy_of(commit)
        if accuracy is not None:
            return accuracy
    return None


def _proof_ladder(proof: dict | None) -> list[dict]:
    """Replay an inclusion proof one fold at a time, for display.

    The commit page shows the proof as a climb from leaf to root, and each rung
    needs the value the fold produces -- otherwise the reader is asked to trust
    that a list of sibling hashes adds up to the root, which is the exact thing
    the page exists to demonstrate.

    Deliberately built from `hash_leaf`/`hash_node`, the same two functions
    `verify_proof` uses. Re-implementing the fold here to draw it would let the
    picture and the verdict drift apart, and the picture is the more persuasive
    of the two -- so it must be the one that cannot lie.
    """
    if not proof:
        return []

    running = hash_leaf(proof["commit_hash"])
    rungs: list[dict] = []

    for index, step in enumerate(proof.get("proof") or [], start=1):
        sibling = step.get("sibling", "")
        side = step.get("side", "")

        if side == "left":
            folded = hash_node(sibling, running)
        elif side == "right":
            folded = hash_node(running, sibling)
        else:
            # A malformed step makes every later rung meaningless, so stop
            # rather than render an invented remainder.
            break

        rungs.append(
            {
                "index": index,
                "side": side,
                "sibling": sibling,
                "result": folded,
                "is_last": False,
            }
        )
        running = folded

    if rungs:
        rungs[-1]["is_last"] = True
    return rungs


#: Chart geometry. Fixed so the SVG viewBox is stable and the template stays
#: free of arithmetic. The container is sized to include the x-axis band --
#: fixing a height that excludes axis labels is what produces a card with its
#: own tiny scrollbar.
CHART = {
    "width": 860,
    "height": 240,
    "pad_left": 46,
    "pad_right": 26,
    "pad_top": 26,
    "pad_bottom": 34,
}

#: Y-axis gridline spacing, as a fraction. Bounds are snapped outward to a
#: multiple of this, so every axis label is a round number.
AXIS_STEP = 0.05

#: Minimum height of the y-domain. Without a floor, a repository whose commits
#: all score within a hair of each other would get an axis so tight that
#: measurement noise looked like a trend.
MIN_SPAN = 0.10


def _axis_bounds(values: list[float]) -> tuple[float, float]:
    """Pick the y-domain: snapped outward to AXIS_STEP, at least MIN_SPAN tall.

    This axis does not start at zero, and that is a deliberate, defensible
    choice rather than an oversight. A bar encodes magnitude in its length, so a
    bar chart must start at zero or the lengths lie. A line encodes *change* in
    its slope, and slope is unaffected by where the axis begins -- while a
    0-100%% domain flattens the entire history of a model into a horizontal
    stroke, hiding the one thing the chart exists to show.

    The two obligations that come with a non-zero baseline are met elsewhere:
    the axis labels its real bounds, and the template states the span in words
    directly above the plot. A truncated axis is only deceptive when it is
    silent about being truncated.
    """
    low = min(values)
    high = max(values)

    floor = math.floor(low / AXIS_STEP) * AXIS_STEP
    ceil = math.ceil(high / AXIS_STEP) * AXIS_STEP

    # A single point, or several identical ones, snap to the same gridline and
    # would divide by zero below.
    if ceil - floor < MIN_SPAN:
        middle = (floor + ceil) / 2
        floor = middle - MIN_SPAN / 2
        ceil = middle + MIN_SPAN / 2

    # Never claim more than the metric can hold.
    return max(floor, 0.0), min(ceil, 1.0)


def _chart_geometry(points: list[dict]) -> dict:
    """Scale accuracy points into SVG coordinates.

    Gridlines land on multiples of AXIS_STEP, so each one is a round percentage
    a reader can judge against. There is no filled area under the line: a fill
    reads as magnitude-from-the-baseline, which is exactly the reading a
    non-zero baseline does not support.
    """
    left = CHART["pad_left"]
    right = CHART["width"] - CHART["pad_right"]
    top = CHART["pad_top"]
    bottom = CHART["height"] - CHART["pad_bottom"]

    plot_width = right - left
    plot_height = bottom - top

    count = len(points)
    if count == 0:
        return {"points": [], "path": "", "grid": [], "left": left, "right": right,
                "top": top, "bottom": bottom, "low_pct": 0, "high_pct": 100}

    low, high = _axis_bounds([point["value"] for point in points])
    span = high - low

    def x_at(index: int) -> float:
        if count == 1:
            return left + plot_width / 2
        return left + (plot_width * index / (count - 1))

    def y_at(value: float) -> float:
        clamped = min(max((value - low) / span, 0.0), 1.0)
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

    # Walk the domain in AXIS_STEP increments. Integer counting avoids the
    # float drift that makes a "0.85" gridline label render as 84.99999%.
    steps = max(int(round(span / AXIS_STEP)), 1)
    grid = []
    for step in range(steps + 1):
        fraction = low + (high - low) * step / steps
        grid.append(
            {
                "value": fraction,
                "y": round(y_at(fraction), 2),
                "label": f"{fraction * 100:.0f}%",
            }
        )

    return {
        "points": placed,
        "path": path,
        "grid": grid,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "low_pct": round(low * 100),
        "high_pct": round(high * 100),
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
    """Rows for the history table, newest first, with parent links.

    Timestamps are cut to the minute. Seconds are recorded on the commit object
    and shown in full on the commit page; in a list they are three characters of
    noise in the widest column, and that width comes out of the message column,
    which is the one a reader actually reads.
    """
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
                "timestamp": (commit.get("timestamp") or "")[:16].replace("T", " "),
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
        commits = _repo_commits(storage, record)

        repos.append(
            {
                "name": record.name,
                "branch_count": len(record.branches),
                "branches": sorted(record.branches),
                "commit_count": len(commits),
                "latest_accuracy": _latest_accuracy(commits),
                "updated_at": (record.updated_at or "")[:16].replace("T", " "),
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

    commits = _repo_commits(storage, record)

    series = _build_series(commits)
    rows = _dag_rows(commits)

    accuracies = [p["value"] for p in series["points"]]
    best = max(accuracies) if accuracies else None

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
                "latest_accuracy": _latest_accuracy(commits),
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
            "ladder": _proof_ladder(proof),
            "leaf_hash": hash_leaf(proof["commit_hash"]) if proof else None,
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
