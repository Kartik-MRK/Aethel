"""CPU training, saving, and evaluation with a locally built tiny model."""

import csv
import json

import pytest

pytest.importorskip("torch", reason="training tests need the ML extra")
pytest.importorskip("transformers")
pytest.importorskip("peft")

import torch
from transformers import (
    BertConfig,
    BertForSequenceClassification,
    BertTokenizerFast,
    DistilBertConfig,
    DistilBertForSequenceClassification,
    DistilBertTokenizerFast,
)

from aethel.commands.train import TrainError, load_training_yaml, run_training
from aethel.core.commits import build_base_object, create_commit
from aethel.core.repo import Repo
from aethel.evaluation.evaluator import (
    IncompatibleEvaluation,
    evaluate_current_vs_parent,
    evaluate_patch,
    require_compatible,
)
from aethel.evaluation.protocol import load_split

pytestmark = pytest.mark.ml


@pytest.fixture
def training_repo(tmp_path, monkeypatch, request):
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    monkeypatch.chdir(tmp_path)
    model_path = tmp_path / "tiny-model"
    model_path.mkdir()
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "good", "bad", "movie"]
    vocab += [str(i) for i in range(40)]
    vocab_path = model_path / "vocab.txt"
    vocab_path.write_text("\n".join(vocab) + "\n")
    distilbert = getattr(request, "param", "bert") == "distilbert"
    tokenizer_class = DistilBertTokenizerFast if distilbert else BertTokenizerFast
    tokenizer = tokenizer_class(vocab_file=str(vocab_path))
    tokenizer.save_pretrained(model_path)
    torch.manual_seed(7)
    if distilbert:
        base = DistilBertForSequenceClassification(DistilBertConfig(
            vocab_size=len(vocab), dim=16, n_layers=1, n_heads=2, hidden_dim=32,
            max_position_embeddings=32, num_labels=2,
        ))
    else:
        base = BertForSequenceClassification(BertConfig(
            vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1,
            num_attention_heads=2, intermediate_size=32, max_position_embeddings=32,
            num_labels=2,
        ))
    base.save_pretrained(model_path)
    data_path = tmp_path / "reviews.csv"
    with data_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sentence", "sentiment"])
        writer.writerows([(f"{'good' if i % 2 else 'bad'} movie {i}", i % 2) for i in range(40)])
    config_path = tmp_path / "train.yaml"
    config_path.write_text(
        "dataset: reviews.csv\ntask_type: sequence_classification\n"
        "text_column: sentence\nlabel_column: sentiment\nlabel_names: [negative, positive]\n"
        "lora_rank: 2\nlora_alpha: 4\nbatch_size: 4\nepochs: 0.5\n"
        "seed: 42\nmax_length: 12\neval_batch_size: 3\n"
    )
    repo = Repo.create(tmp_path, {"model_id": str(model_path), "revision_sha": "b" * 40, "author": "test"})
    yield repo, config_path
    torch.set_num_threads(old_threads)


@pytest.mark.parametrize("training_repo", ["bert", "distilbert"], indirect=True)
def test_training_save_reload_and_shared_parent_evaluation(training_repo):
    from typer.testing import CliRunner

    from aethel.commands.diff import diff_commits
    from aethel.core.workspace import workspace_files
    from aethel.main import app

    repo, config = training_repo
    first_info = run_training(str(config))
    measured = first_info["evaluation"]["current"]
    reloaded = evaluate_patch(repo, repo.workspace_dir)
    assert measured["sample_count"] == 8
    assert reloaded["accuracy"] == measured["accuracy"]
    assert reloaded["macro_f1"] == measured["macro_f1"]
    assert reloaded["eval_loss"] == pytest.approx(measured["eval_loss"], abs=1e-6)
    assert reloaded["adapter_sha256"] == measured["adapter_sha256"]
    assert first_info["max_length"] == 12
    assert first_info["text_column"] == "sentence"
    assert first_info["initialization"]["kind"] == "base"
    assert first_info["stub"] is False
    manifest = first_info["data_manifest"]
    assert len(load_split(repo.root, manifest, "test")) == 8

    base_hash = repo.objects.write_json("bases", build_base_object(
        first_info["model_id"], first_info["revision_sha"]
    ))
    first_hash = create_commit(repo, message="first", author="test", base_hash=base_hash, training_info=first_info)
    config.write_text(config.read_text().replace("seed: 42", "seed: 43"))
    second_info = run_training(str(config))
    assert second_info["data_manifest"] == manifest
    compared = evaluate_current_vs_parent(repo, repo.workspace_dir)
    assert compared["comparison_status"] == "compared"
    assert compared["parent"]["evaluation_spec"] == compared["current"]["evaluation_spec"]
    assert compared["parent"]["adapter_sha256"] == measured["adapter_sha256"]
    assert compared["parent"]["accuracy"] == measured["accuracy"]
    second_hash = create_commit(repo, message="second", author="test", base_hash=base_hash, training_info=second_info)
    before = workspace_files(repo.workspace_dir)
    diff = diff_commits(repo, first_hash, second_hash)
    assert diff["layers"]
    assert any(layer["difference_norm"] > 0 for layer in diff["layers"])
    assert workspace_files(repo.workspace_dir) == before
    runner = CliRunner()
    result = runner.invoke(app, ["diff", first_hash, second_hash, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["right"] == second_hash
    evaluation = runner.invoke(app, ["eval", "main", "--json"])
    assert evaluation.exit_code == 0, evaluation.output
    assert json.loads(evaluation.stdout)["evaluation_spec"]["split"] == "validation"


def test_training_exception_preserves_previous_adapter(training_repo, monkeypatch):
    from aethel.commands import train as module

    repo, config = training_repo
    previous = repo.workspace_dir / "adapter_model.safetensors"
    previous.write_bytes(b"previous staged work")

    def fail(_self, *args, **kwargs):
        raise RuntimeError("simulated training failure")

    monkeypatch.setattr(module.Trainer, "train", fail)
    with pytest.raises(RuntimeError, match="simulated"), torch.random.fork_rng():
        run_training(str(config), force=True)
    assert previous.read_bytes() == b"previous staged work"


def test_missing_data_never_clears_workspace(training_repo):
    repo, config = training_repo
    previous = repo.workspace_dir / "adapter_model.safetensors"
    previous.write_bytes(b"previous")
    (repo.root / "reviews.csv").unlink()
    with pytest.raises(TrainError, match="does not exist"):
        run_training(str(config), force=True)
    assert previous.read_bytes() == b"previous"


@pytest.mark.parametrize("line", ["task_type: causal_lm", "batch_size: 0", "learning_rate: .nan", "learning_rae: 0.001"])
def test_invalid_training_config_is_rejected(training_repo, line):
    _, config = training_repo
    config.write_text(config.read_text() + line + "\n")
    with pytest.raises(TrainError):
        load_training_yaml(str(config))


def test_comparison_rejects_changed_task_or_leaked_split():
    info = {
        "model_id": "base", "revision_sha": "revision", "num_labels": 2,
        "task_type": "SEQ_CLS", "label_names": ["negative", "positive"],
        "data_manifest": {"examples_sha256": "data", "splits": {"train": [0], "validation": [1], "test": [2]}},
    }
    wrong = json.loads(json.dumps(info))
    wrong["label_names"] = ["safe", "unsafe"]
    with pytest.raises(IncompatibleEvaluation, match="label_names"):
        require_compatible(wrong, info)
    leaked = json.loads(json.dumps(info))
    leaked["data_manifest"]["splits"]["train"] = [1]
    with pytest.raises(IncompatibleEvaluation, match="overlap"):
        require_compatible(leaked, info)
