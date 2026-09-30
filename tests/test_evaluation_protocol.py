"""Split identity, leakage prevention, and metric calculation."""

import csv
from copy import deepcopy
from pathlib import Path

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
    _, manifest = manifest_for(dataset, repo_root=tmp_path)
    assert manifest["dataset_file"] == dataset.name
    expected = load_split(tmp_path, manifest, "validation")
    relocated = tmp_path / "other"
    relocated.mkdir()
    (relocated / dataset.name).write_bytes(dataset.read_bytes())
    dataset.unlink()
    assert load_split(relocated, manifest, "validation") == expected


def test_manifest_path_policy_preserves_dataset_identity(dataset, tmp_path):
    _, absolute = manifest_for(dataset)
    _, relative = manifest_for(dataset, repo_root=tmp_path)
    assert absolute["dataset_file"] == str(dataset.resolve())
    assert relative["dataset_file"] == dataset.name
    assert {key: value for key, value in relative.items() if key != "dataset_file"} == {
        key: value for key, value in absolute.items() if key != "dataset_file"
    }
    assert load_split(tmp_path / "unrelated", absolute, "validation") == load_split(tmp_path, relative, "validation")


def test_relative_input_uses_repository_root_not_current_directory(dataset, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _, manifest = manifest_for(Path(dataset.name), repo_root=tmp_path)
    assert manifest["dataset_file"] == dataset.name
    assert len(load_split(Path(".."), manifest, "validation")) == 12


def test_dataset_outside_repository_remains_absolute(dataset, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _, manifest = manifest_for(Path("..") / dataset.name, repo_root=repo)
    assert manifest["dataset_file"] == str(dataset.resolve())
    assert len(load_split(repo, manifest, "validation")) == 12


def test_external_dataset_symlink_is_not_recorded_as_portable(dataset, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    link = repo / "linked.csv"
    link.symlink_to(dataset)
    _, manifest = manifest_for(link, repo_root=repo)
    assert manifest["dataset_file"] == str(dataset.resolve())
    link.unlink()
    assert len(load_split(repo, manifest, "validation")) == 12


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
