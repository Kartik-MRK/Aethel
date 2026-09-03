"""Recorded dataset splits and classification metrics, without ML dependencies."""

import csv
import math
import random
from collections import defaultdict
from pathlib import Path

from aethel.core.hashing import hash_file, hash_json

SPLITS = ("train", "validation", "test")


def read_examples(path: Path, text_column: str, label_column: str) -> list[dict]:
    """Read CSV examples and deduplicate normalized text before splitting."""
    examples = []
    seen = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {text_column, label_column}.issubset(reader.fieldnames or []):
            raise ValueError(f"Dataset needs columns {text_column!r} and {label_column!r}")
        for row_number, row in enumerate(reader, start=2):
            text = row.get(text_column)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"Empty text at CSV row {row_number}")
            try:
                label = int(row[label_column])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid integer label at CSV row {row_number}") from exc
            if label < 0:
                raise ValueError(f"Negative label at CSV row {row_number}")
            key = " ".join(text.split()).casefold()
            if key in seen:
                if seen[key] != label:
                    raise ValueError(f"Conflicting labels for duplicate text at CSV row {row_number}")
                continue
            seen[key] = label
            examples.append({"text": text.strip(), "label": label})
    if not examples:
        raise ValueError("Dataset is empty")
    return examples


def build_manifest(
    path: Path,
    *,
    text_column: str = "text",
    label_column: str = "label",
    seed: int = 42,
    max_samples: int = 0,
    validation_fraction: float = 0.2,
    test_fraction: float = 0.2,
) -> tuple[list[dict], dict]:
    """Create deterministic stratified partitions of unique examples."""
    if not (0 < validation_fraction < 1 and 0 < test_fraction < 1):
        raise ValueError("Validation and test fractions must be between zero and one")
    if validation_fraction + test_fraction >= 1:
        raise ValueError("Validation and test fractions must leave training examples")
    if max_samples < 0:
        raise ValueError("max_samples cannot be negative")
    path = Path(path).resolve()
    examples = read_examples(path, text_column, label_column)
    indices = list(range(len(examples)))
    rng = random.Random(seed)
    if max_samples and max_samples < len(indices):
        indices = sorted(rng.sample(indices, max_samples))
    by_label = defaultdict(list)
    for index in indices:
        by_label[examples[index]["label"]].append(index)
    labels = sorted(by_label)
    if labels != list(range(len(labels))) or len(labels) < 2:
        raise ValueError("Classification labels must be consecutive integers from zero, with at least two classes")
    splits = {name: [] for name in SPLITS}
    for label, members in sorted(by_label.items()):
        if len(members) < 3:
            raise ValueError(f"Label {label} needs at least three unique examples for train/validation/test")
        rng.shuffle(members)
        validation_count = max(1, int(len(members) * validation_fraction))
        test_count = max(1, int(len(members) * test_fraction))
        if validation_count + test_count >= len(members):
            raise ValueError(f"Split fractions leave no training examples for label {label}")
        splits["validation"].extend(members[:validation_count])
        splits["test"].extend(members[validation_count:validation_count + test_count])
        splits["train"].extend(members[validation_count + test_count:])
    for members in splits.values():
        members.sort()
    manifest = {
        "schema": 1,
        "algorithm": "stratified-unique-text-v1",
        "dataset_file": str(path),
        "raw_sha256": hash_file(path),
        "examples_sha256": hash_json(examples),
        "unique_examples": len(examples),
        "selected_examples": len(indices),
        "text_column": text_column,
        "label_column": label_column,
        "label_ids": labels,
        "seed": seed,
        "max_samples": max_samples,
        "validation_fraction": validation_fraction,
        "test_fraction": test_fraction,
        "splits": splits,
    }
    return examples, manifest


def load_split(repo_root: Path, manifest: dict, split: str) -> list[dict]:
    """Load exactly the recorded split and refuse modified inputs or membership."""
    if split not in SPLITS:
        raise ValueError(f"Unknown dataset split: {split}")
    if manifest.get("schema") != 1 or manifest.get("algorithm") != "stratified-unique-text-v1":
        raise ValueError("Unsupported dataset manifest")
    path = Path(manifest["dataset_file"])
    if not path.is_absolute():
        path = Path(repo_root) / path
    examples, rebuilt = build_manifest(
        path,
        text_column=manifest["text_column"],
        label_column=manifest["label_column"],
        seed=manifest["seed"],
        max_samples=manifest["max_samples"],
        validation_fraction=manifest["validation_fraction"],
        test_fraction=manifest["test_fraction"],
    )
    for key in ("raw_sha256", "examples_sha256", "splits", "label_ids", "selected_examples", "unique_examples"):
        if manifest.get(key) != rebuilt[key]:
            raise ValueError(f"Dataset manifest mismatch: {key}")
    return [examples[index] for index in rebuilt["splits"][split]]


def evaluation_spec(info: dict, split: str = "validation") -> dict:
    manifest = info.get("data_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("No held-out dataset manifest recorded. Train with the current configuration first.")
    spec = {
        "schema": 1,
        "evaluator": "classification-v1",
        "split": split,
        "examples_sha256": manifest["examples_sha256"],
        "indices_sha256": hash_json(manifest["splits"][split]),
        "label_ids": manifest["label_ids"],
        "label_names": info.get("label_names"),
        "model_id": info["model_id"],
        "revision_sha": info.get("revision_sha") or info.get("revision_hash"),
        "max_length": info["max_length"],
        "sample_count": len(manifest["splits"][split]),
    }
    return {**spec, "hash": hash_json(spec)}


def classification_metrics(labels: list[int], predictions: list[int], num_labels: int) -> dict:
    if not labels or len(labels) != len(predictions):
        raise ValueError("Labels and predictions must have the same nonzero length")
    confusion = [[0] * num_labels for _ in range(num_labels)]
    for label, prediction in zip(labels, predictions, strict=True):
        if not (0 <= label < num_labels and 0 <= prediction < num_labels):
            raise ValueError("Label or prediction is outside the configured classes")
        confusion[label][prediction] += 1
    support = [sum(row) for row in confusion]
    f1 = []
    for index in range(num_labels):
        true_positive = confusion[index][index]
        denominator = support[index] + sum(row[index] for row in confusion)
        f1.append(2 * true_positive / denominator if denominator else 0.0)
    return {
        "accuracy": sum(confusion[i][i] for i in range(num_labels)) / len(labels),
        "macro_f1": math.fsum(f1) / num_labels,
        "sample_count": len(labels),
        "class_support": support,
        "confusion_matrix": confusion,
    }
