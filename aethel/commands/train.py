"""Train a classification adapter with recorded data splits and safe staging."""

import json
import math
import platform
import shutil
from contextlib import suppress
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import torch
import yaml
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    default_data_collator,
    set_seed,
)

from aethel.commands._common import console, handle_errors
from aethel.core.atomic import atomic_write_text
from aethel.core.errors import AethelError
from aethel.core.hashing import hash_file
from aethel.core.repo import Repo
from aethel.core.workspace import staged_workspace
from aethel.evaluation.local_dataset import ExamplesDataset
from aethel.evaluation.metrics import adapter_size, calculate_metrics
from aethel.evaluation.protocol import build_manifest, evaluation_spec


class TrainError(AethelError):
    """Invalid training configuration or input data."""


def load_training_yaml(config_path: str) -> dict:
    try:
        data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TrainError(f"Cannot read training config: {exc}") from exc
    if not isinstance(data, dict):
        raise TrainError("Training config must be a YAML mapping")
    required = ("dataset", "task_type", "lora_rank", "lora_alpha", "batch_size", "epochs")
    missing = [key for key in required if key not in data]
    if missing:
        raise TrainError(f"Missing config fields: {', '.join(missing)}")
    if data["task_type"] not in ("sequence_classification", "SEQ_CLS"):
        raise TrainError("Only task_type: sequence_classification is supported")
    config = {
        "task_type": "SEQ_CLS", "dataset": str(data["dataset"]),
        "text_column": str(data.get("text_column", "text")),
        "label_column": str(data.get("label_column", "label")),
    }
    integers = {
        "lora_rank": 8, "lora_alpha": 16, "batch_size": 2, "max_samples": 0,
        "num_labels": 0, "max_length": 128, "gradient_accumulation_steps": 1,
        "seed": 42, "split_seed": 42, "eval_batch_size": 8,
    }
    floats = {
        "epochs": 1.0, "learning_rate": 5e-5, "validation_fraction": 0.2,
        "test_fraction": 0.2, "lora_dropout": 0.05,
    }
    supported = set(required) | set(integers) | set(floats) | {
        "text_column", "label_column", "label_names", "deterministic",
    }
    unknown = set(data) - supported
    if unknown:
        raise TrainError(f"Unknown config fields: {', '.join(sorted(map(str, unknown)))}")
    try:
        for key, default in integers.items():
            value = data.get(key, default)
            if isinstance(value, bool) or float(value) != int(value):
                raise ValueError(f"{key} must be an integer")
            config[key] = int(value)
        for key, default in floats.items():
            value = data.get(key, default)
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{key} must be a finite number")
            config[key] = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrainError(f"Invalid training configuration: {exc}") from exc
    for key in ("lora_rank", "lora_alpha", "batch_size", "max_length", "gradient_accumulation_steps", "eval_batch_size", "epochs", "learning_rate"):
        if config[key] <= 0:
            raise TrainError(f"{key} must be greater than zero")
    for key in ("max_samples", "num_labels", "seed", "split_seed"):
        if config[key] < 0:
            raise TrainError(f"{key} cannot be negative")
    if config["seed"] >= 2**32:
        raise TrainError("seed must be smaller than 2**32")
    if not 0 <= config["lora_dropout"] < 1:
        raise TrainError("lora_dropout must be between zero and one")
    config["deterministic"] = data.get("deterministic", False)
    if not isinstance(config["deterministic"], bool):
        raise TrainError("deterministic must be true or false")
    names = data.get("label_names")
    if names is not None and (
        not isinstance(names, list)
        or not all(isinstance(name, str) and name.strip() for name in names)
        or len(set(names)) != len(names)
    ):
        raise TrainError("label_names must list distinct class names in label-ID order")
    config["label_names"] = names
    return config


def resolve_dataset_file(dataset_path: str) -> str:
    path = Path(dataset_path)
    if path.is_file():
        if path.suffix.lower() != ".csv":
            raise TrainError("Training reads CSV files; export other formats to CSV first")
        return str(path.resolve())
    if path.is_dir():
        train = path / "train.csv"
        if train.is_file():
            return str(train.resolve())
        candidates = sorted(path.glob("*.csv"))
        if len(candidates) == 1:
            return str(candidates[0].resolve())
        raise TrainError(f"Expected train.csv or one unambiguous CSV file in {path}")
    raise TrainError(f"Dataset path does not exist: {path}")


def load_model_and_tokenizer(model_id: str, revision_hash: str, num_labels: int = 2):
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision_hash)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
    if tokenizer.pad_token is None:
        raise TrainError("The tokenizer has no usable padding token")
    model = AutoModelForSequenceClassification.from_pretrained(
        model_id, revision=revision_hash, num_labels=num_labels
    )
    return model, tokenizer, TaskType.SEQ_CLS


def infer_lora_target_modules(model) -> list[str]:
    preferred = ("q_proj", "v_proj", "k_proj", "o_proj", "q_lin", "v_lin", "query", "value", "key")
    available = {name.split(".")[-1] for name, module in model.named_modules() if isinstance(module, torch.nn.Linear)}
    targets = [name for name in preferred if name in available]
    if not targets:
        raise TrainError("No supported attention projection modules found for LoRA")
    return targets


def run_training(config_path: str, allow_stub: bool = False, force: bool = False) -> dict:
    repo = Repo.discover()
    repo_config = repo.read_config()
    config = load_training_yaml(config_path)
    model_id = repo_config["model_id"]
    revision = repo_config.get("revision_sha") or repo_config.get("revision_hash")
    if not revision:
        raise TrainError("Repository has no pinned model revision")
    with staged_workspace(repo, force=force) as output:
        try:
            path = Path(config["dataset"])
            if not path.is_absolute():
                path = repo.root / path
            stub = not path.exists() and allow_stub
            if stub:
                console.print("[yellow]Synthetic training requested. No held-out score will be reported.[/yellow]")
                examples = [{"text": f"sample training record {i}", "label": i % 2} for i in range(16)]
                manifest = None
                train_examples = examples
                validation_examples = None
                num_labels = 2
            else:
                path = Path(resolve_dataset_file(str(path)))
                examples, manifest = build_manifest(
                    path, text_column=config["text_column"], label_column=config["label_column"],
                    seed=config["split_seed"], max_samples=config["max_samples"],
                    validation_fraction=config["validation_fraction"], test_fraction=config["test_fraction"],
                )
                with suppress(ValueError):
                    manifest["dataset_file"] = path.relative_to(repo.root).as_posix()
                train_examples = [examples[i] for i in manifest["splits"]["train"]]
                validation_examples = [examples[i] for i in manifest["splits"]["validation"]]
                num_labels = len(manifest["label_ids"])
            if config["num_labels"] not in (0, num_labels):
                raise TrainError("num_labels does not match the selected dataset")
            if config["label_names"] is not None and len(config["label_names"]) != num_labels:
                raise TrainError("label_names does not match the dataset's class count")
            config["num_labels"] = num_labels
            set_seed(config["seed"], deterministic=config["deterministic"])
            model, tokenizer, task_type = load_model_and_tokenizer(model_id, revision, num_labels)
            targets = infer_lora_target_modules(model)
            model = get_peft_model(model, LoraConfig(
                task_type=task_type, r=config["lora_rank"], lora_alpha=config["lora_alpha"],
                lora_dropout=config["lora_dropout"], target_modules=targets,
            ))
            train_dataset = ExamplesDataset(tokenizer, train_examples, config["max_length"])
            args = TrainingArguments(
                output_dir=str(output / "trainer_output"),
                num_train_epochs=config["epochs"], learning_rate=config["learning_rate"],
                per_device_train_batch_size=config["batch_size"],
                gradient_accumulation_steps=config["gradient_accumulation_steps"],
                seed=config["seed"], data_seed=config["seed"],
                full_determinism=config["deterministic"], fp16=torch.cuda.is_available(),
                save_strategy="no", report_to="none", logging_steps=10,
                remove_unused_columns=False, dataloader_num_workers=0,
                dataloader_pin_memory=torch.cuda.is_available(),
            )
            trainer = Trainer(
                model=model, args=args, train_dataset=train_dataset,
                processing_class=tokenizer, data_collator=default_data_collator,
            )
            result = trainer.train()
            model.save_pretrained(output, safe_serialization=True)
            info = {
                **config, "model_id": model_id, "revision_sha": revision,
                "revision_hash": revision, "target_modules": targets,
                "dataset_file": manifest["dataset_file"] if manifest else "stub",
                "data_manifest": manifest, "stub": stub,
                "initialization": {"kind": "base", "revision_sha": revision},
                "metrics": result.metrics,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "environment": {
                    "python": platform.python_version(),
                    **{name: version(name) for name in ("torch", "transformers", "peft", "accelerate", "safetensors")},
                    "device": str(trainer.args.device), "precision": "mixed_float16" if args.fp16 else "float32",
                },
            }
            if validation_examples is not None:
                dataset = ExamplesDataset(tokenizer, validation_examples, config["max_length"])
                metrics = calculate_metrics(model, dataset, batch_size=config["eval_batch_size"])
                current = {
                    **metrics, "adapter_size_mb": adapter_size(output),
                    "adapter_sha256": hash_file(output / "adapter_model.safetensors"),
                    "adapter_config_sha256": hash_file(output / "adapter_config.json"),
                    "evaluation_spec": evaluation_spec(info),
                }
                info["evaluation"] = {"status": "measured", "current": current, "parent": None, "comparison": None}
                console.print(f"Validation accuracy: {metrics['accuracy']:.4f}; macro-F1: {metrics['macro_f1']:.4f}; samples: {metrics['sample_count']}")
            else:
                info["evaluation"] = {"status": "skipped", "reason": "synthetic training data", "current": None}
            atomic_write_text(output / "training_info.json", json.dumps(info, indent=2, allow_nan=False) + "\n")
            scratch = output / "trainer_output"
            if scratch.exists():
                shutil.rmtree(scratch)
        except (OSError, ValueError) as exc:
            raise TrainError(str(exc)) from exc
    return info


@handle_errors
def train(config: str, allow_stub: bool = False, force: bool = False):
    run_training(config, allow_stub=allow_stub, force=force)
    console.print("[green]Training completed. Adapter and run record staged in .aethel/workspace.[/green]")
