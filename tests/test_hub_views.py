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
    _accuracy_of,
    _axis_bounds,
    _build_series,
    _chart_geometry,
    _dag_rows,
    _f1_of,
    _latest_accuracy,
    _proof_ladder,
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
            {"value": v, "hash": f"{i:012d}", "message": "m", "timestamp": "t"}
            for i, v in enumerate(values)
        ]

    def test_an_empty_series_does_not_raise(self):
        """A repository with no measured commits still renders its page."""
        geometry = _chart_geometry([])

        assert geometry["points"] == []
        assert geometry["path"] == ""

    def test_the_highest_value_sits_above_the_lowest_on_screen(self):
        """SVG y grows downward, so a higher score must have a smaller y.

        Inverted, the chart would show every improvement as a decline.
        """
        geometry = _chart_geometry(self.points(0.80, 0.95))
        low, high = geometry["points"]

        assert high["py"] < low["py"]

    def test_time_runs_left_to_right(self):
        geometry = _chart_geometry(self.points(0.80, 0.85, 0.90))
        xs = [p["px"] for p in geometry["points"]]

        assert xs == sorted(xs)

    def test_every_point_stays_inside_the_plot_area(self):
        geometry = _chart_geometry(self.points(0.80, 0.9123, 0.95))

        for point in geometry["points"]:
            assert geometry["left"] <= point["px"] <= geometry["right"]
            assert geometry["top"] <= point["py"] <= geometry["bottom"]

    def test_a_lone_point_is_centred_rather_than_pinned_to_the_axis(self):
        geometry = _chart_geometry(self.points(0.9))
        middle = (geometry["left"] + geometry["right"]) / 2

        assert geometry["points"][0]["px"] == pytest.approx(middle)

    def test_the_path_starts_with_a_moveto_and_then_only_linetos(self):
        """A path whose second command was another M would draw nothing."""
        geometry = _chart_geometry(self.points(0.80, 0.85, 0.90))
        commands = geometry["path"].split()

        assert commands[0].startswith("M")
        assert all(c.startswith("L") for c in commands[1:])
        assert len(commands) == 3

    def test_gridline_labels_are_whole_percentages(self):
        """Float drift is what turns a 0.85 gridline into "84.99999%"."""
        geometry = _chart_geometry(self.points(0.8231, 0.9012))

        for line in geometry["grid"]:
            assert line["label"].endswith("%")
            assert line["label"][:-1].lstrip("-").isdigit()

    def test_the_stated_axis_span_matches_the_gridlines_drawn(self):
        """The template prints low_pct-high_pct in prose above the plot.

        A non-zero baseline is only honest while that sentence is true, so the
        numbers in it must be the numbers on the axis.
        """
        geometry = _chart_geometry(self.points(0.8231, 0.9012))
        labels = [int(line["label"][:-1]) for line in geometry["grid"]]

        assert min(labels) == geometry["low_pct"]
        assert max(labels) == geometry["high_pct"]

    def test_gridlines_span_the_domain_at_the_axis_step(self):
        geometry = _chart_geometry(self.points(0.80, 0.95))

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
        """The hollow marker in the table is driven by this flag."""
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
