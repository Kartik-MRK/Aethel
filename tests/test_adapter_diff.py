"""Effective LoRA comparison, including equivalent factorizations."""

import json

import pytest

pytest.importorskip("torch")
pytest.importorskip("safetensors")

import torch
from safetensors.torch import save_file

from aethel.ml.diff import compare_adapters, effective_difference

pytestmark = pytest.mark.ml


def test_equivalent_factorizations_have_zero_difference():
    a = torch.tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    b = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    result = effective_difference(a, b, 2.0, a * 2, b / 2, 2.0)
    assert result["difference_norm"] == 0
    assert result["cosine"] == pytest.approx(1)


def test_alpha_and_rank_scaling_is_applied():
    a, b = torch.ones(1, 3), torch.ones(2, 1)
    result = effective_difference(a, b, 1.0, a, b, 2.0)
    assert result["difference_norm"] == pytest.approx(6**0.5)
    assert result["right_norm"] == pytest.approx(2 * result["left_norm"])


def test_different_ranks_can_represent_the_same_update():
    a, b = torch.ones(1, 3), torch.ones(2, 1)
    wider_a = torch.cat([a, torch.zeros_like(a)])
    wider_b = torch.cat([b, torch.zeros_like(b)], dim=1)
    result = effective_difference(a, b, 1.0, wider_a, wider_b, 1.0)
    assert result["difference_norm"] == 0


def test_zero_update_has_no_cosine():
    a, b = torch.ones(1, 3), torch.zeros(2, 1)
    result = effective_difference(a, b, 1.0, a, b, 1.0)
    assert result["cosine"] is None
    assert result["relative_change"] is None
    json.dumps(result, allow_nan=False)


def test_blocked_comparison_matches_dense_calculation():
    generator = torch.Generator().manual_seed(15)
    a = torch.randn(3, 7, generator=generator)
    b = torch.randn(9, 3, generator=generator)
    c = torch.randn(2, 7, generator=generator)
    d = torch.randn(9, 2, generator=generator)
    result = effective_difference(a, b, 2.0, c, d, 3.0, block_size=2)
    expected = torch.linalg.vector_norm(3 * d.double() @ c.double() - 2 * b.double() @ a.double())
    assert result["difference_norm"] == pytest.approx(float(expected))


def test_invalid_tensor_is_rejected():
    a, b = torch.ones(1, 3), torch.full((2, 1), float("nan"))
    with pytest.raises(ValueError, match="finite"):
        effective_difference(a, b, 1.0, a, b, 1.0)


def write_adapter(path, **overrides):
    path.mkdir()
    config = {"peft_type": "LORA", "task_type": "SEQ_CLS", "r": 1, "lora_alpha": 2, **overrides}
    (path / "adapter_config.json").write_text(json.dumps(config))
    save_file({
        "base.layer.lora_A.weight": torch.ones(1, 3),
        "base.layer.lora_B.weight": torch.ones(2, 1),
        "base.classifier.weight": torch.zeros(2, 3),
    }, str(path / "adapter_model.safetensors"))


def test_saved_module_changes_are_reported_separately(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_adapter(left)
    write_adapter(right)
    result = compare_adapters(left, right)
    assert len(result["layers"]) == 1
    assert result["saved_modules"][0]["name"] == "base.classifier.weight"
    assert result["saved_modules"][0]["difference_norm"] == 0


@pytest.mark.parametrize("option", ["use_dora", "use_rslora", "rank_pattern"])
def test_unsupported_variants_are_rejected(tmp_path, option):
    left, right = tmp_path / "left", tmp_path / "right"
    write_adapter(left, **{option: True})
    write_adapter(right)
    with pytest.raises(ValueError, match=option):
        compare_adapters(left, right)
