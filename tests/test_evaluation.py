import pytest

pytest.importorskip("torch", reason="the evaluator needs the [ml] extra")

pytestmark = pytest.mark.ml


from types import SimpleNamespace  # noqa: E402

from aethel.evaluation.comparison import compare_metrics  # noqa: E402
from aethel.evaluation.evaluator import evaluate_current_vs_parent  # noqa: E402
from aethel.evaluation.metrics import adapter_size, calculate_metrics  # noqa: E402


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


def test_adapter_size(tmp_path):
    adapter_file = tmp_path / "adapter_model.safetensors"

    # Create a 1 MB fixture file.
    adapter_file.write_bytes(b"\0" * (1024 * 1024))

    size = adapter_size(tmp_path)

    assert size == pytest.approx(1.0)


def test_calculate_metrics_empty_dataset():
    class DummyModel:
        def eval(self):
            return self

    with pytest.raises(ValueError, match="Evaluation dataset is empty"):
        calculate_metrics(DummyModel(), [])


def test_evaluate_current_vs_parent_root_commit(monkeypatch, tmp_path):
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