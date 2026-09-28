"""Evaluate adapters on an explicit shared held-out specification."""

import tempfile
from pathlib import Path

from aethel.core.hashing import hash_file

from .comparison import compare_metrics
from .dataset_loader import load_validation_dataset, read_training_info
from .inference import load_model_for_evaluation
from .metrics import adapter_size, calculate_metrics
from .protocol import evaluation_spec


class IncompatibleEvaluation(ValueError):
    """Adapters cannot be compared under the same task specification."""


def require_compatible(adapter_info: dict, reference_info: dict) -> None:
    for key in ("model_id", "num_labels", "task_type", "label_names"):
        if adapter_info.get(key) != reference_info.get(key):
            raise IncompatibleEvaluation(f"Adapters differ in {key}")
    revision = adapter_info.get("revision_sha") or adapter_info.get("revision_hash")
    reference_revision = reference_info.get("revision_sha") or reference_info.get("revision_hash")
    if revision != reference_revision:
        raise IncompatibleEvaluation("Adapters use different base revisions")
    if not reference_info.get("label_names"):
        left = (adapter_info.get("data_manifest") or {}).get("examples_sha256")
        right = (reference_info.get("data_manifest") or {}).get("examples_sha256")
        if left != right:
            raise IncompatibleEvaluation("Cross-dataset comparison requires explicit matching label_names")
    adapter_data = adapter_info.get("data_manifest") or {}
    reference_data = reference_info.get("data_manifest") or {}
    if adapter_data.get("examples_sha256") == reference_data.get("examples_sha256"):
        training = set(adapter_data.get("splits", {}).get("train", []))
        held_out = set(reference_data.get("splits", {}).get("validation", []))
        held_out.update(reference_data.get("splits", {}).get("test", []))
        if training & held_out:
            raise IncompatibleEvaluation("Evaluation examples overlap the adapter's training split")


def evaluate_patch(repo, adapter_path: Path, *, split: str = "validation", evaluation_path: Path | None = None):
    adapter_path = Path(adapter_path)
    reference_path = Path(evaluation_path) if evaluation_path else adapter_path
    adapter_hash = hash_file(adapter_path / "adapter_model.safetensors")
    config_hash = hash_file(adapter_path / "adapter_config.json")
    adapter_info = read_training_info(adapter_path)
    reference_info = read_training_info(reference_path)
    require_compatible(adapter_info, reference_info)
    spec = evaluation_spec(reference_info, split)
    dataset = load_validation_dataset(repo, reference_path, split=split)
    model, _ = load_model_for_evaluation(adapter_path)
    results = calculate_metrics(model, dataset, batch_size=int(reference_info.get("eval_batch_size", 8)))
    if (
        hash_file(adapter_path / "adapter_model.safetensors") != adapter_hash
        or hash_file(adapter_path / "adapter_config.json") != config_hash
    ):
        raise ValueError("Adapter files changed during evaluation")
    return {
        **results,
        "adapter_size_mb": adapter_size(adapter_path),
        "adapter_sha256": adapter_hash,
        "adapter_config_sha256": config_hash,
        "evaluation_spec": spec,
    }


def evaluate_current_vs_parent(repo, current_adapter_path: Path):
    branch = repo.refs.require_attached_branch()
    parent_hash = repo.refs.read_branch(branch)
    current_metrics = evaluate_patch(repo, Path(current_adapter_path))
    result = {
        "status": "measured", "current": current_metrics,
        "parent": None, "comparison": None,
    }
    if parent_hash is None:
        return result
    parent_commit = repo.objects.read_json("commits", parent_hash)
    with tempfile.TemporaryDirectory(prefix="aethel_parent_") as temp_dir:
        parent_path = Path(temp_dir)
        repo.objects.extract_tree(parent_commit["tree"], parent_path)
        try:
            parent_metrics = evaluate_patch(
                repo, parent_path, evaluation_path=Path(current_adapter_path)
            )
        except IncompatibleEvaluation as exc:
            result["comparison_status"] = "not_comparable"
            result["comparison_reason"] = str(exc)
            return result
    result["parent"] = parent_metrics
    result["comparison"] = compare_metrics(parent_metrics, current_metrics)
    result["comparison_status"] = "compared"
    return result
