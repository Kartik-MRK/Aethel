"""Tests for the dashboard's data preparation.

The views split cleanly into two halves: pure functions that turn commit records
into display data, and route handlers that assemble a template context. The pure
half is what these tests cover, because it is where the page can be wrong while
still rendering perfectly -- a mislabelled axis, a proof rung showing the wrong
fold, a metric read from the wrong key. None of that raises; it just displays a
falsehood, and a screenshot cannot tell you the number is wrong.

`_proof_ladder` gets the most attention. It draws the inclusion proof, which is
the one thing on the site a reader is asked to check rather than believe, so a
ladder that disagreed with `verify_proof` would undermine the exact claim the
page exists to make.
"""

import pytest

from aethel.core.aggregator import MerkleTree, hash_leaf, hash_node
from aethel.core.errors import AethelError
from hub.log import TransparencyLog
from hub.views import (
    CHART,
    LABEL_ADVANCE,
    LABEL_PITCH,
    MAX_GUTTER_FRACTION,
    MAX_LANES,
    RAIL_HEIGHT,
    _accuracy_of,
    _assign_lanes,
    _axis_bounds,
    _build_series,
    _chart_geometry,
    _dag_rows,
    _f1_of,
    _lane_x,
    _latest_accuracy,
    _proof_ladder,
    _rail_lanes,
    _rail_order,
    _repo_commits,
    _shorten,
)

AXIS_STEP = 0.05


def commit(**fields) -> dict:
    """A commit record with only the keys the views actually read."""
    record = {
        "hash": "a" * 64,
        "parent_hash": None,
        "message": "commit",
        "author": "tester",
        "timestamp": "2026-01-01T00:00:00",
    }
    record.update(fields)
    return record


def with_metrics(**metrics) -> dict:
    return {"training_info": {"metrics": metrics}}


class TestMetricExtraction:
    """The metrics dict is written by the trainer, so key spelling varies."""

    @pytest.mark.parametrize("key", ["eval_accuracy", "accuracy", "eval_acc"])
    def test_each_accepted_accuracy_spelling_is_found(self, key):
        assert _accuracy_of(with_metrics(**{key: 0.91})) == pytest.approx(0.91)

    @pytest.mark.parametrize(
        "key", ["eval_f1_macro", "eval_macro_f1", "macro_f1", "eval_f1"]
    )
    def test_each_accepted_f1_spelling_is_found(self, key):
        assert _f1_of(with_metrics(**{key: 0.88})) == pytest.approx(0.88)

    def test_the_first_listed_spelling_wins(self):
        """Two spellings in one record must resolve deterministically.

        Otherwise the figure shown depends on dict ordering, and two runs of the
        same page could disagree.
        """
        both = with_metrics(accuracy=0.50, eval_accuracy=0.90)

        assert _accuracy_of(both) == pytest.approx(0.90)

    @pytest.mark.parametrize(
        "record",
        [
            {},
            {"training_info": None},
            {"training_info": {}},
            {"training_info": {"metrics": None}},
            {"training_info": {"metrics": {}}},
        ],
    )
    def test_a_missing_metric_is_none_and_never_zero(self, record):
        """None renders as an em dash; 0.0 would render as "0.0%".

        "This model scored zero" and "this model was not measured" are opposite
        claims, and only one of them is true.
        """
        assert _accuracy_of(record) is None
        assert _f1_of(record) is None

    def test_a_non_numeric_metric_is_rejected(self):
        """A string here would crash the template's format filter."""
        assert _accuracy_of(with_metrics(eval_accuracy="0.91")) is None

    def test_a_boolean_is_not_accepted_as_a_metric(self):
        """`bool` is a subclass of `int`, so an isinstance check lets it through.

        A flag that leaked into the metrics dict would plot as 100% accuracy.
        """
        assert _accuracy_of(with_metrics(eval_accuracy=True)) is None


class TestShorten:
    def test_it_cuts_to_the_requested_width(self):
        assert _shorten("a" * 64, 12) == "a" * 12

    def test_none_becomes_empty_rather_than_the_string_none(self):
        """A root commit has no parent, and the table must not print "None"."""
        assert _shorten(None) == ""

    def test_a_short_value_is_returned_whole(self):
        assert _shorten("abc", 12) == "abc"


class TestAxisBounds:
    def test_bounds_snap_outward_to_the_gridline_step(self):
        """Every axis label has to be a round number a reader can judge against."""
        low, high = _axis_bounds([0.8231, 0.9012])

        assert low == pytest.approx(0.80)
        assert high == pytest.approx(0.95)

    def test_identical_values_still_produce_a_usable_domain(self):
        """Without a floor this divides by zero when every commit ties."""
        low, high = _axis_bounds([0.9, 0.9, 0.9])

        assert high > low
        assert high - low == pytest.approx(0.10)

    def test_a_single_point_produces_a_usable_domain(self):
        low, high = _axis_bounds([0.42])

        assert high - low == pytest.approx(0.10)

    def test_the_domain_never_claims_more_than_the_metric_can_hold(self):
        """An accuracy axis running past 100% would be nonsense."""
        low, high = _axis_bounds([0.99, 1.0])

        assert high <= 1.0
        assert low >= 0.0

    def test_a_wide_spread_is_not_narrowed(self):
        low, high = _axis_bounds([0.10, 0.95])

        assert low == pytest.approx(0.10)
        assert high == pytest.approx(0.95)


class TestChartGeometry:
    def points(self, *values):
        return [
            {"x": i, "value": v, "hash": f"{i:012d}", "message": "m", "timestamp": "t"}
            for i, v in enumerate(values)
        ]

    def geometry(self, *values):
        placed = self.points(*values)
        return _chart_geometry(placed, len(placed))

    def test_an_empty_series_does_not_raise(self):
        """A repository with no measured commits still renders its page."""
        geometry = _chart_geometry([], 0)

        assert geometry["points"] == []

    def test_the_highest_value_sits_above_the_lowest_on_screen(self):
        """SVG y grows downward, so a higher score must have a smaller y.

        Inverted, the chart would show every improvement as a decline.
        """
        geometry = self.geometry(0.80, 0.95)
        low, high = geometry["points"]

        assert high["py"] < low["py"]

    def test_time_runs_left_to_right(self):
        geometry = self.geometry(0.80, 0.85, 0.90)
        xs = [p["px"] for p in geometry["points"]]

        assert xs == sorted(xs)

    def test_every_point_stays_inside_the_plot_area(self):
        geometry = self.geometry(0.80, 0.9123, 0.95)

        for point in geometry["points"]:
            assert geometry["left"] <= point["px"] <= geometry["right"]
            assert geometry["top"] <= point["py"] <= geometry["bottom"]

    def test_a_lone_point_is_centred_rather_than_pinned_to_the_axis(self):
        geometry = self.geometry(0.9)
        middle = (geometry["left"] + geometry["right"]) / 2

        assert geometry["points"][0]["px"] == pytest.approx(middle)

    def test_a_point_lands_on_its_own_column_not_its_index(self):
        """The unmeasured commits between two points still take up their width.

        Placing by index instead would close the gap, drawing a steady climb
        across a stretch of history where nothing was measured at all.
        """
        sparse = [
            {"x": 0, "value": 0.80, "hash": "a", "message": "m", "timestamp": "t"},
            {"x": 9, "value": 0.90, "hash": "b", "message": "m", "timestamp": "t"},
        ]
        geometry = _chart_geometry(sparse, 10)

        assert geometry["points"][0]["px"] == geometry["left"]
        assert geometry["points"][1]["px"] == geometry["right"]

    def test_a_readout_near_the_right_edge_flips_inward(self):
        """Otherwise the hover label runs off the card it is drawn in."""
        wide = [
            {"x": 0, "value": 0.80, "hash": "a", "message": "m", "timestamp": "t"},
            {"x": 9, "value": 0.90, "hash": "b", "message": "m", "timestamp": "t"},
        ]
        first, last = _chart_geometry(wide, 10)["points"]

        assert first["readout_anchor"] == "start"
        assert first["readout_x"] > first["px"]
        assert last["readout_anchor"] == "end"
        assert last["readout_x"] < last["px"]

    def test_gridline_labels_are_whole_percentages(self):
        """Float drift is what turns a 0.85 gridline into "84.99999%"."""
        geometry = self.geometry(0.8231, 0.9012)

        for line in geometry["grid"]:
            assert line["label"].endswith("%")
            assert line["label"][:-1].lstrip("-").isdigit()

    def test_the_stated_axis_span_matches_the_gridlines_drawn(self):
        """The template prints low_pct-high_pct in prose above the plot.

        A non-zero baseline is only honest while that sentence is true, so the
        numbers in it must be the numbers on the axis.
        """
        geometry = self.geometry(0.8231, 0.9012)
        labels = [int(line["label"][:-1]) for line in geometry["grid"]]

        assert min(labels) == geometry["low_pct"]
        assert max(labels) == geometry["high_pct"]

    def test_gridlines_span_the_domain_at_the_axis_step(self):
        geometry = self.geometry(0.80, 0.95)

        assert len(geometry["grid"]) == 4  # 80, 85, 90, 95


class TestBuildSeries:
    def test_points_run_oldest_first(self):
        """The chart asks "did quality improve", so time must run forward."""
        series = _build_series(
            [
                commit(hash="b" * 64, timestamp="2026-03-01T00:00:00", **with_metrics(eval_accuracy=0.9)),
                commit(hash="a" * 64, timestamp="2026-01-01T00:00:00", **with_metrics(eval_accuracy=0.8)),
            ]
        )

        assert [p["value"] for p in series["points"]] == [0.8, 0.9]

    def test_unmeasured_commits_are_omitted_and_counted(self):
        """A gap in the line is honest; a dive to zero is a lie.

        The count is what lets the page say so in words instead of leaving the
        reader to wonder why the line is shorter than the history.
        """
        series = _build_series(
            [
                commit(hash="a" * 64, timestamp="2026-01-01T00:00:00", **with_metrics(eval_accuracy=0.8)),
                commit(hash="b" * 64, timestamp="2026-02-01T00:00:00"),
            ]
        )

        assert series["measured"] == 1
        assert series["unmeasured"] == 1
        assert series["total_commits"] == 2
        assert len(series["points"]) == 1

    def test_a_history_with_no_metrics_yields_an_empty_series(self):
        series = _build_series([commit(hash="a" * 64)])

        assert series["points"] == []
        assert series["measured"] == 0

    def test_a_single_branch_draws_one_line_with_no_name_on_it(self):
        """The page has already said the repository has one branch. Repeating it
        on the plot is noise over the one value a reader came to read."""
        series = _build_series(
            [
                commit(hash=ROOT, parent_hash=None, **with_metrics(eval_accuracy=0.8)),
                commit(hash=MID, parent_hash=ROOT, **with_metrics(eval_accuracy=0.9)),
            ],
            {"main": MID},
        )

        assert len(series["lines"]) == 1
        assert series["lines"][0]["label"] == "90.0%"


class TestBranchLines:
    """The chart's segments are claims about ancestry, so they get checked as such.

    A line segment says one value became the other. Drawn across a fork it
    reports a change nobody made -- on the demo history, a jump from a 4-bit
    probe at 86.4% to a dropout run at 90.7%, two siblings four points apart.
    That renders beautifully and is simply false, which is why these tests assert
    on which commits a segment joins rather than on how the plot looks.
    """

    def history(self):
        """The forked shape, with a metric on every commit.

            root(.80) ── mid(.85) ─┬─ tipA(.91)     main
                                   └─ sideB(.87)    probe
        """
        return [
            commit(hash=ROOT, parent_hash=None, timestamp="2026-01-01T00:00:00",
                   message="root", **with_metrics(eval_accuracy=0.80)),
            commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-02T00:00:00",
                   message="mid", **with_metrics(eval_accuracy=0.85)),
            commit(hash=SIDE_B, parent_hash=MID, timestamp="2026-01-03T00:00:00",
                   message="side", **with_metrics(eval_accuracy=0.87)),
            commit(hash=TIP_A, parent_hash=MID, timestamp="2026-01-04T00:00:00",
                   message="tip", **with_metrics(eval_accuracy=0.91)),
        ]

    def tips(self):
        return {"main": TIP_A, "probe": SIDE_B}

    def test_a_fork_becomes_two_lines(self):
        series = _build_series(self.history(), self.tips())

        assert [line["branch"] for line in series["lines"]] == ["main", "probe"]

    def test_no_segment_joins_two_siblings(self):
        """The defect this whole function exists to remove.

        `sideB` and `tipA` share a parent. A single date-ordered line would put
        a segment straight between them and report a four-point improvement that
        is really two independent experiments.
        """
        series = _build_series(self.history(), self.tips())
        parent = {
            _shorten(c["hash"]): _shorten(c["parent_hash"] or "")
            for c in self.history()
        }

        for line in series["lines"]:
            hashes = [p["hash"] for p in line["points"]]
            for earlier, later in zip(hashes[:-1], hashes[1:], strict=True):
                assert parent[later] == earlier

    def test_a_side_branch_starts_at_the_commit_it_forked_from(self):
        """Otherwise its line floats detached in the middle of the plot with no
        visible connection to the history it came out of."""
        series = _build_series(self.history(), self.tips())
        probe = next(line for line in series["lines"] if line["branch"] == "probe")

        assert [p["hash"] for p in probe["points"]] == [_shorten(MID), _shorten(SIDE_B)]

    def test_both_lines_leave_the_fork_from_the_same_point(self):
        """The two lines have to meet, or the fork does not read as a fork."""
        series = _build_series(self.history(), self.tips())
        main, probe = series["lines"]

        shared_on_main = next(p for p in main["points"] if p["hash"] == _shorten(MID))
        shared_on_probe = probe["points"][0]

        assert (shared_on_main["px"], shared_on_main["py"]) == (
            shared_on_probe["px"],
            shared_on_probe["py"],
        )

    def test_the_shared_commit_is_marked_once(self):
        """It sits on two paths. Two markers would stack their surface rings into
        a dot heavier than its neighbours, and a keyboard user would tab through
        the same value twice on the way past."""
        series = _build_series(self.history(), self.tips())
        marked = [p["hash"] for p in series["marks"]]

        assert marked.count(_shorten(MID)) == 1
        assert sorted(marked) == sorted({_shorten(c["hash"]) for c in self.history()})

    def test_every_commit_is_marked_exactly_once(self):
        series = _build_series(self.history(), self.tips())

        assert series["measured"] == 4
        assert series["unmeasured"] == 0

    def test_each_line_names_the_branch_it_follows(self):
        """With two lines the name is the only thing saying which is which. The
        hue must never be carrying that on its own."""
        series = _build_series(self.history(), self.tips())

        assert [line["label"] for line in series["lines"]] == ["main 91.0%", "probe 87.0%"]

    def test_main_is_drawn_first_whatever_the_dict_order(self):
        """The trunk owns the shared history, and the hues must not move between
        two loads of one page."""
        forward = _build_series(self.history(), {"main": TIP_A, "probe": SIDE_B})
        reversed_dict = _build_series(self.history(), {"probe": SIDE_B, "main": TIP_A})

        assert [line["branch"] for line in forward["lines"]] == ["main", "probe"]
        assert [line["branch"] for line in reversed_dict["lines"]] == ["main", "probe"]

    def test_branches_other_than_main_are_ordered_alphabetically(self):
        history = self.history() + [
            commit(hash="9" * 64, parent_hash=MID, timestamp="2026-01-05T00:00:00",
                   **with_metrics(eval_accuracy=0.88))
        ]
        series = _build_series(history, {"probe": SIDE_B, "audit": "9" * 64, "main": TIP_A})

        assert [line["branch"] for line in series["lines"]] == ["main", "audit", "probe"]

    def test_hues_cycle_rather_than_running_past_the_palette(self):
        """A fourth line reuses a hue instead of inventing one that has not been
        contrast-checked. Safe because the name, not the colour, identifies it."""
        history = [commit(hash=ROOT, parent_hash=None, **with_metrics(eval_accuracy=0.8))]
        tips = {"main": ROOT}
        for index in range(4):
            child = f"{index:x}" * 63 + "f"
            history.append(
                commit(hash=child, parent_hash=ROOT, timestamp=f"2026-01-0{index + 2}T00:00:00",
                       **with_metrics(eval_accuracy=0.8 + index / 100))
            )
            tips[f"b{index}"] = child

        hues = [line["hue"] for line in _build_series(history, tips)["lines"]]

        assert len(hues) == 5
        assert set(hues) <= {1, 2, 3}

    def test_a_branch_pointing_into_another_history_draws_no_second_line(self):
        """A ref at an older commit on main is not a separate line of work.

        Drawing it would lay a second stroke over the first, thickening part of
        main's line for no reason a reader could interpret.
        """
        series = _build_series(self.history(), {"main": TIP_A, "old": ROOT})

        assert [line["branch"] for line in series["lines"]] == ["main"]

    def test_an_unmeasured_fork_point_does_not_detach_the_branch(self):
        """A line whose fork commit has no metric simply starts one commit later,
        rather than dropping the branch or plotting a zero."""
        history = [
            commit(hash=ROOT, parent_hash=None, timestamp="2026-01-01T00:00:00",
                   **with_metrics(eval_accuracy=0.80)),
            commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-02T00:00:00"),
            commit(hash=SIDE_B, parent_hash=MID, timestamp="2026-01-03T00:00:00",
                   **with_metrics(eval_accuracy=0.87)),
            commit(hash=TIP_A, parent_hash=MID, timestamp="2026-01-04T00:00:00",
                   **with_metrics(eval_accuracy=0.91)),
        ]
        series = _build_series(history, self.tips())

        probe = next(line for line in series["lines"] if line["branch"] == "probe")
        assert [p["hash"] for p in probe["points"]] == [_shorten(SIDE_B)]
        assert series["unmeasured"] == 1

    def test_clock_skew_cannot_make_a_line_double_back(self):
        """x comes from topological rank, not from the timestamp.

        Here the parent is stamped after its child. Ordering by date would place
        the child to the left of its own parent and the polyline would run
        backwards through itself.
        """
        skewed = [
            commit(hash=ROOT, parent_hash=None, timestamp="2026-01-01T00:05:00",
                   **with_metrics(eval_accuracy=0.80)),
            commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-01T00:04:00",
                   **with_metrics(eval_accuracy=0.90)),
        ]
        series = _build_series(skewed, {"main": MID})
        xs = [p["px"] for p in series["lines"][0]["points"]]

        assert xs == sorted(xs)


class TestEndpointLabels:
    """Where each line's name is drawn, and why it is not where you would guess.

    A direct label is only worth having over a legend if it is legible, and the
    two ways it stops being legible are both invisible to a passing glance at a
    screenshot: it runs off the edge of the card, so "main 90.7%" renders as
    "main"; or the line it names passes straight through the text.

    Both were real. The label used to sit above and left of the endpoint, which
    put it on top of any line that arrived descending, and the plot used the full
    card width, which left the rightmost line's label nothing to be drawn in.
    """

    def two_lines(self, tip_accuracy=0.91, side_accuracy=0.86):
        history = [
            commit(hash=ROOT, parent_hash=None, timestamp="2026-01-01T00:00:00",
                   **with_metrics(eval_accuracy=0.88)),
            commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-02T00:00:00",
                   **with_metrics(eval_accuracy=0.89)),
            commit(hash=SIDE_B, parent_hash=MID, timestamp="2026-01-03T00:00:00",
                   **with_metrics(eval_accuracy=side_accuracy)),
            commit(hash=TIP_A, parent_hash=MID, timestamp="2026-01-04T00:00:00",
                   **with_metrics(eval_accuracy=tip_accuracy)),
        ]
        return _build_series(history, {"main": TIP_A, "probe": SIDE_B})

    def crowded(self, side_name="probe", accuracy=0.881):
        """Eleven commits on main, one branch off the second-to-last.

        Length is the point. x is a commit's rank in the whole history and no two
        commits share a rank, so two endpoints can never sit in the same column --
        they collide only when the columns are packed closer together than a label
        is wide. Eleven commits puts the pitch at ~64 units against a ~70-unit
        label, which is an ordinary-sized history, not a contrived one.
        """
        chain = [f"{index:064x}" for index in range(1, 13)]
        history = [commit(hash=chain[0], parent_hash=None,
                          timestamp="2026-01-01T00:00:00", **with_metrics(eval_accuracy=0.88))]
        for index in range(1, 11):
            history.append(
                commit(hash=chain[index], parent_hash=chain[index - 1],
                       timestamp=f"2026-01-{index + 1:02d}T00:00:00",
                       **with_metrics(eval_accuracy=0.88))
            )
        history.append(
            commit(hash=chain[11], parent_hash=chain[9], timestamp="2026-02-01T00:00:00",
                   **with_metrics(eval_accuracy=accuracy))
        )
        return _build_series(history, {"main": chain[10], side_name: chain[11]})

    def test_every_label_fits_inside_the_viewbox(self):
        """The defect the gutter exists to prevent: a clipped branch name."""
        series = self.two_lines()

        for line in series["lines"]:
            width = len(line["label"]) * LABEL_ADVANCE
            assert line["label_x"] + width <= CHART["width"]

    def test_the_plot_gives_up_room_for_the_widest_label(self):
        """Not the last one drawn. `main` may end at the far right while a longer
        name ends short of it, and it is the widest that decides the fit."""
        narrow = self.crowded(side_name="a")
        wide = self.crowded(side_name="experiment/quantised-int4")

        assert wide["geometry"]["right"] < narrow["geometry"]["right"]

    def test_a_single_line_reserves_only_what_its_percentage_needs(self):
        """One line carries no branch name, so the gutter is a few characters
        wide rather than however long the branch happens to be called."""
        series = _build_series(
            [commit(hash=ROOT, parent_hash=None, **with_metrics(eval_accuracy=0.9))],
            {"experiment/quantised-int4": ROOT},
        )

        assert series["lines"][0]["label"] == "90.0%"
        assert series["geometry"]["right"] > CHART["width"] - CHART["pad_right"] - 60

    def test_the_gutter_never_eats_more_than_its_share_of_the_plot(self):
        """A 60-character branch name would otherwise squeeze the line it labels
        into the left third of the card. Past the cap the label overhangs
        instead: losing the shape is worse than a label running long."""
        series = self.crowded(side_name="a" * 60)
        g = series["geometry"]
        full = CHART["width"] - CHART["pad_left"] - CHART["pad_right"]

        assert g["right"] - g["left"] >= full * (1 - MAX_GUTTER_FRACTION)

    def test_a_label_sits_outward_from_its_endpoint(self):
        """Outward, because nothing is drawn past a line's own end. Above-and-left
        is where a descending line runs through the text."""
        series = self.two_lines()

        for line in series["lines"]:
            assert line["label_x"] > line["end"]["px"]

    def test_a_label_is_level_with_the_point_it_names(self):
        """Level, so a reader's eye travels straight out from the marker to the
        name rather than searching a diagonal."""
        series = self.two_lines()

        for line in series["lines"]:
            assert abs(line["label_y"] - line["end"]["py"]) <= 6

    def test_labels_that_would_collide_are_pushed_apart(self):
        """Two branches finishing a tenth of a point apart is what a sweep looks
        like. Overlapping, they are not two labels but one smear."""
        series = self.crowded(accuracy=0.881)
        ys = sorted(line["label_y"] for line in series["lines"])

        assert ys[1] - ys[0] >= LABEL_PITCH

    def test_labels_clear_of_each_other_are_left_where_their_lines_ended(self):
        """Moving a label that had no reason to move would misreport where its
        line finished. These two end at opposite ends of the plot, so neither is
        in the other's way whatever their heights."""
        series = self.two_lines()

        for line in series["lines"]:
            assert line["label_y"] == round(line["end"]["py"] + 4, 2)

    def test_a_pushed_label_stays_inside_the_card(self):
        """Nudging one past the viewBox edge would trade a legible collision for
        an invisible label. The bottom margin is fair game -- nothing is drawn
        there -- but the edge of the card is not."""
        series = self.crowded(accuracy=0.8799)

        for line in series["lines"]:
            assert line["label_y"] <= CHART["height"]


class TestDagRows:
    def test_rows_run_newest_first(self):
        rows = _dag_rows(
            [
                commit(hash="a" * 64, timestamp="2026-01-01T00:00:00"),
                commit(hash="b" * 64, timestamp="2026-03-01T00:00:00"),
            ]
        )

        assert rows[0]["hash"] == "b" * 64

    def test_a_commit_without_a_parent_is_marked_as_a_root(self):
        """The hollow rail marker is driven by this flag."""
        rows = _dag_rows([commit(hash="a" * 64, parent_hash=None)])

        assert rows[0]["is_root"] is True
        assert rows[0]["parent_short"] is None

    def test_a_child_is_not_marked_as_a_root(self):
        rows = _dag_rows([commit(hash="b" * 64, parent_hash="a" * 64)])

        assert rows[0]["is_root"] is False
        assert rows[0]["parent_short"] == "a" * 12

    def test_the_timestamp_is_cut_to_the_minute_and_loses_its_t(self):
        """Seconds are three characters of noise in the widest column, and that
        width comes out of the message column, which is the one people read."""
        rows = _dag_rows([commit(timestamp="2026-01-02T03:04:05")])

        assert rows[0]["timestamp"] == "2026-01-02 03:04"

    def test_a_missing_author_falls_back_rather_than_showing_none(self):
        record = commit()
        del record["author"]

        assert _dag_rows([record])[0]["author"] == "—"

    def test_a_branch_tip_is_named_on_its_own_row(self):
        """Without this the graph shows three lines of work and names none."""
        rows = _dag_rows(
            [commit(hash="a" * 64), commit(hash="b" * 64, parent_hash="a" * 64)],
            {"main": "b" * 64, "probe": "a" * 64},
        )

        by_hash = {row["hash"]: row["refs"] for row in rows}
        assert by_hash["b" * 64] == ["main"]
        assert by_hash["a" * 64] == ["probe"]

    def test_two_branches_at_one_commit_are_both_named_in_a_stable_order(self):
        rows = _dag_rows([commit(hash="a" * 64)], {"zeta": "a" * 64, "alpha": "a" * 64})

        assert rows[0]["refs"] == ["alpha", "zeta"]

    def test_a_commit_no_branch_points_at_carries_no_names(self):
        rows = _dag_rows([commit(hash="a" * 64)], {"main": "b" * 64})

        assert rows[0]["refs"] == []


# ---------------------------------------------------------------------------
# The commit graph.
#
# The rail is the one thing on the repository page that can be confidently
# wrong. Every other cell either shows a value or shows an em dash, and a
# reader can tell which. A rail that assigns the wrong lane draws a perfectly
# clean picture of a history that never happened -- so these tests assert the
# geometry a reader would trace with a finger, not just that a number came out.
#
# The fixture below is the shape the demo seed builds, because it is the
# smallest history holding all three properties a linear one cannot show: two
# branches forking from the same commit, a fork point that is not a tip, and a
# lane passing through rows that belong to another branch.
#
#   root ── mid ─┬─ tipA        (lane 0, oldest at the bottom)
#                └─ sideB       (lane 1)
# ---------------------------------------------------------------------------

ROOT = "1" * 64
MID = "2" * 64
TIP_A = "3" * 64
SIDE_B = "4" * 64


def forked_history() -> list[dict]:
    """Two branches off one parent. Deliberately shuffled, not pre-sorted."""
    return [
        commit(hash=SIDE_B, parent_hash=MID, timestamp="2026-01-04T00:00:00"),
        commit(hash=ROOT, parent_hash=None, timestamp="2026-01-01T00:00:00"),
        commit(hash=TIP_A, parent_hash=MID, timestamp="2026-01-05T00:00:00"),
        commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-02T00:00:00"),
    ]


class TestRailOrder:
    def test_the_order_is_newest_first(self):
        order = [c["hash"] for c in _rail_order(forked_history())]

        assert order == [TIP_A, SIDE_B, MID, ROOT]

    def test_a_parent_never_appears_above_its_own_child(self):
        """The property the whole drawing rests on.

        A lane is handed downward, from a commit to its parent's row. If a
        parent sat above its child the lane would run off the bottom of the
        table toward a row that had already been passed.
        """
        order = [c["hash"] for c in _rail_order(forked_history())]
        position = {h: i for i, h in enumerate(order)}

        for record in forked_history():
            parent = record["parent_hash"]
            if parent:
                assert position[record["hash"]] < position[parent]

    def test_clock_skew_cannot_lift_a_parent_above_its_child(self):
        """Timestamps come from whichever machine made the commit.

        Here the parent is stamped a minute *after* its child, which two
        laptops with unsynchronised clocks produce routinely. Date order would
        put them the wrong way round; topological order cannot.
        """
        skewed = [
            commit(hash=ROOT, parent_hash=None, timestamp="2026-01-01T00:05:00"),
            commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-01T00:04:00"),
        ]

        assert [c["hash"] for c in _rail_order(skewed)] == [MID, ROOT]

    def test_two_commits_sharing_a_timestamp_order_the_same_way_every_time(self):
        """Otherwise the graph reshuffles between two loads of one page."""
        pair = [
            commit(hash=SIDE_B, parent_hash=MID, timestamp="2026-01-04T00:00:00"),
            commit(hash=TIP_A, parent_hash=MID, timestamp="2026-01-04T00:00:00"),
            commit(hash=MID, parent_hash=None, timestamp="2026-01-01T00:00:00"),
        ]

        first = [c["hash"] for c in _rail_order(pair)]
        assert first == [c["hash"] for c in _rail_order(list(reversed(pair)))]

    def test_a_commit_whose_parent_is_absent_is_still_placed(self):
        """A page showing the last N commits cuts the history somewhere."""
        order = _rail_order([commit(hash=MID, parent_hash=ROOT)])

        assert [c["hash"] for c in order] == [MID]

    def test_a_cycle_loses_no_rows(self):
        """Content addressing makes this impossible; a hand-edited store does not.

        Dropping the rows would be the worst response: the page would
        under-report the history while looking perfectly healthy.
        """
        cycle = [
            commit(hash=ROOT, parent_hash=MID, timestamp="2026-01-01T00:00:00"),
            commit(hash=MID, parent_hash=ROOT, timestamp="2026-01-02T00:00:00"),
        ]

        assert len(_rail_order(cycle)) == 2


class TestLaneAssignment:
    def test_a_linear_history_stays_in_one_lane(self):
        rail = _assign_lanes(_rail_order([commit(hash=MID, parent_hash=ROOT), commit(hash=ROOT)]))

        assert [row["lane"] for row in rail["rows"]] == [0, 0]
        assert rail["lanes"] == 1

    def test_a_fork_opens_a_second_lane_and_the_parent_keeps_the_first(self):
        rail = _assign_lanes(_rail_order(forked_history()))

        # TIP_A, SIDE_B, MID, ROOT
        assert [row["lane"] for row in rail["rows"]] == [0, 1, 0, 0]
        assert rail["lanes"] == 2

    def test_the_lane_a_branch_opened_closes_at_the_fork_point(self):
        """Two lanes exist only between the tips and the commit they share.

        The row *below* the fork point must be back to a single line, or the
        drawing claims a branch continues past where it started.
        """
        rail = _assign_lanes(_rail_order(forked_history()))
        root_row = rail["rows"][3]

        # One through-line would mean a second branch is still open here.
        assert root_row["segments"] == [{"cls": "rail-line", "d": "M20 0V10"}]

    def test_a_lane_passes_through_rows_it_does_not_own(self):
        """The fork point is two rows below the first tip, so lane 0 has to be
        drawn on the side branch's row without a marker on it."""
        rail = _assign_lanes(_rail_order(forked_history()))
        side_row = rail["rows"][1]

        assert {"cls": "rail-line", "d": f"M20 0V{RAIL_HEIGHT}"} in side_row["segments"]

    def test_a_side_branch_curves_into_the_lane_its_parent_already_holds(self):
        """A cubic, and it leaves and arrives vertically.

        A diagonal would meet the straight segments above and below at an
        angle, and the joins would read as breaks in the line.
        """
        rail = _assign_lanes(_rail_order(forked_history()))
        side_row = rail["rows"][1]

        curves = [s["d"] for s in side_row["segments"] if "C" in s["d"]]
        assert curves == ["M34 10C34 15 20 15 20 20"]

    def test_a_history_of_one_commit_draws_no_line_at_all(self):
        """Nothing above it, nothing below it -- only the marker.

        A stub of line either way would be a claim about a row that is not
        there, which is exactly what the rail must never make.
        """
        rail = _assign_lanes(_rail_order([commit(hash=ROOT, parent_hash=None)]))

        assert rail["rows"][0]["segments"] == []
        assert rail["rows"][0]["lane"] == 0

    def test_a_parent_off_the_page_is_drawn_cut_rather_than_continuing(self):
        """The parent exists; this page just does not go back far enough.

        A solid line to the row's edge would promise another row underneath.
        """
        rail = _assign_lanes(_rail_order([commit(hash=MID, parent_hash=ROOT)]))
        segments = rail["rows"][0]["segments"]

        assert segments == [{"cls": "rail-line cut", "d": f"M20 10V{RAIL_HEIGHT - 4}"}]

    def test_a_closed_lane_is_reused_before_a_new_one_is_opened(self):
        """Lanes are a resource, not a per-branch identity.

        Two forks that never overlap in time share one lane, so a long history
        of short-lived branches stays narrow instead of drifting rightward.
        """
        history = [
            commit(hash="a" * 64, parent_hash="c" * 64, timestamp="2026-01-06T00:00:00"),
            commit(hash="b" * 64, parent_hash="c" * 64, timestamp="2026-01-05T00:00:00"),
            commit(hash="c" * 64, parent_hash="e" * 64, timestamp="2026-01-04T00:00:00"),
            commit(hash="d" * 64, parent_hash="e" * 64, timestamp="2026-01-03T00:00:00"),
            commit(hash="e" * 64, parent_hash=None, timestamp="2026-01-01T00:00:00"),
        ]

        rail = _assign_lanes(_rail_order(history))

        assert [row["lane"] for row in rail["rows"]] == [0, 1, 0, 1, 0]
        assert rail["lanes"] == 2

    def test_every_row_gets_geometry(self):
        ordered = _rail_order(forked_history())
        rail = _assign_lanes(ordered)

        assert len(rail["rows"]) == len(ordered)

    def test_the_drawing_never_names_a_lane_no_row_owns(self):
        """Reserved width comes from the widest marker, so a segment drawn to
        the right of every marker would be clipped out of the cell."""
        rail = _assign_lanes(_rail_order(forked_history()))
        widest = _lane_x(max(row["lane"] for row in rail["rows"]))

        for row in rail["rows"]:
            for segment in row["segments"]:
                for token in segment["d"].replace("M", " ").replace("C", " ").split():
                    assert float(token.split("V")[0]) <= widest


class TestRailWidth:
    def test_a_linear_history_reserves_one_lane(self):
        assert _rail_lanes([{"lane": 0}, {"lane": 0}]) == 1

    def test_the_widest_row_sets_the_width(self):
        assert _rail_lanes([{"lane": 0}, {"lane": 2}, {"lane": 1}]) == 3

    def test_an_empty_history_still_reserves_a_lane(self):
        """The column exists in the header whether or not there are rows."""
        assert _rail_lanes([]) == 1

    def test_the_width_is_capped(self):
        """Past the cap the drawing gets crowded; it never gets wrong. The
        Parent column carries the exact ancestry either way."""
        assert _rail_lanes([{"lane": 40}]) == MAX_LANES

    def test_lanes_past_the_cap_share_the_rightmost_column(self):
        assert _lane_x(MAX_LANES) == _lane_x(MAX_LANES - 1)

    def test_lanes_are_evenly_pitched(self):
        steps = [_lane_x(i + 1) - _lane_x(i) for i in range(MAX_LANES - 1)]

        assert len(set(steps)) == 1


class TestRepoCommits:
    """The version count both pages print, and the reason it is shared code.

    The index and the repository page have to agree about how many versions a
    repository has. They cannot disagree while both call this.
    """

    class FakeStorage:
        """Just enough of HubStorage: tip hash in, ancestry out, newest first."""

        def __init__(self, histories: dict[str, list[dict]], broken: set[str] = frozenset()):
            self.histories = histories
            self.broken = broken

        def commit_history(self, tip: str, limit: int = 100) -> list[dict]:
            if tip in self.broken:
                raise AethelError(f"unreadable object {tip}")
            return self.histories[tip][:limit]

    class FakeRepo:
        def __init__(self, branches: dict[str, str]):
            self.branches = branches

    def test_diverged_branches_count_their_union_not_their_longest(self):
        """Two branches sharing ancestors are more versions than either alone.

        Taking the longest branch is the bug this function exists to prevent: a
        3-commit and a 4-commit branch over 2 shared ancestors are 5 distinct
        versions. Reporting 4 on one page and 5 on the other for the same
        repository undermines every other number on the site.
        """
        root = commit(hash="0" * 64)
        shared = commit(hash="1" * 64, parent_hash="0" * 64)
        main_tip = commit(hash="2" * 64, parent_hash="1" * 64)
        side_a = commit(hash="3" * 64, parent_hash="1" * 64)
        side_b = commit(hash="4" * 64, parent_hash="3" * 64)

        commits = _repo_commits(
            self.FakeStorage(
                {
                    "2" * 64: [main_tip, shared, root],
                    "4" * 64: [side_b, side_a, shared, root],
                }
            ),
            self.FakeRepo({"main": "2" * 64, "side": "4" * 64}),
        )

        assert len(commits) == 5
        assert len({c["hash"] for c in commits}) == 5

    def test_a_shared_ancestor_is_counted_once(self):
        """Both branches walk back through it, so it is reached twice."""
        root = commit(hash="0" * 64)

        commits = _repo_commits(
            self.FakeStorage({"0" * 64: [root]}),
            self.FakeRepo({"main": "0" * 64, "copy": "0" * 64}),
        )

        assert len(commits) == 1

    def test_an_unreadable_branch_is_skipped_rather_than_failing_the_page(self):
        """A missing object must not turn the landing page into a 500.

        The other branches are still legible, and a broken object store is
        /ops's story to tell.
        """
        root = commit(hash="0" * 64)

        commits = _repo_commits(
            self.FakeStorage({"0" * 64: [root]}, broken={"9" * 64}),
            self.FakeRepo({"main": "0" * 64, "wrecked": "9" * 64}),
        )

        assert [c["hash"] for c in commits] == ["0" * 64]

    def test_a_repository_with_no_branches_has_no_commits(self):
        assert _repo_commits(self.FakeStorage({}), self.FakeRepo({})) == []


class TestLatestAccuracy:
    """The figure both the index and the repository page print as "Latest"."""

    def test_it_reads_the_newest_commit_by_timestamp(self):
        """Not the first branch encountered.

        Branch iteration order is dict order, so a figure taken from whichever
        branch came first would depend on insertion order rather than on time.
        """
        assert _latest_accuracy(
            [
                commit(timestamp="2026-01-01T00:00:00", **with_metrics(eval_accuracy=0.70)),
                commit(timestamp="2026-05-01T00:00:00", **with_metrics(eval_accuracy=0.90)),
                commit(timestamp="2026-03-01T00:00:00", **with_metrics(eval_accuracy=0.80)),
            ]
        ) == pytest.approx(0.90)

    def test_an_unmeasured_newest_commit_does_not_blank_the_figure(self):
        """It is the newest *measured* value.

        A commit pushed without eval must not erase the most recent thing that is
        actually known -- the honest reading is "0.80, as of the last measured
        version", not "no data".
        """
        assert _latest_accuracy(
            [
                commit(timestamp="2026-01-01T00:00:00", **with_metrics(eval_accuracy=0.80)),
                commit(timestamp="2026-06-01T00:00:00"),
            ]
        ) == pytest.approx(0.80)

    def test_a_history_with_nothing_measured_is_none(self):
        assert _latest_accuracy([commit(), commit()]) is None

    def test_an_empty_history_is_none(self):
        assert _latest_accuracy([]) is None


class TestProofLadder:
    """The ladder is the page's argument, so it must agree with the verifier."""

    def build(self, count: int):
        """A real log of `count` leaves, with a proof for the first commit.

        MerkleTree applies `hash_leaf` to whatever it is given and `get_proof`
        takes a raw value, so both are handed plain commit hashes here -- the
        same way `TransparencyLog.tree` does it.
        """
        commits = [f"{i:064x}" for i in range(count)]
        tree = MerkleTree(commits)
        proof = tree.get_proof(commits[0])

        return {
            "commit_hash": commits[0],
            "proof": [{"sibling": sibling, "side": side} for sibling, side in proof],
            "root": tree.get_root(),
        }

    def test_no_proof_yields_no_ladder(self):
        """An unlogged commit renders an explanation, not an empty ladder."""
        assert _proof_ladder(None) == []
        assert _proof_ladder({}) == []

    @pytest.mark.parametrize("leaves", [2, 3, 4, 5, 8, 9])
    def test_the_last_rung_lands_on_the_published_root(self, leaves):
        """This is the whole claim: fold the leaf through the siblings and you
        arrive at the root the Hub published. If the ladder's final value were
        anything else, the page would be showing arithmetic that does not work.
        """
        proof = self.build(leaves)
        ladder = _proof_ladder(proof)

        assert ladder, "a multi-leaf log must produce at least one rung"
        assert ladder[-1]["result"] == proof["root"]

    @pytest.mark.parametrize("leaves", [2, 4, 5, 8])
    def test_the_ladder_agrees_with_the_verifier(self, leaves):
        """Drawn and checked by the same two hash functions, so they cannot drift.

        The picture is the more persuasive of the two, so it is the one that must
        not be able to lie.
        """
        proof = self.build(leaves)

        assert TransparencyLog.verify(proof["commit_hash"], proof["proof"], proof["root"])
        assert _proof_ladder(proof)[-1]["result"] == proof["root"]

    def test_each_rung_folds_the_one_below_it(self):
        """Rung N's input is rung N-1's output, starting from the leaf.

        A ladder that restarted from the leaf each time would still end on the
        right root for a two-leaf log and be wrong for every larger one.
        """
        proof = self.build(8)
        ladder = _proof_ladder(proof)

        running = hash_leaf(proof["commit_hash"])
        for rung in ladder:
            expected = (
                hash_node(rung["sibling"], running)
                if rung["side"] == "left"
                else hash_node(running, rung["sibling"])
            )
            assert rung["result"] == expected
            running = rung["result"]

    def test_the_side_decides_the_order_of_the_pair(self):
        """Order is the whole content of `side`: hash_node is not commutative,
        so folding a left sibling on the right produces a different root."""
        leaf = "1" * 64
        sibling = "2" * 64

        left = _proof_ladder({"commit_hash": leaf, "proof": [{"sibling": sibling, "side": "left"}]})
        right = _proof_ladder({"commit_hash": leaf, "proof": [{"sibling": sibling, "side": "right"}]})

        assert left[0]["result"] == hash_node(sibling, hash_leaf(leaf))
        assert right[0]["result"] == hash_node(hash_leaf(leaf), sibling)
        assert left[0]["result"] != right[0]["result"]

    def test_rungs_are_numbered_from_one(self):
        ladder = _proof_ladder(self.build(8))

        assert [rung["index"] for rung in ladder] == list(range(1, len(ladder) + 1))

    def test_only_the_final_rung_is_flagged_last(self):
        """The template gives that rung the root treatment."""
        ladder = _proof_ladder(self.build(8))

        assert [rung["is_last"] for rung in ladder] == [False] * (len(ladder) - 1) + [True]

    def test_a_malformed_step_truncates_rather_than_inventing_a_remainder(self):
        """A step with no usable side cannot be folded.

        Continuing past it would draw rungs whose values mean nothing, ending on
        a root that does not match -- which reads as a verification failure
        rather than as malformed input.
        """
        ladder = _proof_ladder(
            {
                "commit_hash": "1" * 64,
                "proof": [
                    {"sibling": "2" * 64, "side": "left"},
                    {"sibling": "3" * 64, "side": "sideways"},
                    {"sibling": "4" * 64, "side": "right"},
                ],
            }
        )

        assert len(ladder) == 1
        assert ladder[0]["is_last"] is True

    def test_a_single_leaf_log_has_an_empty_proof_and_no_rungs(self):
        """With one leaf the root *is* the leaf hash, so there is nothing to fold.

        The commit page has to handle this: it is the state of the log right
        after the very first push.
        """
        commits = ["a" * 64]
        tree = MerkleTree(commits)

        assert _proof_ladder(
            {"commit_hash": commits[0], "proof": [], "root": tree.get_root()}
        ) == []
