def compare_metrics(parent_metrics, current_metrics):
    """
    Compare the current adapter against its parent adapter.
    """

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

    return {
        "loss_change": loss_change,
        "accuracy_change": accuracy_change,
        "adapter_size_change": adapter_size_change,
        "improved": improved,
    }