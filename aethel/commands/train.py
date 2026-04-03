import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, Tuple

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


class StubTrainingDataset(Dataset):
    """Simple synthetic dataset to scaffold Trainer integration."""

    def __init__(self, tokenizer, task_type: str, size: int, max_length: int = 64):
        self.tokenizer = tokenizer
        self.task_type = task_type
        self.size = size
        self.max_length = max_length

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, index: int) -> Dict[str, torch.Tensor]:
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
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as e:
        raise TrainError("Repository config not found. Run 'aethel init' first.") from e
    except json.JSONDecodeError as e:
        raise TrainError(f"Repository config is invalid JSON: {e}") from e
    except OSError as e:
        raise TrainError(f"Could not read repository config: {e}") from e

    if not config.get("model_id"):
        raise TrainError("Config is missing model_id.")
    if not config.get("revision_hash"):
        raise TrainError("Config is missing revision_hash. Re-run 'aethel init --model <repo>'.")

    return config


def load_training_yaml(config_path: str) -> dict:
    if not os.path.isfile(config_path):
        raise TrainError(f"Training config file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
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
        }
    except (TypeError, ValueError) as e:
        raise TrainError(f"Training config contains invalid value types: {e}") from e

    for key in ["lora_rank", "lora_alpha", "batch_size", "epochs"]:
        if parsed[key] <= 0:
            raise TrainError(f"Training config value must be > 0: {key}")

    return parsed


def load_model_and_tokenizer(model_id: str, revision_hash: str) -> Tuple[Any, Any, str]:
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision_hash)
    except Exception as e:
        raise TrainError(f"Failed to load tokenizer for {model_id}@{revision_hash}: {e}") from e

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token

    model_kwargs: Dict[str, Any] = {"revision": revision_hash}
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
            num_labels=2,
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
        "q_proj",
        "v_proj",
        "k_proj",
        "o_proj",
        "c_attn",
        "query_key_value",
        "q_lin",
        "v_lin",
        "query",
        "value",
        "key",
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


def run_training(config_path: str) -> None:
    ensure_repo_exists()
    repo_config = load_repo_config()
    training_config = load_training_yaml(config_path)
    prepare_workspace()

    model_id = repo_config["model_id"]
    revision_hash = repo_config["revision_hash"]
    model, tokenizer, task_type = load_model_and_tokenizer(model_id, revision_hash)

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

    dataset_size = max(8, training_config["batch_size"] * 4)
    train_dataset = StubTrainingDataset(tokenizer, task_type=task_type, size=dataset_size)

    training_args = TrainingArguments(
        output_dir=os.path.join(WORKSPACE_DIR, "trainer_output"),
        num_train_epochs=float(training_config["epochs"]),
        per_device_train_batch_size=training_config["batch_size"],
        logging_steps=1,
        save_strategy="no",
        report_to="none",
        remove_unused_columns=False,
        fp16=torch.cuda.is_available(),
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        tokenizer=tokenizer,
        data_collator=default_data_collator,
    )

    train_result = trainer.train()
    model.save_pretrained(WORKSPACE_DIR)

    training_info = {
        "dataset": training_config["dataset"],
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


@app.callback(invoke_without_command=True)
def train_callback(
    ctx: typer.Context,
    config: str = typer.Option(..., "--config", "-c", help="Path to YAML training config."),
):
    """
    Run training using pinned model revision and save adapter artifacts to workspace.
    """
    if ctx.invoked_subcommand is None:
        train(config=config)


@app.command()
def train(
    config: str = typer.Option(..., "--config", "-c", help="Path to YAML training config.")
):
    """
    Run LoRA training from YAML configuration and write outputs into .aethel/workspace.
    """
    try:
        run_training(config)
    except TrainError as e:
        console.print(f"[bold red]Training failed: {e}[/bold red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[bold red]Unexpected training failure: {e}[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold green]Training completed.[/bold green]")
    console.print(f"[cyan]Workspace:[/cyan] {WORKSPACE_DIR}")
