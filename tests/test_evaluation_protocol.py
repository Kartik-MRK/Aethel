"""Split identity, leakage prevention, and metric calculation."""

import csv
from copy import deepcopy

import pytest

from aethel.evaluation.comparison import compare_metrics
from aethel.evaluation.protocol import (
    build_manifest,
    classification_metrics,
    load_split,
    read_examples,
)


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "data.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sentence", "sentiment"])
        writer.writerows([(f"unique review {i}", i % 2) for i in range(60)])
    return path


def manifest_for(path, **kwargs):
    return build_manifest(path, text_column="sentence", label_column="sentiment", **kwargs)


def test_splits_are_disjoint_reproducible_and_stratified(dataset):
    records, manifest = manifest_for(dataset)
    _, repeated = manifest_for(dataset)
    assert manifest == repeated
    splits = [set(manifest["splits"][name]) for name in ("train", "validation", "test")]
    assert set.union(*splits) == set(range(len(records)))
    for i, left in enumerate(splits):
        assert {records[index]["label"] for index in left} == {0, 1}
        for right in splits[i + 1:]:
            assert left.isdisjoint(right)


def test_sample_limit_is_applied_before_splitting(dataset):
    _, manifest = manifest_for(dataset, max_samples=30)
    assert manifest["selected_examples"] == 30
    assert sum(map(len, manifest["splits"].values())) == 30


def test_duplicate_text_cannot_cross_splits(dataset):
    with dataset.open("a") as handle:
        handle.write('  UNIQUE   review 0  ,0\n')
    examples, manifest = manifest_for(dataset)
    assert len(examples) == 60
    assert sum(map(len, manifest["splits"].values())) == 60


def test_conflicting_duplicate_labels_fail(dataset):
    with dataset.open("a") as handle:
        handle.write('unique review 0,1\n')
    with pytest.raises(ValueError, match="Conflicting labels"):
        manifest_for(dataset)


def test_changed_file_is_refused_at_evaluation(dataset):
    _, manifest = manifest_for(dataset)
    dataset.write_text(dataset.read_text().replace("review 0", "changed 0"))
    with pytest.raises(ValueError, match="mismatch"):
        load_split(dataset.parent, manifest, "validation")


def test_modified_split_membership_is_refused(dataset):
    _, manifest = manifest_for(dataset)
    manifest["splits"]["validation"][0] = manifest["splits"]["train"][0]
    with pytest.raises(ValueError, match="splits"):
        load_split(dataset.parent, manifest, "validation")


def test_relative_dataset_path_survives_repository_move(dataset, tmp_path):
    _, manifest = manifest_for(dataset)
    manifest["dataset_file"] = dataset.name
    relocated = tmp_path / "other"
    relocated.mkdir()
    (relocated / dataset.name).write_bytes(dataset.read_bytes())
    assert load_split(relocated, manifest, "validation") == load_split(tmp_path, manifest, "validation")


@pytest.mark.parametrize("validation,test", [(0, 0.2), (0.8, 0.3), (float("nan"), 0.2)])
def test_invalid_split_fractions_fail(dataset, validation, test):
    with pytest.raises(ValueError):
        manifest_for(dataset, validation_fraction=validation, test_fraction=test)


def test_class_with_too_few_unique_examples_fails(tmp_path):
    path = tmp_path / "small.csv"
    path.write_text("text,label\na,0\nb,0\nc,1\nd,1\n")
    with pytest.raises(ValueError, match="three unique"):
        build_manifest(path)


def test_wrong_columns_fail(dataset):
    with pytest.raises(ValueError, match="columns"):
        read_examples(dataset, "text", "label")


def test_metrics_include_absent_predicted_classes():
    result = classification_metrics([0, 0, 1, 1], [0, 0, 0, 0], 2)
    assert result["accuracy"] == 0.5
    assert result["macro_f1"] == pytest.approx(1 / 3)
    assert result["class_support"] == [2, 2]
    assert result["confusion_matrix"] == [[2, 0], [2, 0]]


def test_metrics_on_different_specs_cannot_be_compared():
    current = {"eval_loss": 1, "accuracy": 0.5, "adapter_size_mb": 1, "evaluation_spec": {"hash": "a"}}
    parent = deepcopy(current)
    parent["evaluation_spec"]["hash"] = "b"
    with pytest.raises(ValueError, match="different evaluation"):
        compare_metrics(parent, current)
