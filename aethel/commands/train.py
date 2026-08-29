"""LoRA fine-tuning, driven by a YAML config.

Loads a real CSV or JSONL dataset from a local path. A missing path is a hard
error, not a warning: training on synthetic filler would produce a committable
adapter with plausible metrics that had learned nothing. Pass --allow-stub to
ask for the synthetic dataset deliberately, and the commit is marked as such.
"""
import json
import os
import shutil
from datetime import datetime
from typing import Any

import torch
import typer
import yaml
from peft import LoraConfig, TaskType, get_peft_model
from rich.console import Console
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    default_data_collator,
)

console = Console()
app = typer.Typer()

AETHEL_DIR = ".aethel"
CONFIG_NAME = "config.json"
WORKSPACE_DIR = os.path.join(AETHEL_DIR, "workspace")
TRAINING_INFO_FILE = os.path.join(WORKSPACE_DIR, "training_info.json")


class TrainError(Exception):
    """Raised when training command fails."""


# ---------------------------------------------------------------------------
# Dataset classes
# ---------------------------------------------------------------------------

class LocalCSVDataset(Dataset):
    """Loads a real CSV dataset from disk for sequence classification."""

    def __init__(self, tokenizer, file_path: str, text_column: str,
                 label_column: str, max_length: int = 128, max_samples: int = 0):
        import csv

        self.tokenizer = tokenizer
        self.max_length = max_length
        self.texts: list[str] = []
        self.labels: list[int] = []

        try:
            with open(file_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if max_samples > 0 and i >= max_samples:
                        break
                    self.texts.append(str(row[text_column]))
                    self.labels.append(int(row[label_column]))
        except FileNotFoundError as e:
            raise TrainError(f"Dataset file not found: {file_path}") from e
        except KeyError as e:
            raise TrainError(f"Column {e} not found in CSV. Check text_column/label_column in YAML.") from e
        except (ValueError, OSError) as e:
            raise TrainError(f"Failed to load dataset: {e}") from e

        if not self.texts:
            raise TrainError(f"Dataset at {file_path} is empty or was fully filtered.")

        console.print(f"[cyan]Loaded {len(self.texts)} samples from {file_path}[/cyan]")

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        tokenized = self.tokenizer(
            self.texts[index],
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {k: v.squeeze(0) for k, v in tokenized.items()}
        item["labels"] = torch.tensor(self.labels[index], dtype=torch.long)
        return item

    def get_num_labels(self) -> int:
        return len(set(self.labels))


class StubTrainingDataset(Dataset):
    """Simple synthetic dataset fallback."""

    def __init__(self, tokenizer, task_type: str, size: int, max_length: int = 64):
        self.tokenizer = tokenizer
        self.task_type = task_type
        self.size = size
        self.max_length = max_length

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        text = f"sample training record {index}"
        tokenized = self.tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {k: v.squeeze(0) for k, v in tokenized.items()}

        if self.task_type == TaskType.CAUSAL_LM:
            item["labels"] = item["input_ids"].clone()
        else:
            item["labels"] = torch.tensor(index % 2, dtype=torch.long)

        return item


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def ensure_repo_exists() -> None:
    if not os.path.isdir(AETHEL_DIR):
        raise TrainError("Not an Aethel repository. Run 'aethel init' first.")


def prepare_workspace() -> None:
    try:
        if os.path.exists(WORKSPACE_DIR):
            shutil.rmtree(WORKSPACE_DIR)
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
    except OSError as e:
        raise TrainError(f"Failed to prepare workspace: {e}") from e


def load_repo_config() -> dict:
    config_path = os.path.join(AETHEL_DIR, CONFIG_NAME)
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as e:
        raise TrainError("Repository config not found. Run 'aethel init' first.") from e
    except json.JSONDecodeError as e:
        raise TrainError(f"Repository config is invalid JSON: {e}") from e
    except OSError as e:
        raise TrainError(f"Could not read repository config: {e}") from e

    if not config.get("model_id"):
        raise TrainError("Config is missing model_id.")

    # Schema 2 writes `revision_sha`; schema 1 wrote `revision_hash`. Accept
    # either and normalize, so a repository created before the rebuild still
    # trains without a re-init.
    revision = config.get("revision_sha") or config.get("revision_hash")
    if not revision:
        raise TrainError(
            "Config is missing revision_sha. Re-run 'aethel init --model <owner/model>'."
        )
    config["revision_sha"] = revision

    return config


def load_training_yaml(config_path: str) -> dict:
    if not os.path.isfile(config_path):
        raise TrainError(f"Training config file not found: {config_path}")

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise TrainError(f"Invalid YAML in training config: {e}") from e
    except OSError as e:
        raise TrainError(f"Could not read training config: {e}") from e

    required_fields = ["dataset", "lora_rank", "lora_alpha", "batch_size", "epochs"]
    missing = [field for field in required_fields if field not in data]
    if missing:
        raise TrainError(f"Missing required training config fields: {', '.join(missing)}")

    try:
        parsed = {
            "dataset": str(data["dataset"]),
            "lora_rank": int(data["lora_rank"]),
            "lora_alpha": int(data["lora_alpha"]),
            "batch_size": int(data["batch_size"]),
            "epochs": int(data["epochs"]),
            # Optional fields with sensible defaults
            "text_column": str(data.get("text_column", "text")),
            "label_column": str(data.get("label_column", "label")),
            "max_samples": int(data.get("max_samples", 0)),
            "num_labels": int(data.get("num_labels", 0)),
            "max_length": int(data.get("max_length", 128)),
            "gradient_accumulation_steps": int(data.get("gradient_accumulation_steps", 1)),
        }
    except (TypeError, ValueError) as e:
        raise TrainError(f"Training config contains invalid value types: {e}") from e

    for key in ["lora_rank", "lora_alpha", "batch_size", "epochs"]:
        if parsed[key] <= 0:
            raise TrainError(f"Training config value must be > 0: {key}")

    return parsed


def load_model_and_tokenizer(model_id: str, revision_hash: str,
                              num_labels: int = 2) -> tuple[Any, Any, str]:
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision_hash)
    except Exception as e:
        raise TrainError(f"Failed to load tokenizer for {model_id}@{revision_hash}: {e}") from e

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token

    model_kwargs: dict[str, Any] = {"revision": revision_hash}
    if torch.cuda.is_available():
        model_kwargs["torch_dtype"] = torch.float16
        model_kwargs["device_map"] = "auto"

    causal_error = None
    try:
        model = AutoModelForCausalLM.from_pretrained(model_id, **model_kwargs)
        return model, tokenizer, TaskType.CAUSAL_LM
    except Exception as e:
        causal_error = e

    try:
        model = AutoModelForSequenceClassification.from_pretrained(
            model_id,
            num_labels=num_labels,
            **model_kwargs,
        )
        return model, tokenizer, TaskType.SEQ_CLS
    except Exception as seq_error:
        raise TrainError(
            "Failed to load model for training. "
            f"CausalLM error: {causal_error}; SequenceClassification error: {seq_error}"
        ) from seq_error


def infer_lora_target_modules(model) -> list[str]:
    preferred = [
        "q_proj", "v_proj", "k_proj", "o_proj",
        "c_attn", "query_key_value",
        "q_lin", "v_lin",
        "query", "value", "key",
    ]
    linear_module_names: list[str] = []

    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear):
            suffix = name.split(".")[-1]
            if suffix not in linear_module_names:
                linear_module_names.append(suffix)

    selected = [name for name in preferred if name in linear_module_names]
    if selected:
        return selected[:8]

    if linear_module_names:
        return linear_module_names[:8]

    raise TrainError("Could not infer LoRA target modules from model architecture.")


def persist_training_info(training_info: dict) -> None:
    try:
        with open(TRAINING_INFO_FILE, "w", encoding="utf-8") as f:
            json.dump(training_info, f, indent=2)
    except OSError as e:
        raise TrainError(f"Failed to write training info: {e}") from e


def resolve_dataset_file(dataset_path: str) -> str:
    """Find the CSV file inside a dataset directory, or return the path if it is a file.

    Recognizes a HuggingFace `save_to_disk` (Arrow) directory and explains how
    to get a CSV, rather than reporting the misleading "No CSV dataset found".
    Arrow directories are easy to produce by accident, and the resulting error
    otherwise points at the wrong problem.
    """
    if os.path.isfile(dataset_path):
        return dataset_path

    if os.path.isdir(dataset_path):
        # Look for train.csv first, then any .csv
        train_csv = os.path.join(dataset_path, "train.csv")
        if os.path.isfile(train_csv):
            return train_csv
        for f in sorted(os.listdir(dataset_path)):
            if f.endswith(".csv"):
                return os.path.join(dataset_path, f)

        arrow_markers = {"dataset_dict.json", "dataset_info.json", "state.json"}
        entries = set(os.listdir(dataset_path))
        if arrow_markers & entries or any(e.endswith(".arrow") for e in entries):
            raise TrainError(
                f"'{dataset_path}' is a HuggingFace Arrow dataset (save_to_disk), "
                f"but the trainer reads CSV.\n"
                f"Convert it with:  python setup_demo_data.py\n"
                f"or export a CSV with columns matching text_column/label_column."
            )

    raise TrainError(f"No CSV dataset found at: {dataset_path}")


# ---------------------------------------------------------------------------
# Main training logic
# ---------------------------------------------------------------------------

def run_training(config_path: str, allow_stub: bool = False) -> None:
    ensure_repo_exists()
    repo_config = load_repo_config()
    training_config = load_training_yaml(config_path)
    prepare_workspace()

    model_id = repo_config["model_id"]
    revision_hash = repo_config["revision_sha"]

    dataset_path = training_config["dataset"]
    use_real_data = os.path.exists(dataset_path)

    # Determine num_labels before loading the model
    num_labels = training_config.get("num_labels", 0)
    train_dataset = None

    if use_real_data:
        csv_file = resolve_dataset_file(dataset_path)
        # Pre-load tokenizer just for dataset
        try:
            tokenizer_tmp = AutoTokenizer.from_pretrained(model_id, revision=revision_hash)
            if tokenizer_tmp.pad_token is None:
                tokenizer_tmp.pad_token = tokenizer_tmp.eos_token or tokenizer_tmp.unk_token
        except Exception as e:
            raise TrainError(f"Failed to load tokenizer: {e}") from e

        csv_dataset = LocalCSVDataset(
            tokenizer=tokenizer_tmp,
            file_path=csv_file,
            text_column=training_config["text_column"],
            label_column=training_config["label_column"],
            max_length=training_config["max_length"],
            max_samples=training_config["max_samples"],
        )
        if num_labels <= 0:
            num_labels = csv_dataset.get_num_labels()
        train_dataset = csv_dataset
        console.print(f"[green]Using real dataset: {csv_file} ({num_labels} labels)[/green]")
    elif not allow_stub:
        # Refuse rather than silently training on synthetic text. The old code
        # printed one yellow line and carried on, producing a fully committable
        # adapter with plausible-looking metrics that had learned nothing
        # (defect S2.2). A typo in a dataset path must not yield a publishable
        # model.
        raise TrainError(
            f"Dataset path '{dataset_path}' does not exist.\n"
            f"Check the 'dataset:' field in {config_path}.\n"
            f"To train on synthetic data deliberately, pass --allow-stub "
            f"(the commit will be marked stub-trained)."
        )
    else:
        console.print(
            f"[bold yellow]--allow-stub: '{dataset_path}' not found, "
            f"training on SYNTHETIC data. This model learns nothing real.[/bold yellow]"
        )

    if num_labels <= 0:
        num_labels = 2

    model, tokenizer, task_type = load_model_and_tokenizer(model_id, revision_hash, num_labels=num_labels)

    target_modules = infer_lora_target_modules(model)
    peft_config = LoraConfig(
        task_type=task_type,
        inference_mode=False,
        r=training_config["lora_rank"],
        lora_alpha=training_config["lora_alpha"],
        lora_dropout=0.05,
        target_modules=target_modules,
    )
    model = get_peft_model(model, peft_config)

    if train_dataset is None:
        dataset_size = max(8, training_config["batch_size"] * 4)
        train_dataset = StubTrainingDataset(tokenizer, task_type=task_type, size=dataset_size)

    training_args = TrainingArguments(
        output_dir=os.path.join(WORKSPACE_DIR, "trainer_output"),
        num_train_epochs=float(training_config["epochs"]),
        per_device_train_batch_size=training_config["batch_size"],
        gradient_accumulation_steps=training_config["gradient_accumulation_steps"],
        logging_steps=10,
        save_strategy="no",
        report_to="none",
        remove_unused_columns=False,
        fp16=torch.cuda.is_available(),
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        processing_class=tokenizer,
        data_collator=default_data_collator,
    )

    train_result = trainer.train()
    model.save_pretrained(WORKSPACE_DIR)

    training_info = {
        "dataset": training_config["dataset"],
        "dataset_file": csv_file if use_real_data else "stub",
        "num_labels": num_labels,
        "lora_rank": training_config["lora_rank"],
        "lora_alpha": training_config["lora_alpha"],
        "batch_size": training_config["batch_size"],
        "epochs": training_config["epochs"],
        "model_id": model_id,
        "revision_hash": revision_hash,
        "task_type": task_type,
        "target_modules": target_modules,
        "metrics": train_result.metrics,
        "timestamp": datetime.now().isoformat(),
    }
    persist_training_info(training_info)

    trainer_output = os.path.join(WORKSPACE_DIR, "trainer_output")
    if os.path.exists(trainer_output):
        shutil.rmtree(trainer_output, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def train_callback(
    ctx: typer.Context,
    config: str = typer.Option(..., "--config", "-c", help="Path to YAML training config."),
    allow_stub: bool = typer.Option(
        False, "--allow-stub", help="Train on synthetic data if the dataset is missing."
    ),
):
    """
    Run training using pinned model revision and save adapter artifacts to workspace.
    """
    if ctx.invoked_subcommand is None:
        train(config=config, allow_stub=allow_stub)


@app.command()
def train(
    config: str = typer.Option(..., "--config", "-c", help="Path to YAML training config."),
    allow_stub: bool = typer.Option(
        False, "--allow-stub", help="Train on synthetic data if the dataset is missing."
    ),
):
    """
    Run LoRA training from YAML configuration and write outputs into .aethel/workspace.
    """
    try:
        run_training(config, allow_stub=allow_stub)
    except TrainError as e:
        console.print(f"[bold red]Training failed: {e}[/bold red]")
        raise typer.Exit(code=1) from e

    console.print("[bold green]Training completed.[/bold green]")
    console.print(f"[cyan]Workspace:[/cyan] {WORKSPACE_DIR}")
