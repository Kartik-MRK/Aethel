import tempfile
from pathlib import Path

from .comparison import compare_metrics
from .dataset_loader import load_validation_dataset
from .inference import load_model_for_evaluation
from .metrics import adapter_size, calculate_metrics


def evaluate_patch(repo, adapter_path: Path):
    """
    Evaluate one adapter directory.

    Returns:
        {
            "eval_loss": ...,
            "accuracy": ...,
            "adapter_size_mb": ...
        }
    """

    adapter_path = Path(adapter_path)

    dataset = load_validation_dataset(
        repo,
        adapter_path,
    )

    model, _ = load_model_for_evaluation(
        adapter_path,
    )

    results = calculate_metrics(
        model,
        dataset,
    )

    results["adapter_size_mb"] = adapter_size(
        adapter_path,
    )

    return results


def evaluate_current_vs_parent(repo, current_adapter_path: Path):
    """
    Evaluate the current workspace adapter and, when a parent commit
    exists, evaluate the parent adapter as well.

    The parent adapter is extracted into a temporary directory.
    The actual workspace is never modified.

    Returns:
        {
            "current": {...},
            "parent": {...} | None,
            "comparison": {...} | None
        }
    """

    branch = repo.refs.require_attached_branch()
    parent_hash = repo.refs.read_branch(branch)

    # Evaluate the current workspace adapter first.
    current_metrics = evaluate_patch(
        repo,
        Path(current_adapter_path),
    )

    # First commit has no parent.
    if parent_hash is None:
        return {
            "current": current_metrics,
            "parent": None,
            "comparison": None,
        }

    # Read the parent commit object.
    parent_commit = repo.objects.read_json(
        "commits",
        parent_hash,
    )

    parent_tree_hash = parent_commit["tree"]

    # Extract the parent's complete workspace into a temporary directory.
    with tempfile.TemporaryDirectory(prefix="aethel_parent_") as temp_dir:
        parent_path = Path(temp_dir)

        repo.objects.extract_tree(
            parent_tree_hash,
            parent_path,
        )

        parent_metrics = evaluate_patch(
            repo,
            parent_path,
        )

    comparison = compare_metrics(
        parent_metrics,
        current_metrics,
    )

    return {
        "current": current_metrics,
        "parent": parent_metrics,
        "comparison": comparison,
    }