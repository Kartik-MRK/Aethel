"""Compare effective standard LoRA updates with bounded intermediate memory."""

import json
import math
from pathlib import Path

import torch
from safetensors.torch import load_file


def _summary(left_sq, right_sq, difference_sq, dot):
    left_norm, right_norm = math.sqrt(left_sq), math.sqrt(right_sq)
    distance = math.sqrt(difference_sq)
    cosine = max(-1.0, min(1.0, dot / (left_norm * right_norm))) if left_norm and right_norm else None
    return {
        "left_norm": left_norm, "right_norm": right_norm, "difference_norm": distance,
        "relative_change": distance / left_norm if left_norm else None, "cosine": cosine,
    }


def effective_difference(left_a, left_b, left_scale, right_a, right_b, right_scale, block_size=256):
    """Compare scale * B @ A; factor coordinates alone do not identify an update."""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    tensors = (left_a, left_b, right_a, right_b)
    if any(tensor.ndim != 2 or not torch.isfinite(tensor).all() for tensor in tensors):
        raise ValueError("LoRA factors must be finite matrices")
    if left_a.shape[0] != left_b.shape[1] or right_a.shape[0] != right_b.shape[1]:
        raise ValueError("LoRA A/B rank mismatch")
    if left_a.shape[1] != right_a.shape[1] or left_b.shape[0] != right_b.shape[0]:
        raise ValueError("Adapters target different layer dimensions")
    if not math.isfinite(left_scale) or not math.isfinite(right_scale):
        raise ValueError("LoRA scales must be finite")
    left_a, left_b, right_a, right_b = (tensor.to(dtype=torch.float64, device="cpu") for tensor in tensors)
    totals = [0.0] * 4
    for start in range(0, left_b.shape[0], block_size):
        left = (left_b[start:start + block_size] @ left_a) * left_scale
        right = (right_b[start:start + block_size] @ right_a) * right_scale
        totals[0] += float(left.square().sum())
        totals[1] += float(right.square().sum())
        totals[2] += float((right - left).square().sum())
        totals[3] += float((left * right).sum())
    return _summary(*totals)


def _read_adapter(path: Path):
    config = json.loads((path / "adapter_config.json").read_text(encoding="utf-8"))
    if config.get("peft_type") != "LORA":
        raise ValueError("Diff supports standard LoRA adapters only")
    for option in ("use_dora", "use_rslora", "rank_pattern", "alpha_pattern", "lora_bias", "use_qalora"):
        if config.get(option):
            raise ValueError(f"Diff does not support adapter option {option}")
    if config.get("bias", "none") != "none":
        raise ValueError("Diff requires bias: none")
    rank, alpha = config.get("r"), config.get("lora_alpha")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
        raise ValueError("Adapter rank must be a positive integer")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha):
        raise ValueError("Adapter alpha must be finite")
    tensors = load_file(str(path / "adapter_model.safetensors"), device="cpu")
    factors, other = {}, {}
    for key, tensor in tensors.items():
        if ".lora_A." in key:
            partner = key.replace(".lora_A.", ".lora_B.")
            if partner not in tensors or tensor.ndim != 2 or tensor.shape[0] != rank:
                raise ValueError(f"Missing or inconsistent LoRA factors for {key}")
            factors[key.split(".lora_A.")[0]] = (tensor, tensors[partner], alpha / rank)
        elif ".lora_B." in key:
            if key.replace(".lora_B.", ".lora_A.") not in tensors:
                raise ValueError(f"Missing LoRA A factor for {key}")
        else:
            other[key] = tensor
    if not factors:
        raise ValueError("Adapter contains no supported LoRA factors")
    return config, factors, other


def compare_adapters(left_path: Path, right_path: Path):
    left_config, left_layers, left_other = _read_adapter(Path(left_path))
    right_config, right_layers, right_other = _read_adapter(Path(right_path))
    for field in ("task_type", "fan_in_fan_out"):
        if left_config.get(field) != right_config.get(field):
            raise ValueError(f"Adapters differ in {field}")
    if left_layers.keys() != right_layers.keys() or left_other.keys() != right_other.keys():
        raise ValueError("Adapters have different target layers or saved modules")
    layers = []
    for name in sorted(left_layers):
        layers.append({"name": name, **effective_difference(*left_layers[name], *right_layers[name])})
    saved = []
    for name in sorted(left_other):
        left, right = left_other[name].double(), right_other[name].double()
        if left.shape != right.shape:
            raise ValueError(f"Saved module dimensions differ: {name}")
        if not torch.isfinite(left).all() or not torch.isfinite(right).all():
            raise ValueError(f"Saved module contains non-finite values: {name}")
        saved.append({"name": name, **_summary(
            float(left.square().sum()), float(right.square().sum()),
            float((right - left).square().sum()), float((left * right).sum()),
        )})
    fields = ("r", "lora_alpha", "lora_dropout", "target_modules", "modules_to_save")
    return {
        "method": "effective-standard-lora-v1",
        "configuration_changes": {
            field: {"left": left_config.get(field), "right": right_config.get(field)}
            for field in fields if left_config.get(field) != right_config.get(field)
        },
        "layers": layers, "saved_modules": saved,
    }
