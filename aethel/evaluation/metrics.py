import os

import torch
from transformers import default_data_collator


def calculate_metrics(model, dataset):
    """
    Evaluate a sequence-classification model.

    Returns:
        {
            "eval_loss": ...,
            "accuracy": ...
        }
    """

    model.eval()

    total_loss = 0.0
    total_samples = 0
    correct_predictions = 0

    with torch.no_grad():

        for sample in dataset:

            batch = default_data_collator([sample])

            outputs = model(**batch)

            # Accumulate loss
            if outputs.loss is not None:
                total_loss += outputs.loss.item()

            # Classification prediction
            predictions = torch.argmax(
                outputs.logits,
                dim=-1,
            )

            labels = batch["labels"]

            correct_predictions += (
                predictions == labels
            ).sum().item()

            total_samples += labels.size(0)

    if total_samples == 0:
        raise ValueError("Evaluation dataset is empty.")

    avg_loss = total_loss / total_samples

    accuracy = correct_predictions / total_samples

    return {
        "eval_loss": avg_loss,
        "accuracy": accuracy,
    }


def adapter_size(adapter_path):
    """
    Return adapter_model.safetensors size in MB.
    """

    adapter_file = os.path.join(
        adapter_path,
        "adapter_model.safetensors",
    )

    if not os.path.isfile(adapter_file):
        raise FileNotFoundError(
            f"Adapter file not found: {adapter_file}"
        )

    size = os.path.getsize(adapter_file)

    return size / (1024 * 1024)