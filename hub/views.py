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
from hub import apidocs, prose
from hub.api import get_anchors, get_config, get_log, get_storage
from hub.config import HubConfig
from hub.log import AnchorStore, TransparencyLog
from hub.storage import HubStorage

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

# The API reference is written as prose in hub/apidocs.py, which needs code
# spans and paragraph breaks that plain strings cannot carry. Registered as
# filters rather than applied in the view so the template decides where a run of
# text is one line and where it is several -- that is a layout question.
templates.env.filters["inline"] = prose.inline
templates.env.filters["prose"] = prose.prose


def _is_metric(value: object) -> bool:
    """Whether a metrics-dict value is a number this dashboard can display.

    The `bool` exclusion is not pedantry: `bool` subclasses `int`, so a flag
    that found its way into the metrics dict would satisfy a plain numeric check
    and `float(True)` is `1.0` -- a perfect 100% accuracy, plotted on the chart
    and printed in the table, with nothing raising anywhere. The metrics dict is
    written by the trainer, so this side has to be the strict one.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mapping(value: object) -> dict:
    """`value` if it is a dict, otherwise an empty one.

    Commit objects arrive from a client over HTTP. A malformed one should cost a
    number on a page, not a 500 on it, so every step down into the metadata
    checks rather than assumes.
    """
    return value if isinstance(value, dict) else {}


def _metric_sources(commit: dict) -> tuple[dict, dict]:
    """The two places a commit can carry numbers, in preference order.

    `training_info["metrics"]` is what the trainer writes at the end of a run —
    loss and runtime, and whatever else the trainer happened to compute.
    `training_info["evaluation"]["current"]` is what the commit-time evaluator
    writes after scoring the adapter itself, and it wins because it is a
    measurement of the finished artifact rather than a by-product of fitting it.
    It is not yet a held-out score: the evaluator reads the dataset recorded at
    training time, so today it reports accuracy on seen data, and the
    current-versus-parent comparison is the part that carries real signal. Both
    shapes are in the wild, so both are read, and neither side has to know about
    the other.
    """
    info = _mapping(commit.get("training_info"))
    evaluated = _mapping(_mapping(info.get("evaluation")).get("current"))
    return evaluated, _mapping(info.get("metrics"))


def _first_metric(commit: dict, keys: tuple[str, ...]) -> float | None:
    """First numeric value under any of `keys`, evaluation results winning."""
    for source in _metric_sources(commit):
        for key in keys:
            value = source.get(key)
            if _is_metric(value):
                return float(value)
    return None


def _accuracy_of(commit: dict) -> float | None:
    """Pull an accuracy metric out of a commit's training info.

    Tolerates several key spellings because two independent pieces of the
    metrics pipeline write them: the trainer emits HuggingFace's `eval_`
    prefixes, the commit-time evaluator emits a bare `accuracy`. Returns None
    when there is genuinely no number — the dashboard then shows an em dash
    rather than inventing a zero, which would read as "this model scored 0%"
    instead of "not measured".
    """
    return _first_metric(commit, ("eval_accuracy", "accuracy", "eval_acc"))


def _f1_of(commit: dict) -> float | None:
    return _first_metric(commit, ("eval_f1_macro", "eval_macro_f1", "macro_f1", "eval_f1"))


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


#: Width of one character of the label face, in viewBox units at its 11px size.
#: Measured in the browser with `getComputedTextLength`, not guessed: labels of
#: 10, 11, 15 and 27 characters all came back at 6.593 units per character.
#:
#: One number is enough because the face is monospaced -- every glyph advances by
#: the same amount, so a character count *is* a width. Rounded up rather than
#: down, and to the tenth rather than the thousandth, because a machine without
#: the self-hosted face falls back to its own monospace, which may advance a
#: little differently. Over-reserving costs a few units of plot; under-reserving
#: clips a label at the edge of the card.
LABEL_ADVANCE = 6.6

#: Gap between a marker and the label naming it. Wide enough to clear the 4-unit
#: marker radius and its 2-unit surface ring with daylight left over.
LABEL_GAP = 10

#: Ceiling on the label gutter, as a fraction of the plot. A branch called
#: `experiment/quantised-int4-rerun` is 32 characters and would otherwise claim
#: over 200 units, squeezing the line it labels into the left third of the card.
#: Past this the label overhangs the plot instead -- worse than clipping a label
#: is losing the shape the label is pointing at.
MAX_GUTTER_FRACTION = 0.28


def _chart_geometry(points: list[dict], columns: int, gutter: float = 0) -> dict:
    """Scale accuracy points into SVG coordinates.

    `columns` is how many x slots the history occupies -- the total number of
    commits, measured or not. Each point carries its own `x`, its rank in that
    history, so a commit lands at its real position rather than at its index in
    whatever subset was measured. Two branches then leave their shared ancestor
    from the same spot, and a run of unmeasured commits shows up as a wider gap
    instead of silently closing up.

    `gutter` is room held back on the right for the labels that name each line's
    endpoint. Direct labels are only worth having if they are legible: without
    the gutter the last commit sits hard against the plot edge and its label runs
    off the card, which is how "main 90.7%" renders as "main".

    Gridlines land on multiples of AXIS_STEP, so each one is a round percentage
    a reader can judge against. There is no filled area under the line: a fill
    reads as magnitude-from-the-baseline, which is exactly the reading a
    non-zero baseline does not support.
    """
    left = CHART["pad_left"]
    right = CHART["width"] - CHART["pad_right"]
    top = CHART["pad_top"]
    bottom = CHART["height"] - CHART["pad_bottom"]

    if not points:
        return {"points": [], "grid": [], "left": left, "right": right,
                "top": top, "bottom": bottom, "low_pct": 0, "high_pct": 100}

    right -= min(gutter, (right - left) * MAX_GUTTER_FRACTION)
    plot_width = right - left
    plot_height = bottom - top

    low, high = _axis_bounds([point["value"] for point in points])
    span = high - low

    def x_at(column: int) -> float:
        if columns <= 1:
            return left + plot_width / 2
        return left + (plot_width * column / (columns - 1))

    def y_at(value: float) -> float:
        clamped = min(max((value - low) / span, 0.0), 1.0)
        return bottom - (plot_height * clamped)

    placed = []
    for point in points:
        px = round(x_at(point["x"]), 2)
        # Which side of the point its hover readout is drawn on. Past the
        # two-thirds mark there is no room to the right, so the text runs back
        # over the plot instead of off the edge of the card.
        #
        # `readout_`, not `label_`: a point also sits at the end of a line, and
        # that line has label coordinates of its own with a different rule behind
        # them. Two keys called `label_x` on two objects one is reachable from is
        # the kind of thing a template picks up the wrong one of.
        flipped = px > left + plot_width * 0.66
        placed.append(
            {
                **point,
                "px": px,
                "py": round(y_at(point["value"]), 2),
                "pct": round(point["value"] * 100, 1),
                "readout_anchor": "end" if flipped else "start",
                "readout_x": round(px + (-8 if flipped else 8), 2),
            }
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
        "grid": grid,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "low_pct": round(low * 100),
        "high_pct": round(high * 100),
    }


#: How many hues the chart cycles through for its branch lines. Three, because
#: that is how many series tokens the palette defines -- and a fourth line has
#: to reuse a hue rather than invent one that has not been contrast-checked.
#: Reuse is safe here because the hue is not what identifies a line: every line
#: is labelled with its branch name at its own endpoint.
SERIES_HUES = 3


def _branch_lines(commits: list[dict], branches: dict[str, str]) -> list[dict]:
    """Split a history into one polyline per branch, oldest commit first.

    A single line through every commit in date order is wrong the moment a
    repository has two branches. On the demo history it runs from a 4-bit probe
    at 86.4% straight up to a dropout experiment at 90.7% -- a four-point climb
    that nobody made, because those two commits are siblings, not a before and
    after. A line segment claims one value *became* the other, so every segment
    has to lie along one line of work.

    Each commit is drawn once. Where two branches share an ancestor, the earlier
    branch in the ordering owns that stretch and the later one picks the line up
    again at the fork point, so a reader sees two lines parting at the commit
    they have in common -- the same shape the rail draws two sections down.

    Branch ordering decides only which line owns a shared commit, never whether
    a segment is real: the commit is drawn at the same place with the same value
    either way. `main` first because that is where a trunk lives by convention,
    then alphabetically so the colours do not move between page loads.
    """
    by_hash = {c["hash"]: c for c in commits if c.get("hash")}

    # Oldest first, and topological rather than by date. This is the property
    # that lets a branch be drawn as a polyline at all: a parent always precedes
    # its child, so x only ever increases along a branch. Sorting by timestamp
    # cannot promise that -- two machines with unsynchronised clocks are enough
    # to stamp a parent after its child, and the line would double back.
    history = list(reversed(_rail_order(commits)))
    column_of = {commit["hash"]: index for index, commit in enumerate(history)}

    def ancestry(tip: str) -> list[str]:
        """A branch's commits, oldest first, within the visible set."""
        walk = []
        current = tip
        while current in by_hash and current not in walk:
            walk.append(current)
            current = by_hash[current].get("parent_hash")
        return list(reversed(walk))

    ordered_names = sorted(branches, key=lambda name: (name != "main", name))

    claimed: set[str] = set()
    lines = []

    for name in ordered_names:
        chain = ancestry(branches[name])
        mine = [h for h in chain if h not in claimed]
        if not mine:
            # Every commit on this branch is already drawn -- the branch points
            # into another's history rather than adding to it. Its tip is still
            # named in the table's chips; drawing a second line over the first
            # would only thicken the stroke.
            continue

        # Reach back one commit so the line starts at the fork it came from
        # rather than floating detached in the middle of the plot. That commit
        # belongs to the line that already drew it, so it is not listed as owned
        # here -- `owned` is what decides who draws the marker and the hover
        # target, and a point on two paths must still be one readable value.
        first = chain.index(mine[0])
        drawn = chain[first - 1 :] if first else mine

        points = []
        for commit_hash in drawn:
            accuracy = _accuracy_of(by_hash[commit_hash])
            if accuracy is None:
                continue
            record = by_hash[commit_hash]
            points.append(
                {
                    "x": column_of[commit_hash],
                    "value": accuracy,
                    "hash": _shorten(commit_hash),
                    "message": record.get("message", ""),
                    "timestamp": (record.get("timestamp") or "")[:19].replace("T", " "),
                    "branch": name,
                }
            )

        owned = {column_of[h] for h in mine}
        claimed.update(mine)
        if points:
            lines.append({"branch": name, "points": points, "owned": owned})

    return lines


#: Minimum vertical distance between two endpoint labels, in viewBox units. The
#: face is 11 units tall, so this is the text's own height plus enough daylight
#: that two adjacent labels read as two labels.
LABEL_PITCH = 14

#: How far a label's glyphs reach below its baseline, plus a unit of margin. Used
#: to keep a nudged label's descenders inside the card rather than shaved off by
#: the viewBox edge.
LABEL_DESCENT = 4


def _spread_labels(lines: list[dict]) -> None:
    """Nudge endpoint labels apart when two branches finish at similar scores.

    Two branches forking from one commit and landing a tenth of a point apart is
    not a contrived case -- it is what a hyperparameter sweep looks like. Their
    labels would then print within a unit of each other, and the result is not
    two overlapping labels but one illegible smear where a reader cannot tell
    which name goes with which number.

    Only labels that actually overlap horizontally are moved: two lines ending at
    opposite ends of the plot can sit at the same height without interfering, and
    moving one of those off its own endpoint would be a lie about where its line
    finished. Adjustment is downward from the top so the order of the labels
    still matches the order of the lines.

    A nudged label does end up sitting a little off its own endpoint's level,
    which is a real cost -- the eye travels a short diagonal instead of straight
    out. It is the smaller cost. The alternative is two names printed through each
    other, where a reader cannot recover either one; here the marker is still
    within a line's height of the text, and the hue and dash pattern say which
    stroke it belongs to.

    The clamp is against the bottom of the *card*, not the bottom of the plot:
    nothing is drawn in the bottom margin -- the x axis is the commit table, not
    a band of tick labels -- so a nudged label is perfectly legible there, and
    refusing to use it would mean giving up the room for no reader's benefit.
    Enough labels piled into the last few units do stop spreading and start
    stacking; that needs four branches finishing within a point of each other at
    the very floor of the axis, and a clamped overlap is still better than a
    label pushed off the card entirely.
    """
    floor_limit = CHART["height"] - LABEL_DESCENT
    ordered = sorted(lines, key=lambda line: line["label_y"])
    for index, line in enumerate(ordered[1:], start=1):
        above = ordered[index - 1]
        overlaps = line["label_x"] < above["label_x"] + len(above["label"]) * LABEL_ADVANCE
        if not overlaps:
            continue
        floor = above["label_y"] + LABEL_PITCH
        if line["label_y"] < floor:
            line["label_y"] = round(min(floor, floor_limit), 2)


def _build_series(commits: list[dict], branches: dict[str, str] | None = None) -> dict:
    """Turn commits into per-branch accuracy lines for the chart.

    Oldest-first, because the chart's job is "did quality improve over the
    project's life" and time must run left to right.

    Commits without a metric are skipped rather than plotted as zero. A gap in
    a line is honest; a dive to zero is a lie.

    With no branches given -- or one -- this is a single line, exactly as it was
    before there was anything to split.
    """
    ordered = sorted(commits, key=lambda c: c.get("timestamp", ""))

    if not branches:
        # No refs to follow, so the whole history is one line. Reachable from a
        # store whose refs failed to read, where a chart is still better than a
        # blank card.
        lines = [{"branch": "", "points": [], "owned": set()}]
        for index, commit in enumerate(ordered):
            accuracy = _accuracy_of(commit)
            if accuracy is None:
                continue
            lines[0]["points"].append(
                {
                    "x": index,
                    "value": accuracy,
                    "hash": _shorten(commit.get("hash")),
                    "message": commit.get("message", ""),
                    "timestamp": (commit.get("timestamp") or "")[:19].replace("T", " "),
                    "branch": "",
                }
            )
            lines[0]["owned"].add(index)
    else:
        lines = _branch_lines(commits, branches)

    flat = [point for line in lines for point in line["points"]]

    # One line needs no name on it: the page has already said the repository has
    # a single branch, and a label repeating it is noise on the one value a
    # reader came to read. Two or more, and the name is the only thing saying
    # which line is which -- the hue must never be carrying that alone.
    named = len(lines) > 1

    def label_of(line: dict) -> str:
        pct = round(line["points"][-1]["value"] * 100, 1)
        return f"{line['branch']} {pct}%" if named else f"{pct}%"

    # The gutter has to be sized before the plot is, because it is what the plot
    # gives up. Every label is measured, not just the last one drawn: `main` may
    # end at the far right while a longer `wide-rank` ends short of it, and it is
    # the widest label that decides whether any of them fit.
    labels = {id(line): label_of(line) for line in lines if line["points"]}
    gutter = (max((len(text) for text in labels.values()), default=0) * LABEL_ADVANCE
              + LABEL_GAP) if labels else 0
    geometry = _chart_geometry(flat, max(len(ordered), 1), gutter)

    placed = {(point["branch"], point["x"]): point for point in geometry["points"]}
    drawn = []
    marks = []
    for index, line in enumerate(lines):
        points = [placed[(line["branch"], p["x"])] for p in line["points"]]
        if not points:
            continue
        hue = index % SERIES_HUES + 1
        end = points[-1]
        drawn.append(
            {
                "branch": line["branch"],
                "hue": hue,
                "points": points,
                "path": " ".join(
                    f"{'M' if i == 0 else 'L'}{p['px']},{p['py']}" for i, p in enumerate(points)
                ),
                "label": labels[id(line)],
                "end": end,
                # Outward from the endpoint, level with it. Above-and-left was the
                # obvious placement and it is the wrong one: a descending line
                # runs straight through the text, and a reader ends up parsing
                # "probe 86.4%" with a dashed orange stroke through the middle of
                # it. Nothing is drawn past a line's own endpoint, so the space to
                # its right is the one place a label is guaranteed clear of it.
                "label_x": round(end["px"] + LABEL_GAP, 2),
                # +4 sets the baseline so the text's cap height straddles the
                # marker rather than sitting on top of it.
                "label_y": round(end["py"] + 4, 2),
            }
        )
        # A branch's line reaches back one commit into the fork it left, so that
        # shared commit is a point on two paths. Its marker and its hover column
        # belong to whichever line owns it -- drawn twice, a keyboard user would
        # tab through the same value on the way past, and the two 2px surface
        # rings would stack into a visibly heavier dot than its neighbours.
        marks.extend(
            {**point, "hue": hue, "branch_label": line["branch"] if named else ""}
            for point in points
            if point["x"] in line["owned"]
        )

    _spread_labels(drawn)
    marks.sort(key=lambda point: point["x"])

    return {
        "lines": drawn,
        "marks": marks,
        "points": geometry["points"],
        "geometry": geometry,
        "chart": CHART,
        "total_commits": len(ordered),
        "measured": len(marks),
        "unmeasured": len(ordered) - len(marks),
    }


#: Rail geometry, in the SVG's own units. The cell is exactly as wide as the rail,
#: so one unit is one CSS pixel horizontally; vertically the viewBox is stretched
#: to whatever height the row turns out to be (see the template).
#:
#: The inset is wider than the tail on purpose. It matches the table's own cell
#: padding plus the marker's radius, so the leftmost lane clears the panel border
#: instead of sitting on it; the tail only has to clear the rightmost marker.
LANE_PITCH = 14
RAIL_INSET = 20
RAIL_TAIL = 14
RAIL_HEIGHT = 20

#: Lanes past this share the rightmost column rather than widening the rail
#: without limit. Six concurrent lines of work is already past the point where a
#: graph this small is readable, and the Parent column carries the exact ancestry
#: regardless -- so the failure mode is a crowded drawing, never a wrong one.
MAX_LANES = 6


def _rail_order(commits: list[dict]) -> list[dict]:
    """Newest first, but never a parent above its own child.

    Plain timestamp order is not safe to draw. The rail assigns each commit a
    lane and hands the lane down to its parent, which only works if the parent
    comes later in the list. Timestamps come from whichever machine made the
    commit, so two laptops with a few seconds of clock skew are enough to put a
    parent above its child -- and the drawing would then run a lane off the
    bottom of the table toward a commit that was already passed.

    So: repeatedly take the newest commit whose children have all been placed.
    This is what `git log --topo-order` does, and on a history with no forks it
    is indistinguishable from sorting by date.
    """
    by_hash = {c["hash"]: c for c in commits if c.get("hash")}

    children_left = dict.fromkeys(by_hash, 0)
    for commit in by_hash.values():
        parent = commit.get("parent_hash")
        if parent in children_left:
            children_left[parent] += 1

    def newest_last(commit_hash: str) -> tuple[str, str]:
        # The hash breaks ties so that two commits sharing a timestamp -- which
        # the demo seed produces deliberately -- draw the same way every load.
        return (by_hash[commit_hash].get("timestamp") or "", commit_hash)

    ready = [h for h, remaining in children_left.items() if remaining == 0]
    ordered: list[dict] = []

    while ready:
        ready.sort(key=newest_last)
        current = ready.pop()
        ordered.append(by_hash[current])

        parent = by_hash[current].get("parent_hash")
        if parent in children_left:
            children_left[parent] -= 1
            if children_left[parent] == 0:
                ready.append(parent)

    if len(ordered) < len(by_hash):
        # Only reachable through a cycle, which content addressing makes
        # effectively impossible -- a commit would have to contain its own hash.
        # A hand-edited store could still fake one, and dropping rows silently is
        # the worst possible response: the page would under-report the history
        # while looking perfectly healthy.
        placed = {c["hash"] for c in ordered}
        ordered.extend(
            sorted(
                (c for h, c in by_hash.items() if h not in placed),
                key=lambda c: (c.get("timestamp") or "", c["hash"]),
                reverse=True,
            )
        )

    return ordered


def _lane_x(index: int) -> int:
    return RAIL_INSET + min(index, MAX_LANES - 1) * LANE_PITCH


def _assign_lanes(ordered: list[dict]) -> dict:
    """Turn a topologically ordered history into per-row rail geometry.

    One pass down the list, carrying a list of open lanes. A lane holds the hash
    it is currently descending toward: a child claims a lane, then hands it to
    its parent, so a line on the page is one line of work.

    Three things happen at a row:

    * every lane already pointing at this commit arrives here -- more than one
      means this commit is a fork point, and the extra lanes close;
    * the commit takes the leftmost arriving lane, or a free one if it is a
      branch tip that nothing has pointed at yet;
    * its parent takes the same lane, unless the parent already has one, in
      which case this line curves into that one and its lane closes.

    Every other open lane passes straight through, which is what makes a fork's
    two sides readable as continuous while unrelated rows sit between them.
    """
    known = {c["hash"] for c in ordered if c.get("hash")}
    lanes: list[str | None] = []
    rows = []

    for commit in ordered:
        commit_hash = commit.get("hash", "")

        arriving = [i for i, held in enumerate(lanes) if held == commit_hash]
        if arriving:
            lane = arriving[0]
        else:
            lane = next((i for i, held in enumerate(lanes) if held is None), len(lanes))
            if lane == len(lanes):
                lanes.append(None)

        before = list(lanes)
        for index in arriving:
            lanes[index] = None

        parent = commit.get("parent_hash")
        # A parent outside the visible set is not drawn as a continuing line.
        # That happens when the history is longer than the page's limit, and a
        # lane running to the bottom edge would claim there is another row.
        target = None
        if parent and parent in known:
            existing = next((i for i, held in enumerate(lanes) if held == parent), None)
            target = lane if existing is None else existing
            lanes[target] = parent

        rows.append(
            {
                "lane": lane,
                "segments": _rail_segments(
                    before=before,
                    after=list(lanes),
                    lane=lane,
                    arriving=arriving,
                    target=target,
                    truncated=bool(parent) and parent not in known,
                ),
            }
        )

    return {"rows": rows, "lanes": len(lanes)}


def _rail_segments(
    *,
    before: list[str | None],
    after: list[str | None],
    lane: int,
    arriving: list[int],
    target: int | None,
    truncated: bool,
) -> list[dict]:
    """SVG path data for one row of the rail.

    Curves are cubics that leave and arrive vertically, so a line crossing lanes
    still reads as one continuous stroke where it meets its neighbours' straight
    segments. A quadratic or a plain diagonal would meet them at an angle and
    the joins would look like breaks.

    The node itself is deliberately absent: the row's height is unknown here, so
    the viewBox is stretched vertically by the template and a circle drawn in it
    would come out an ellipse. The marker is an HTML element positioned over the
    cell instead, which keeps its shape at any row height.
    """
    mid = RAIL_HEIGHT / 2
    x = _lane_x(lane)
    segments = []

    for index, held in enumerate(before):
        if held is None or index == lane or index in arriving:
            continue
        if after[index] is not None:
            segments.append({"cls": "rail-line", "d": f"M{_lane_x(index)} 0V{RAIL_HEIGHT}"})

    for index in arriving:
        if index == lane:
            segments.append({"cls": "rail-line", "d": f"M{x} 0V{mid:g}"})
        else:
            start = _lane_x(index)
            segments.append(
                {
                    "cls": "rail-line",
                    "d": f"M{start} 0C{start} {mid / 2:g} {x} {mid / 2:g} {x} {mid:g}",
                }
            )

    if target is not None:
        end = _lane_x(target)
        if end == x:
            segments.append({"cls": "rail-line", "d": f"M{x} {mid:g}V{RAIL_HEIGHT}"})
        else:
            handle = mid + (RAIL_HEIGHT - mid) / 2
            segments.append(
                {
                    "cls": "rail-line",
                    "d": f"M{x} {mid:g}C{x} {handle:g} {end} {handle:g} {end} {RAIL_HEIGHT}",
                }
            )
    elif truncated:
        # Stops short of the row's edge, and dimmer: the parent exists, this page
        # just does not go back that far.
        segments.append({"cls": "rail-line cut", "d": f"M{x} {mid:g}V{RAIL_HEIGHT - 4}"})

    return segments


def _dag_rows(commits: list[dict], branches: dict[str, str] | None = None) -> list[dict]:
    """Rows for the history table, newest first, with lane geometry for the rail.

    Timestamps are cut to the minute. Seconds are recorded on the commit object
    and shown in full on the commit page; in a list they are three characters of
    noise in the widest column, and that width comes out of the message column,
    which is the one a reader actually reads.

    `branches` maps a branch name to its tip hash, so a row can say which
    branches point at it. Without that the table cannot answer "where is
    `wide-rank`?" -- the graph shows three lines of work and nothing names them.
    """
    ordered = _rail_order(commits)
    rail = _assign_lanes(ordered)

    tips: dict[str, list[str]] = {}
    for name, tip in (branches or {}).items():
        tips.setdefault(tip, []).append(name)

    rows = []
    # strict: one row of geometry per commit. A length mismatch would silently
    # shift every lane onto the wrong commit, which draws a clean picture of a
    # history that never happened -- the one failure this table must not have.
    for commit, track in zip(ordered, rail["rows"], strict=True):
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
                "refs": sorted(tips.get(commit.get("hash", ""), [])),
                "lane": track["lane"],
                "segments": track["segments"],
            }
        )

    return rows


def _rail_lanes(rows: list[dict]) -> int:
    """How many lanes the rail column has to reserve width for.

    The widest node lane is the answer. A lane only ever comes into existence at
    a commit's own row -- a parent inherits its child's lane or an existing one,
    never a new one -- so no lane can pass through a row without some row further
    down owning it.
    """
    return min(max((row["lane"] for row in rows), default=0) + 1, MAX_LANES)


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

    series = _build_series(commits, record.branches)
    rows = _dag_rows(commits, record.branches)

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
            "rail": {
                "lanes": _rail_lanes(rows),
                "height": RAIL_HEIGHT,
                "width": _lane_x(_rail_lanes(rows) - 1) + RAIL_TAIL,
            },
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


@router.get("/api", response_class=HTMLResponse)
async def api_reference(request: Request) -> HTMLResponse:
    """The HTTP API reference.

    Static: it reads no repository state, because a protocol description does not
    depend on what happens to be published. Served from the app anyway rather
    than written as a markdown file, so a client author reading it is looking at
    the same version of the API they are about to call.
    """
    return templates.TemplateResponse(
        request=request,
        name="api.html",
        context={
            "conventions": apidocs.CONVENTIONS,
            "push_sequence": apidocs.PUSH_SEQUENCE,
            "groups": apidocs.GROUPS,
            "shared_errors": apidocs.SHARED_ERRORS,
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
