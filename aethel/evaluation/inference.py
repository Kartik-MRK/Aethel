import json
from pathlib import Path

from peft import PeftModel
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
)


def load_model_for_evaluation(adapter_path: Path):
    """
    Load the base sequence-classification model and attach
    the trained LoRA adapter.
    """

    training_info_path = adapter_path / "training_info.json"

    if not training_info_path.is_file():
        raise FileNotFoundError(
            f"training_info.json not found in {adapter_path}"
        )

    with open(training_info_path, encoding="utf-8") as f:
        training_info = json.load(f)

    model_id = training_info["model_id"]
    revision_hash = training_info["revision_hash"]
    num_labels = int(training_info.get("num_labels", 2))

    tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        revision=revision_hash,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = (
            tokenizer.eos_token or tokenizer.unk_token
        )

    model = AutoModelForSequenceClassification.from_pretrained(
        model_id,
        revision=revision_hash,
        num_labels=num_labels,
    )

    model = PeftModel.from_pretrained(
        model,
        str(adapter_path),
    )

    model.eval()

    return model, tokenizer