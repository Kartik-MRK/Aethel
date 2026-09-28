"""Load held-out examples from a verified training manifest."""

import json
from pathlib import Path

from transformers import AutoTokenizer

from .local_dataset import ExamplesDataset
from .protocol import evaluation_spec, load_split


def read_training_info(adapter_path: Path) -> dict:
    info = json.loads((Path(adapter_path) / "training_info.json").read_text(encoding="utf-8"))
    if not isinstance(info, dict):
        raise ValueError("training_info.json must contain an object")
    return info


def load_validation_dataset(repo, adapter_path: Path, *, split: str = "validation"):
    info = read_training_info(adapter_path)
    evaluation_spec(info, split)
    examples = load_split(repo.root, info["data_manifest"], split)
    tokenizer = AutoTokenizer.from_pretrained(
        info["model_id"], revision=info.get("revision_sha") or info.get("revision_hash")
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
    return ExamplesDataset(tokenizer, examples, info["max_length"])
