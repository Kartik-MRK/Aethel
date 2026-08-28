import json
from pathlib import Path

from transformers import AutoTokenizer

from .local_dataset import LocalCSVDataset


def load_validation_dataset(repo, adapter_path: Path):
    """
    Load the dataset configuration recorded during training
    and create the evaluation dataset.
    """

    training_info_path = adapter_path / "training_info.json"

    if not training_info_path.is_file():
        raise FileNotFoundError(
            f"training_info.json not found in {adapter_path}"
        )

    with open(training_info_path, encoding="utf-8") as f:
        training_info = json.load(f)

    dataset_file = training_info.get("dataset_file")

    if not dataset_file or dataset_file == "stub":
        raise ValueError(
            "No real dataset is recorded in training_info.json"
        )

    dataset_path = Path(dataset_file)

    if not dataset_path.is_absolute():
        dataset_path = repo.root / dataset_path

    if not dataset_path.is_file():
        raise FileNotFoundError(
            f"Dataset file not found: {dataset_path}"
        )

    # Use recorded training configuration when available.
    text_column = training_info.get("text_column", "text")
    label_column = training_info.get("label_column", "label")
    max_length = int(training_info.get("max_length", 128))
    max_samples = int(training_info.get("max_samples", 0))

    model_id = training_info["model_id"]
    revision_hash = training_info["revision_hash"]

    tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        revision=revision_hash,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = (
            tokenizer.eos_token or tokenizer.unk_token
        )

    return LocalCSVDataset(
        tokenizer=tokenizer,
        file_path=str(dataset_path),
        text_column=text_column,
        label_column=label_column,
        max_length=max_length,
        max_samples=max_samples,
    )