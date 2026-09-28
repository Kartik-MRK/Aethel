def compare_metrics(parent_metrics, current_metrics):
    """
    Compare the current adapter against its parent adapter.
    """

    parent_spec = parent_metrics.get("evaluation_spec")
    current_spec = current_metrics.get("evaluation_spec")
    if parent_spec != current_spec:
        raise ValueError("Cannot compare metrics measured on different evaluation specifications")
    parent_loss = parent_metrics["eval_loss"]
    current_loss = current_metrics["eval_loss"]

    parent_accuracy = parent_metrics["accuracy"]
    current_accuracy = current_metrics["accuracy"]

    parent_size = parent_metrics["adapter_size_mb"]
    current_size = current_metrics["adapter_size_mb"]

    loss_change = current_loss - parent_loss
    accuracy_change = current_accuracy - parent_accuracy
    adapter_size_change = current_size - parent_size

    # Accuracy is the primary metric for this classification task.
    improved = current_accuracy > parent_accuracy

    result = {
        "loss_change": loss_change,
        "accuracy_change": accuracy_change,
        "adapter_size_change": adapter_size_change,
        "improved": improved,
    }
    if "macro_f1" in parent_metrics and "macro_f1" in current_metrics:
        result["macro_f1_change"] = current_metrics["macro_f1"] - parent_metrics["macro_f1"]
    return result
