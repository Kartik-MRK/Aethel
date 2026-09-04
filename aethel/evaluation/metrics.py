"""Batched classification inference and adapter size measurements."""

from pathlib import Path

import torch
from torch.utils.data import DataLoader
from transformers import default_data_collator

from aethel.evaluation.protocol import classification_metrics


def calculate_metrics(model, dataset, batch_size: int = 8):
    if len(dataset) == 0:
        raise ValueError("Evaluation dataset is empty.")
    if batch_size <= 0:
        raise ValueError("Evaluation batch size must be positive")
    model.eval()
    device = next(model.parameters()).device
    labels, predictions = [], []
    loss_sum = 0.0
    loss_samples = 0
    with torch.inference_mode():
        for batch in DataLoader(dataset, batch_size=batch_size, shuffle=False, collate_fn=default_data_collator):
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            if not torch.isfinite(outputs.logits).all():
                raise ValueError("Model produced non-finite logits")
            actual = batch["labels"]
            count = actual.numel()
            labels.extend(actual.cpu().tolist())
            predictions.extend(outputs.logits.argmax(dim=-1).cpu().tolist())
            if outputs.loss is not None:
                loss = float(outputs.loss)
                if not torch.isfinite(outputs.loss):
                    raise ValueError("Model produced a non-finite loss")
                loss_sum += loss * count
                loss_samples += count
    if loss_samples != len(labels):
        raise ValueError("Model did not return a loss for every evaluation example")
    return {
        **classification_metrics(labels, predictions, model.config.num_labels),
        "eval_loss": loss_sum / loss_samples,
    }


def adapter_size(adapter_path):
    """Adapter size in MiB; the result retains its legacy field name."""
    return (Path(adapter_path) / "adapter_model.safetensors").stat().st_size / (1024 * 1024)
