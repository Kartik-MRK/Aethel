import csv

import torch
from torch.utils.data import Dataset


class LocalCSVDataset(Dataset):
    """Loads a CSV dataset for sequence classification."""

    def __init__(
        self,
        tokenizer,
        file_path: str,
        text_column: str = "text",
        label_column: str = "label",
        max_length: int = 128,
        max_samples: int = 0,
    ):
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
            raise FileNotFoundError(
                f"Dataset file not found: {file_path}"
            ) from e

        except KeyError as e:
            raise ValueError(
                f"Column {e} not found in CSV."
            ) from e

        if not self.texts:
            raise ValueError(
                f"Dataset at {file_path} is empty."
            )

        print(f"Loaded {len(self.texts)} samples from {file_path}")

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

        item = {
            key: value.squeeze(0)
            for key, value in tokenized.items()
        }

        # Sequence classification labels
        item["labels"] = torch.tensor(
            self.labels[index],
            dtype=torch.long,
        )

        return item

    def get_num_labels(self) -> int:
        return len(set(self.labels))