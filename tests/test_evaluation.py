from types import SimpleNamespace

import pytest

from aethel.evaluation.comparison import compare_metrics

# `comparison` is dict arithmetic and imports nothing, so its test runs on a base install. The
# other three reach `metrics` and `evaluator`, which import torch and transformers at module
# scope, so their imports sit inside the test bodies behind an importorskip. Gating the whole
# file instead would cost the comparison logic its coverage in the four core CI jobs.
NEEDS_ML = "the evaluator needs the [ml] extra"


def test_compare_metrics():
    parent = {
        "eval_loss": 0.70,
        "accuracy": 0.60,
        "adapter_size_mb": 2.0,
    }

    current = {
        "eval_loss": 0.65,
        "accuracy": 0.75,
        "adapter_size_mb": 2.5,
    }

    result = compare_metrics(parent, current)

    assert result["loss_change"] == pytest.approx(-0.05)
    assert result["accuracy_change"] == pytest.approx(0.15)
    assert result["adapter_size_change"] == pytest.approx(0.5)
    assert result["improved"] is True


@pytest.mark.ml
def test_adapter_size(tmp_path):
    pytest.importorskip("torch", reason=NEEDS_ML)

    from aethel.evaluation.metrics import adapter_size

    adapter_file = tmp_path / "adapter_model.safetensors"

    # Create a 1 MB fixture file.
    adapter_file.write_bytes(b"\0" * (1024 * 1024))

    size = adapter_size(tmp_path)

    assert size == pytest.approx(1.0)


@pytest.mark.ml
def test_calculate_metrics_empty_dataset():
    pytest.importorskip("torch", reason=NEEDS_ML)

    from aethel.evaluation.metrics import calculate_metrics

    class DummyModel:
        def eval(self):
            return self

    with pytest.raises(ValueError, match="Evaluation dataset is empty"):
        calculate_metrics(DummyModel(), [])


@pytest.mark.ml
def test_evaluate_current_vs_parent_root_commit(monkeypatch, tmp_path):
    pytest.importorskip("torch", reason=NEEDS_ML)

    from aethel.evaluation.evaluator import evaluate_current_vs_parent

    fake_metrics = {
        "eval_loss": 0.65,
        "accuracy": 0.75,
        "adapter_size_mb": 2.5,
    }

    # Avoid running an actual model evaluation here.
    monkeypatch.setattr(
        "aethel.evaluation.evaluator.evaluate_patch",
        lambda repo, path: fake_metrics,
    )

    refs = SimpleNamespace(
        require_attached_branch=lambda: "main",
        read_branch=lambda branch: None,
    )

    repo = SimpleNamespace(refs=refs)

    result = evaluate_current_vs_parent(repo, tmp_path)

    assert result["current"] == fake_metrics
    assert result["parent"] is None
    assert result["comparison"] is None
