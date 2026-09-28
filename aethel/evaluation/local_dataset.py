"""Tokenized classification examples for training and evaluation."""

import torch
from torch.utils.data import Dataset


class ExamplesDataset(Dataset):
    def __init__(self, tokenizer, examples: list[dict], max_length: int = 128):
        self.tokenizer = tokenizer
        self.examples = examples
        self.max_length = max_length

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, index):
        example = self.examples[index]
        tokens = self.tokenizer(
            example["text"], truncation=True, padding="max_length",
            max_length=self.max_length, return_tensors="pt",
        )
        return {
            **{key: value.squeeze(0) for key, value in tokens.items()},
            "labels": torch.tensor(example["label"], dtype=torch.long),
        }
